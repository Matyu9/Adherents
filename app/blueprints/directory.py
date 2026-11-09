"""Annuaire : personnes, foyers, adhésions, saisons, coordonnées bancaires et rattachement des comptes.

Toutes ses pages contiennent des données personnelles : le navigateur ne les garde pas (Cache-Control: no-store).
"""
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import DateField, EmailField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError

from ..extensions import db
from ..models import (Account, Activity, BankAccount, Household, HouseholdMember, Membership, Person, Phone, Season,
                      SepaMandate)
from ..permissions import can, require
from ..services import accounts as accounts_service
from ..services import bank, directory, guard, throttle
from ..services import totp as totp_service
from ..services.activity import diff, log
from ..services.directory import DirectoryError

bp = Blueprint("directory", __name__, url_prefix="/annuaire")

PER_PAGE = 100
EMAIL = Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Adresse email invalide.")
_bank_reveal_failures = throttle.Limiter("bank-reveal", 5, 15 * 60)


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def _text(value):
    """Valeur d'une liste déroulante : une colonne vide (None) correspond au choix « — »."""
    return "" if value is None else str(value)


def _blank(value):
    """Champ texte vide → None, pour ne pas stocker de chaînes vides."""
    value = _strip(value)
    return value or None


# ---------------------------------------------------------------------------
# Formulaires
# ---------------------------------------------------------------------------

class AddressForm(FlaskForm):
    address_recipient = StringField("Destinataire (s'il diffère)", validators=[Optional(), Length(max=120)])
    address_street = TextAreaField("Adresse", validators=[Optional(), Length(max=500)])
    postal_code = StringField("Code postal", validators=[Optional(), Length(max=10)])
    city = StringField("Ville", validators=[Optional(), Length(max=80)])
    country = StringField("Pays (code ISO)", default="FR",
                          validators=[DataRequired(), Regexp(r"^[A-Za-z]{2}$", message="Deux lettres : FR, BE…")])


class PersonForm(AddressForm):
    civility = SelectField("Civilité", default="", coerce=_text, validate_choice=False, choices=[("", "—"), *((c, c) for c in Person.CIVILITIES)])
    first_name = StringField("Prénom", validators=[DataRequired(), Length(max=80)])
    last_name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    birth_name = StringField("Nom de naissance", validators=[Optional(), Length(max=80)])
    sex = SelectField("Sexe", default="", coerce=_text, validate_choice=False, choices=[("", "—"), *Person.SEXES.items()])
    birth_date = DateField("Date de naissance", validators=[Optional()])
    birth_place = StringField("Lieu de naissance", validators=[Optional(), Length(max=120)])
    email = EmailField("Email personnel", validators=[Optional(), Length(max=255), EMAIL], filters=[_strip])
    emergency_contact = StringField("Contact en cas d'urgence", validators=[Optional(), Length(max=255)])
    notes = TextAreaField("Observations internes", validators=[Optional(), Length(max=5000)])

    def __init__(self, *args, person=None, households=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._person = person
        self._households = households or []

    def validate_email(self, field):
        if not field.data:
            return
        probe = self._person or Person(first_name="", last_name="")
        parent = directory.guardian_email_conflict(probe, field.data, households=self._households)
        if parent is not None:
            raise ValidationError(f"C'est l'adresse de {parent.full_name}, responsable du foyer : celle d'un parent "
                                  "n'est jamais recopiée sur la fiche de son enfant.")

    def values(self):
        data = {name: _blank(getattr(self, name).data) for name in directory.PERSON_FIELDS + directory.ADDRESS_FIELDS}
        data["country"] = (data["country"] or "FR").upper()
        # Listes sans contrôle de WTForms (un champ absent du formulaire vaut « — ») : on ne garde qu'un choix connu.
        data["civility"] = data["civility"] if data["civility"] in Person.CIVILITIES else None
        data["sex"] = data["sex"] if data["sex"] in Person.SEXES else None
        return data


class HouseholdForm(AddressForm):
    name = StringField("Nom du foyer", validators=[DataRequired(), Length(max=120)])
    notes = TextAreaField("Observations internes", validators=[Optional(), Length(max=5000)])

    def values(self):
        data = {name: _blank(getattr(self, name).data) for name in ("name", "notes") + directory.ADDRESS_FIELDS}
        data["country"] = (data["country"] or "FR").upper()
        return data


class PhoneForm(FlaskForm):
    number = StringField("Numéro", validators=[DataRequired(), Length(max=30)])
    label = StringField("Libellé", validators=[Optional(), Length(max=40)])


class MembershipForm(FlaskForm):
    season_id = SelectField("Saison", coerce=int)
    status = SelectField("Statut", choices=list(Membership.STATUSES.items()))
    withdrawn_on = DateField("Date de l'abandon", validators=[Optional()])
    category = StringField("Catégorie", validators=[Optional(), Length(max=40)])
    school_level = StringField("Niveau scolaire", validators=[Optional(), Length(max=40)])
    school_class = StringField("Classe", validators=[Optional(), Length(max=40)])

    @property
    def withdrawn_date(self):
        """La date d'abandon ne compte que pour un abandon : masquée sinon, on ne garde pas ce qui y traînerait."""
        return self.withdrawn_on.data if self.status.data == Membership.WITHDRAWN else None


class SeasonForm(FlaskForm):
    label = StringField("Saison", validators=[DataRequired(), Regexp(r"^\d{4}-\d{4}$", message="Par exemple 2026-2027.")],
                        filters=[_strip])
    starts_on = DateField("Début", validators=[DataRequired()])
    ends_on = DateField("Fin", validators=[DataRequired()])

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def validate_label(self, field):
        existing = db.session.scalar(db.select(Season).filter_by(label=field.data))
        if existing is not None and existing is not self._current:
            raise ValidationError("Cette saison existe déjà.")

    def validate_ends_on(self, field):
        if field.data and self.starts_on.data and field.data <= self.starts_on.data:
            raise ValidationError("La fin doit suivre le début.")


class BankAccountForm(FlaskForm):
    holder_person_id = SelectField("Titulaire dans l'annuaire", coerce=int, default=0)
    holder_name = StringField("Titulaire, tel qu'écrit sur le RIB", validators=[DataRequired(), Length(max=120)])
    iban = StringField("IBAN", validators=[DataRequired(), Length(max=50)])
    bic = StringField("BIC", validators=[Optional(), Length(max=15)])

    def validate_iban(self, field):
        try:
            field.data = bank.normalize_iban(field.data)
        except bank.InvalidIban as exc:
            raise ValidationError(str(exc)) from None

    def validate_bic(self, field):
        try:
            field.data = bank.normalize_bic(field.data)
        except bank.InvalidIban as exc:
            raise ValidationError(str(exc)) from None


class MandateForm(FlaskForm):
    rum = StringField("Référence unique du mandat (RUM)", validators=[
        DataRequired(), Length(max=35), Regexp(r"^[A-Za-z0-9+?/:().,' -]+$", message="Lettres, chiffres et + ? / - : ( ) . , ' seulement.")],
        filters=[_strip])
    signed_on = DateField("Signé le", validators=[DataRequired()])
    first_debit_on = DateField("Premier prélèvement", validators=[Optional()])
    last_debit_on = DateField("Dernier prélèvement", validators=[Optional()])
    revoked_on = DateField("Révoqué le", validators=[Optional()])

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def validate_rum(self, field):
        existing = db.session.scalar(db.select(SepaMandate).filter_by(rum=field.data))
        if existing is not None and existing is not self._current:
            raise ValidationError("Cette référence est déjà celle d'un autre mandat.")


class CodeForm(FlaskForm):
    code = StringField("Code de double authentification", validators=[DataRequired(), Length(max=12)])


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _person(person_id):
    return db.session.get(Person, person_id) or abort(404)


def _household(household_id):
    return db.session.get(Household, household_id) or abort(404)


def _member(household, person_id):
    return next((m for m in household.members if m.person_id == person_id), None) or abort(404)


def _history(**where):
    column = Activity.person_id if "person" in where else Activity.household_id
    return db.session.scalars(db.select(Activity).where(column == next(iter(where.values())).id)
                              .order_by(Activity.created_at.desc(), Activity.id.desc()).limit(30)).all()


def _page(query, order):
    total = db.session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    page = max(request.args.get("page", 1, type=int), 1)
    items = db.session.scalars(query.order_by(*order).limit(PER_PAGE).offset((page - 1) * PER_PAGE)).all()
    return items, total, page, max((total + PER_PAGE - 1) // PER_PAGE, 1)


@bp.before_request
def count_reads():
    """Une page lue compte pour la limite de lecture (services/guard.py). Les écritures ont leurs propres droits."""
    from flask import g

    if request.method == "GET" and current_user.is_authenticated:
        g.write_lock = True  # cette lecture écrit un compteur
        guard.count_read()


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.app_template_filter("phone")
def phone_filter(number):
    return directory.display_phone(number)


# ---------------------------------------------------------------------------
# Personnes
# ---------------------------------------------------------------------------

@bp.route("")
@require("directory.view")
def index():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("statut")
    status = status if status in directory.STATUSES else None
    all_seasons = directory.seasons()
    season = db.session.get(Season, request.args.get("saison", type=int) or 0) or directory.current_season()
    items, total, page, pages = _page(directory.person_query(q, status, season),
                                      (Person.last_name, Person.first_name, Person.id))
    args = {k: v for k, v in (("q", q), ("statut", status),
                              ("saison", season.id if season and request.args.get("saison") else None)) if v}
    return render_template("directory/index.html", items=items, total=total, page=page, pages=pages, args=args, q=q,
                           status=status, statuses=directory.STATUSES, season=season, seasons=all_seasons,
                           current=directory.current_season())


@bp.route("/personnes/nouvelle", methods=["GET", "POST"])
@require("directory.edit")
def create_person():
    household = db.session.get(Household, request.args.get("foyer", type=int) or 0)
    role = request.args.get("role") if request.args.get("role") in HouseholdMember.ROLES else HouseholdMember.DEPENDENT
    form = PersonForm(households=[household] if household and role == HouseholdMember.DEPENDENT else None,
                      data={"last_name": household.name.removeprefix("Famille ") if household else None})
    if form.validate_on_submit():
        person = Person(**form.values())
        db.session.add(person)
        db.session.flush()
        log("person.create", f"Fiche créée : {person.full_name}", person=person)
        if household is not None:
            try:
                directory.add_member(household, person, role)
            except DirectoryError as exc:
                db.session.rollback()
                flash(str(exc), "error")
                return render_template("directory/person_form.html", form=form, person=None, household=household)
            log("household.member_add", f"{person.full_name} ajouté(e) au foyer {household.name}",
                household=household, person=person)
        db.session.commit()
        flash("Fiche créée.", "success")
        if household is not None:
            return redirect(url_for(".household", household_id=household.id))
        return redirect(url_for(".person", person_id=person.id))
    return render_template("directory/person_form.html", form=form, person=None, household=household)


@bp.route("/personnes/<int:person_id>")
@require("directory.view")
def person(person_id):
    person = _person(person_id)
    can_account = can(current_user, "account.manage") and can(current_user, "directory.edit")
    return render_template("directory/person.html", person=person, phone_form=PhoneForm(),
                           entries=_history(person=person), current=directory.current_season(),
                           can_account=can_account,
                           season_count=len(directory.seasons()),
                           account_problem=directory.account_problems(person) if can_account else None,
                           needs_consent=directory.needs_guardian_consent(person))


@bp.route("/personnes/<int:person_id>/modifier", methods=["GET", "POST"])
@require("directory.edit")
def edit_person(person_id):
    person = _person(person_id)
    form = PersonForm(obj=person, person=person)
    if form.validate_on_submit():
        changes = diff(person, directory.PERSON_FIELDS + directory.ADDRESS_FIELDS, form.values())
        if changes:
            log("person.update", f"Fiche modifiée : {person.full_name}", changes=changes, person=person)
        db.session.commit()
        flash("Modifications enregistrées.", "success")
        return redirect(url_for(".person", person_id=person.id))
    return render_template("directory/person_form.html", form=form, person=person, household=None)


@bp.route("/personnes/<int:person_id>/archiver", methods=["POST"])
@require("directory.edit")
def archive_person(person_id):
    person = _person(person_id)
    directory.archive(person)
    log("person.archive", f"Fiche archivée : {person.full_name}", person=person)
    db.session.commit()
    flash("Fiche archivée : elle n'apparaît plus dans la liste, mais rien n'est effacé.", "success")
    return redirect(url_for(".person", person_id=person.id))


@bp.route("/personnes/<int:person_id>/restaurer", methods=["POST"])
@require("directory.edit")
def restore_person(person_id):
    person = _person(person_id)
    directory.restore(person)
    log("person.restore", f"Fiche restaurée : {person.full_name}", person=person)
    db.session.commit()
    flash("Fiche restaurée.", "success")
    return redirect(url_for(".person", person_id=person.id))


@bp.route("/personnes/<int:person_id>/telephones", methods=["POST"])
@require("directory.edit")
def add_person_phone(person_id):
    person = _person(person_id)
    return _add_phone(person, PhoneForm(), url_for(".person", person_id=person.id), person=person)


@bp.route("/foyers/<int:household_id>/telephones", methods=["POST"])
@require("directory.edit")
def add_household_phone(household_id):
    household = _household(household_id)
    return _add_phone(household, PhoneForm(), url_for(".household", household_id=household.id), household=household)


def _add_phone(owner, form, back, **where):
    if form.validate_on_submit():
        try:
            phone = directory.add_phone(owner, form.number.data, form.label.data)
        except DirectoryError as exc:
            flash(str(exc), "error")
        else:
            log("phone.add", f"Téléphone ajouté : {directory.display_phone(phone.number)}", **where)
            db.session.commit()
            flash("Téléphone ajouté.", "success")
    else:
        flash("Indiquez un numéro.", "error")
    return redirect(back + "#telephones")


@bp.route("/telephones/<int:phone_id>/supprimer", methods=["POST"])
@require("directory.edit")
def delete_phone(phone_id):
    phone = db.session.get(Phone, phone_id) or abort(404)
    owner = {"person": phone.person} if phone.person_id else {"household": phone.household}
    back = (url_for(".person", person_id=phone.person_id) if phone.person_id
            else url_for(".household", household_id=phone.household_id))
    log("phone.delete", f"Téléphone retiré : {directory.display_phone(phone.number)}", **owner)
    db.session.delete(phone)
    db.session.commit()
    flash("Téléphone retiré.", "success")
    return redirect(back + "#telephones")


# --- Adhésions -----------------------------------------------------------

def _membership_form(person, membership=None):
    form = MembershipForm(obj=membership)
    taken = {m.season_id for m in person.memberships if m is not membership}
    form.season_id.choices = [(s.id, s.label) for s in directory.seasons() if s.id not in taken]
    if membership is None and not form.is_submitted() and (current := directory.current_season()):
        form.season_id.data = current.id
    return form


@bp.route("/personnes/<int:person_id>/adhesions/nouvelle", methods=["GET", "POST"])
@require("directory.edit")
def create_membership(person_id):
    person = _person(person_id)
    form = _membership_form(person)
    if not form.season_id.choices:
        flash("Aucune saison disponible : créez d'abord la saison." if not directory.seasons()
              else "Cette personne a déjà une adhésion sur chaque saison.", "warning")
        return redirect(url_for(".person", person_id=person.id))
    if form.validate_on_submit():
        membership = Membership(person=person, season_id=form.season_id.data, status=form.status.data,
                                withdrawn_on=form.withdrawn_date, category=_blank(form.category.data),
                                school_level=_blank(form.school_level.data), school_class=_blank(form.school_class.data))
        db.session.add(membership)
        db.session.flush()
        log("membership.create", f"Adhésion {membership.season.label} ajoutée à {person.full_name}", person=person)
        db.session.commit()
        flash("Adhésion enregistrée.", "success")
        return redirect(url_for(".person", person_id=person.id) + "#adhesions")
    return render_template("directory/membership_form.html", form=form, person=person, membership=None)


@bp.route("/adhesions/<int:membership_id>/modifier", methods=["GET", "POST"])
@require("directory.edit")
def edit_membership(membership_id):
    membership = db.session.get(Membership, membership_id) or abort(404)
    person = membership.person
    form = _membership_form(person, membership)
    if form.validate_on_submit():
        changes = diff(membership, ("season_id", "status", "withdrawn_on", "category", "school_level", "school_class"),
                       {"season_id": form.season_id.data, "status": form.status.data,
                        "withdrawn_on": form.withdrawn_date,
                        "category": _blank(form.category.data), "school_level": _blank(form.school_level.data),
                        "school_class": _blank(form.school_class.data)})
        if changes:
            db.session.flush()
            log("membership.update", f"Adhésion {membership.season.label} de {person.full_name} modifiée",
                changes=changes, person=person)
        db.session.commit()
        flash("Adhésion enregistrée.", "success")
        return redirect(url_for(".person", person_id=person.id) + "#adhesions")
    return render_template("directory/membership_form.html", form=form, person=person, membership=membership)


# --- Fusion ----------------------------------------------------------------

def _shown(field, value):
    """Valeur lisible d'un champ comparé à la fusion."""
    if value is None:
        return ""
    if field == "adresse":
        recipient, street, postal_code, city, country = value
        parts = [recipient, (street or "").replace("\n", ", "), " ".join(p for p in (postal_code, city) if p)]
        return ", ".join(p for p in parts if p) + (f" ({country})" if country and country != "FR" else "")
    if field == "sex":
        return Person.SEXES.get(value, value)
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _merge_rows(keep, other, fields):
    rows = []
    for field, kept, theirs, conflict in directory.merge_plan(keep, other, fields):
        rows.append({"field": field, "label": fields[field], "kept": _shown(field, kept),
                     "other": _shown(field, theirs), "conflict": conflict,
                     "result": _shown(field, kept if kept is not None else theirs)})
    return rows


def _person_merge_problems(keep, other):
    """En plus des règles de l'annuaire : déplacer un compte de connexion d'une fiche à l'autre change ce qu'il
    ouvrira (l'espace adhérent de la fiche gardée). C'est une décision sur les comptes, pas seulement sur l'annuaire."""
    problems = directory.merge_problems(keep, other)
    if (keep.account or other.account) and not can(current_user, "account.manage"):
        problems.append("Une des fiches a un compte de connexion : la fusion demande aussi le droit de gérer les "
                        "comptes.")
    return problems


def _taken(fields):
    """Champs en conflit pour lesquels on a choisi la valeur de l'autre fiche."""
    return {field for field in fields if request.form.get(f"prendre_{field}") == "autre"}


@bp.route("/personnes/<int:person_id>/fusionner", methods=["GET", "POST"])
@require("directory.merge")
def merge_person(person_id):
    keep = _person(person_id)
    other = db.session.get(Person, request.values.get("avec", type=int) or 0)
    if request.method == "POST":
        if other is None:
            abort(400)
        name = other.full_name
        if problems := _person_merge_problems(keep, other):
            flash(" ".join(problems), "error")
            return redirect(url_for(".merge_person", person_id=keep.id, avec=other.id))
        try:
            summary = directory.merge_persons(keep, other, _taken(directory.PERSON_MERGE_FIELDS))
        except DirectoryError as exc:
            flash(str(exc), "error")
            return redirect(url_for(".merge_person", person_id=keep.id, avec=other.id))
        log("person.merge", f"Fiche de {name} fusionnée dans celle de {keep.full_name}"
            + (f" ({' ; '.join(summary)})" if summary else ""), person=keep)
        db.session.commit()
        flash(f"Fiches fusionnées : celle de {name} n'existe plus.", "success")
        return redirect(url_for(".person", person_id=keep.id))
    q = (request.args.get("q") or "").strip()
    candidates = []
    if other is None:
        query = directory.search(db.select(Person), q or keep.last_name).where(Person.id != keep.id)
        candidates = db.session.scalars(query.order_by(Person.last_name, Person.first_name).limit(30)).all()
    fields = {k: v for k, v in directory.PERSON_MERGE_FIELDS.items()
              if k != "birth_place" or can(current_user, "directory.edit")}
    return render_template("directory/person_merge.html", keep=keep, other=other, q=q, candidates=candidates,
                           rows=_merge_rows(keep, other, fields) if other else [],
                           problems=_person_merge_problems(keep, other) if other else [])


# --- Compte de connexion ---------------------------------------------------

@bp.route("/personnes/<int:person_id>/compte", methods=["POST"])
@require("account.manage")
@require("directory.edit")
def create_account(person_id):
    person = _person(person_id)
    consent = request.form.get("accord") == "1"
    try:
        account = directory.create_account(person, guardian_consent=consent)
    except DirectoryError as exc:
        flash(str(exc), "error")
        return redirect(url_for(".person", person_id=person.id) + "#compte")
    log("account.create", f"Compte créé depuis l'annuaire : {account.full_name} ({account.role_label})"
        + (", avec l'accord d'un responsable légal (mineur)" if directory.needs_guardian_consent(person) else ""),
        target=account, person=person)
    from .accounts import _grant_access
    return _grant_access(account)


@bp.route("/personnes/<int:person_id>/compte/detacher", methods=["POST"])
@require("account.manage")
@require("directory.edit")
def unlink_account(person_id):
    person = _person(person_id)
    if account := directory.unlink(person):
        log("account.unlink", f"Compte {account.email} détaché de la fiche de {person.full_name}", target=account,
            person=person)
        db.session.commit()
        flash("Compte détaché : il existe toujours, sans fiche.", "success")
    return redirect(url_for(".person", person_id=person.id))


# ---------------------------------------------------------------------------
# Foyers
# ---------------------------------------------------------------------------

@bp.route("/foyers")
@require("directory.view")
def households():
    q = (request.args.get("q") or "").strip()
    archived = request.args.get("archives") == "1"
    items, total, page, pages = _page(directory.household_query(q, archived), (Household.name, Household.id))
    args = {k: v for k, v in (("q", q), ("archives", "1" if archived else None)) if v}
    return render_template("directory/households.html", items=items, total=total, page=page, pages=pages, args=args,
                           q=q, archived=archived)


@bp.route("/foyers/nouveau", methods=["GET", "POST"])
@require("directory.edit")
def create_household():
    founder = db.session.get(Person, request.args.get("personne", type=int) or 0)
    form = HouseholdForm(data={"name": directory.default_household_name(founder)} if founder else None)
    if form.validate_on_submit():
        household = Household(**form.values())
        db.session.add(household)
        db.session.flush()
        log("household.create", f"Foyer créé : {household.name}", household=household)
        if founder is not None:
            directory.add_member(household, founder, HouseholdMember.GUARDIAN)
            log("household.member_add", f"{founder.full_name} ajouté(e) au foyer {household.name}",
                household=household, person=founder)
        db.session.commit()
        flash("Foyer créé.", "success")
        return redirect(url_for(".household", household_id=household.id))
    return render_template("directory/household_form.html", form=form, household=None, founder=founder)


@bp.route("/foyers/<int:household_id>")
@require("directory.view")
def household(household_id):
    household = _household(household_id)
    q = (request.args.get("q") or "").strip()
    found = []
    if q and can(current_user, "directory.edit"):
        present = [m.person_id for m in household.members]
        query = directory.search(db.select(Person), q).where(Person.archived_at.is_(None), Person.id.not_in(present))
        found = db.session.scalars(query.order_by(Person.last_name, Person.first_name).limit(20)).all()
    # Même IBAN sur d'autres foyers : affiché en permanence, pas seulement au moment de l'ajout.
    same_iban = {}
    if can(current_user, "bank.view"):
        for account in household.bank_accounts:
            same_iban[account.id] = db.session.scalars(
                db.select(Household).join(BankAccount).where(BankAccount.iban_fingerprint == account.iban_fingerprint,
                                                             BankAccount.id != account.id)
                .order_by(Household.name).distinct()).all()
    return render_template("directory/household.html", household=household, phone_form=PhoneForm(), q=q, found=found,
                           same_iban=same_iban,
                           secure_left=guard.remaining() if can(current_user, "bank.view") else 0,
                           entries=_history(household=household), roles=HouseholdMember.ROLES,
                           bank_enabled=bank.enabled())


@bp.route("/foyers/<int:household_id>/modifier", methods=["GET", "POST"])
@require("directory.edit")
def edit_household(household_id):
    household = _household(household_id)
    form = HouseholdForm(obj=household)
    if form.validate_on_submit():
        changes = diff(household, ("name", "notes") + directory.ADDRESS_FIELDS, form.values())
        if changes:
            log("household.update", f"Foyer modifié : {household.name}", changes=changes, household=household)
        db.session.commit()
        flash("Modifications enregistrées.", "success")
        return redirect(url_for(".household", household_id=household.id))
    return render_template("directory/household_form.html", form=form, household=household, founder=None)


@bp.route("/foyers/<int:household_id>/archiver", methods=["POST"])
@require("directory.edit")
def archive_household(household_id):
    household = _household(household_id)
    directory.archive(household)
    log("household.archive", f"Foyer archivé : {household.name}", household=household)
    db.session.commit()
    flash("Foyer archivé.", "success")
    return redirect(url_for(".household", household_id=household.id))


@bp.route("/foyers/<int:household_id>/restaurer", methods=["POST"])
@require("directory.edit")
def restore_household(household_id):
    household = _household(household_id)
    directory.restore(household)
    log("household.restore", f"Foyer restauré : {household.name}", household=household)
    db.session.commit()
    flash("Foyer restauré.", "success")
    return redirect(url_for(".household", household_id=household.id))


@bp.route("/foyers/<int:household_id>/membres", methods=["POST"])
@require("directory.edit")
def add_member(household_id):
    household = _household(household_id)
    person = db.session.get(Person, request.form.get("person_id", type=int) or 0) or abort(400)
    try:
        member = directory.add_member(household, person, request.form.get("role"), request.form.get("relationship"))
    except DirectoryError as exc:
        flash(str(exc), "error")
    else:
        log("household.member_add", f"{person.full_name} ajouté(e) au foyer {household.name} ({member.label})",
            household=household, person=person)
        db.session.commit()
        flash(f"{person.full_name} fait maintenant partie du foyer.", "success")
    return redirect(url_for(".household", household_id=household.id) + "#membres")


@bp.route("/foyers/<int:household_id>/membres/<int:person_id>/modifier", methods=["POST"])
@require("directory.edit")
def update_member(household_id, person_id):
    household = _household(household_id)
    member = _member(household, person_id)
    try:
        directory.change_role(member, request.form.get("role", member.role))
    except DirectoryError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for(".household", household_id=household.id) + "#membres")
    member.relationship = _blank(request.form.get("relationship"))
    log("household.member_update", f"{member.person.full_name} dans le foyer {household.name} : {member.label}"
        + (f", {member.relationship}" if member.relationship else ""), household=household, person=member.person)
    db.session.commit()
    flash("Membre modifié.", "success")
    return redirect(url_for(".household", household_id=household.id) + "#membres")


@bp.route("/foyers/<int:household_id>/membres/<int:person_id>/retirer", methods=["POST"])
@require("directory.edit")
def remove_member(household_id, person_id):
    household = _household(household_id)
    member = _member(household, person_id)
    person = member.person
    household.members.remove(member)
    log("household.member_remove", f"{person.full_name} retiré(e) du foyer {household.name}", household=household,
        person=person)
    db.session.commit()
    flash(f"{person.full_name} ne fait plus partie du foyer. Sa fiche est conservée.", "success")
    return redirect(url_for(".household", household_id=household.id) + "#membres")


@bp.route("/foyers/<int:household_id>/responsables/echanger", methods=["POST"])
@require("directory.edit")
def swap_guardians(household_id):
    household = _household(household_id)
    directory.swap_guardians(household)
    log("household.swap", f"Responsables 1 et 2 échangés dans le foyer {household.name}", household=household)
    db.session.commit()
    flash("Responsables échangés.", "success")
    return redirect(url_for(".household", household_id=household.id) + "#membres")


@bp.route("/foyers/<int:household_id>/fusionner", methods=["GET", "POST"])
@require("directory.merge")
def merge_household(household_id):
    keep = _household(household_id)
    other = db.session.get(Household, request.values.get("avec", type=int) or 0)
    if request.method == "POST":
        if other is None:
            abort(400)
        name = other.name
        try:
            summary = directory.merge_households(keep, other, _taken(directory.HOUSEHOLD_MERGE_FIELDS))
        except (DirectoryError, bank.BankKeyError) as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for(".merge_household", household_id=keep.id, avec=other.id))
        log("household.merge", f"Foyer {name} fusionné dans {keep.name}"
            + (f" ({' ; '.join(summary)})" if summary else ""), household=keep)
        db.session.commit()
        flash(f"Foyers fusionnés : « {name} » n'existe plus.", "success")
        return redirect(url_for(".household", household_id=keep.id))
    q = (request.args.get("q") or "").strip()
    candidates = []
    if other is None:
        query = directory.household_query(q or keep.name.split()[-1]).where(Household.id != keep.id)
        candidates = db.session.scalars(query.order_by(Household.name).limit(30)).all()
    return render_template("directory/household_merge.html", keep=keep, other=other, q=q, candidates=candidates,
                           rows=_merge_rows(keep, other, directory.HOUSEHOLD_MERGE_FIELDS) if other else [],
                           problems=directory.household_merge_problems(keep, other) if other else [])


# ---------------------------------------------------------------------------
# Coordonnées bancaires
# ---------------------------------------------------------------------------

def _bank_account(account_id):
    return db.session.get(BankAccount, account_id) or abort(404)


def _safe_next(path):
    """Adresse de retour après le mode sécurisé : une page de l'annuaire, jamais un autre site."""
    path = path or ""
    if path.startswith("/annuaire/") and "//" not in path and "\\" not in path and "\n" not in path:
        return path
    return url_for(".index")


def _secure(view):
    """Opération bancaire : il faut être en mode sécurisé (code TOTP récent), sinon on passe par sa page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if guard.elevated():
            return view(*args, **kwargs)
        if request.method == "GET":
            return redirect(url_for(".elevate", suite=request.full_path.rstrip("?")))
        flash("Confirmez votre identité, puis recommencez l'opération.", "warning")
        referrer = urlsplit(request.referrer or "")
        back = referrer.path if referrer.netloc == request.host else None
        return redirect(url_for(".elevate", suite=_safe_next(back)))
    return wrapped


@bp.route("/mode-securise", methods=["GET", "POST"])
@require("bank.view")
def elevate():
    """Saisie d'un code TOTP : ouvre le mode sécurisé pour quelques minutes, sur cette session seulement."""
    target = _safe_next(request.values.get("suite"))
    if not current_user.totp_enabled:
        flash("Activez d'abord la double authentification dans Mon compte : elle est exigée pour les données "
              "bancaires.", "error")
        return redirect(url_for("account.totp"))
    if guard.elevated():
        return redirect(target)
    form = CodeForm()
    if form.validate_on_submit():
        keys = (f"account:{current_user.id}",)
        if _bank_reveal_failures.blocked(keys) or totp_service.failures.blocked(keys):
            form.code.errors = ["Trop de tentatives : réessayez dans quelques minutes."]
        elif totp_service.verify_code(current_user, form.code.data):
            totp_service.failures.clear(keys)
            guard.elevate()
            log("auth.elevate", f"Mode sécurisé ouvert par {current_user.full_name}")
            db.session.commit()
            return redirect(target)
        else:
            _bank_reveal_failures.hit(keys)
            totp_service.failures.hit(keys)
            log("auth.elevate_failed", f"Code refusé pour le mode sécurisé ({current_user.full_name})")
            db.session.commit()
            form.code.errors = ["Code incorrect."]
    return render_template("directory/elevate.html", form=form, target=target,
                           minutes=current_app.config.get("ELEVATION_MINUTES", 10))


@bp.route("/mode-securise/quitter", methods=["POST"])
@login_required
def leave_secure():
    guard.drop_elevation()
    flash("Mode sécurisé fermé.", "success")
    return redirect(_safe_next(request.values.get("suite")))


@bp.route("/foyers/<int:household_id>/banque/nouveau", methods=["GET", "POST"])
@require("bank.view")
@_secure
def create_bank_account(household_id):
    household = _household(household_id)
    if not bank.enabled():
        flash("BANK_DATA_KEYS n'est pas définie : impossible d'enregistrer un IBAN (voir la documentation).", "error")
        return redirect(url_for(".household", household_id=household.id))
    form = BankAccountForm()
    form.holder_person_id.choices = [(0, "—"), *((m.person_id, m.person.full_name) for m in household.members)]
    if not form.is_submitted() and household.guardians:
        form.holder_person_id.data = household.guardians[0].person_id
        form.holder_name.data = household.guardians[0].person.full_name
    if form.validate_on_submit():
        account = BankAccount(household_id=household.id, holder_name=form.holder_name.data.strip(), bic=form.bic.data,
                              holder_person_id=form.holder_person_id.data or None)
        bank.set_iban(account, form.iban.data)
        others = bank.same_iban(form.iban.data)
        db.session.add(account)
        db.session.flush()
        log("bank.create", f"Coordonnées bancaires ajoutées au foyer {household.name} : {account.masked_iban}",
            household=household)
        db.session.commit()
        flash("Coordonnées bancaires enregistrées.", "success")
        for other in others:
            flash(f"Le même IBAN est déjà enregistré pour le foyer {other.household.name}.", "warning")
        return redirect(url_for(".household", household_id=household.id) + "#banque")
    return render_template("directory/bank_form.html", form=form, household=household)


@bp.route("/banque/<int:account_id>/afficher")
@require("bank.view")
@_secure
def reveal_iban(account_id):
    """L'IBAN en clair, en mode sécurisé. Chaque lecture est écrite au journal."""
    account = _bank_account(account_id)
    household = account.household
    try:
        iban = bank.group(bank.reveal(account))
    except bank.BankKeyError as exc:
        flash(str(exc), "error")
        iban = None
    log("bank.view", f"IBAN {account.masked_iban} du foyer {household.name} affiché", household=household)
    db.session.commit()
    return render_template("directory/bank_reveal.html", account=account, household=household, iban=iban)


@bp.route("/banque/<int:account_id>/supprimer", methods=["POST"])
@require("bank.view")
@_secure
def delete_bank_account(account_id):
    account = _bank_account(account_id)
    household = account.household
    log("bank.delete", f"Coordonnées bancaires {account.masked_iban} retirées du foyer {household.name}",
        household=household)
    db.session.delete(account)
    db.session.commit()
    flash("Coordonnées bancaires supprimées, avec leurs mandats.", "success")
    return redirect(url_for(".household", household_id=household.id) + "#banque")


MANDATE_FIELDS = ("rum", "signed_on", "first_debit_on", "last_debit_on", "revoked_on")


@bp.route("/banque/<int:account_id>/mandats/nouveau", methods=["GET", "POST"])
@require("bank.view")
@_secure
def create_mandate(account_id):
    account = _bank_account(account_id)
    form = MandateForm(data=None if request.method == "POST" else {"rum": bank.new_rum()})
    if form.validate_on_submit():
        mandate = SepaMandate(bank_account=account, **{f: getattr(form, f).data for f in MANDATE_FIELDS})
        db.session.add(mandate)
        log("mandate.create", f"Mandat {mandate.rum} ajouté ({account.masked_iban}, foyer {account.household.name})",
            household=account.household)
        db.session.commit()
        flash("Mandat enregistré.", "success")
        return redirect(url_for(".household", household_id=account.household_id) + "#banque")
    return render_template("directory/mandate_form.html", form=form, account=account, mandate=None)


@bp.route("/mandats/<int:mandate_id>/modifier", methods=["GET", "POST"])
@require("bank.view")
@_secure
def edit_mandate(mandate_id):
    mandate = db.session.get(SepaMandate, mandate_id) or abort(404)
    account = mandate.bank_account
    form = MandateForm(obj=mandate, current=mandate)
    if form.validate_on_submit():
        changes = diff(mandate, MANDATE_FIELDS, {f: getattr(form, f).data for f in MANDATE_FIELDS})
        if changes:
            log("mandate.update", f"Mandat {mandate.rum} modifié", changes=changes, household=account.household)
        db.session.commit()
        flash("Mandat enregistré.", "success")
        return redirect(url_for(".household", household_id=account.household_id) + "#banque")
    return render_template("directory/mandate_form.html", form=form, account=account, mandate=mandate)


# ---------------------------------------------------------------------------
# Saisons
# ---------------------------------------------------------------------------

@bp.route("/saisons")
@require("directory.edit")
def seasons():
    counts = dict(db.session.execute(db.select(Membership.season_id, db.func.count(Membership.id))
                                     .group_by(Membership.season_id)).all())
    return render_template("directory/seasons.html", seasons=directory.seasons(), counts=counts,
                           current=directory.current_season())


@bp.route("/saisons/nouvelle", methods=["GET", "POST"])
@require("directory.edit")
def create_season():
    form = SeasonForm()
    if form.validate_on_submit():
        season = Season(label=form.label.data, starts_on=form.starts_on.data, ends_on=form.ends_on.data)
        db.session.add(season)
        db.session.flush()
        if directory.current_season() is None:
            directory.set_current_season(season)
        log("season.create", f"Saison créée : {season.label}")
        db.session.commit()
        flash("Saison créée.", "success")
        return redirect(url_for(".seasons"))
    return render_template("directory/season_form.html", form=form, season=None)


@bp.route("/saisons/<int:season_id>/modifier", methods=["GET", "POST"])
@require("directory.edit")
def edit_season(season_id):
    season = db.session.get(Season, season_id) or abort(404)
    form = SeasonForm(obj=season, current=season)
    if form.validate_on_submit():
        changes = diff(season, ("label", "starts_on", "ends_on"),
                       {"label": form.label.data, "starts_on": form.starts_on.data, "ends_on": form.ends_on.data})
        if changes:
            log("season.update", f"Saison modifiée : {season.label}", changes=changes)
        db.session.commit()
        flash("Saison enregistrée.", "success")
        return redirect(url_for(".seasons"))
    return render_template("directory/season_form.html", form=form, season=season)


@bp.route("/saisons/<int:season_id>/en-cours", methods=["POST"])
@require("directory.edit")
def set_current_season(season_id):
    season = db.session.get(Season, season_id) or abort(404)
    directory.set_current_season(season)
    log("season.current", f"Saison en cours : {season.label}")
    db.session.commit()
    flash(f"La saison en cours est maintenant {season.label}.", "success")
    return redirect(url_for(".seasons"))


@bp.route("/saisons/<int:season_id>/supprimer", methods=["POST"])
@require("directory.edit")
def delete_season(season_id):
    season = db.session.get(Season, season_id) or abort(404)
    if db.session.scalar(db.select(Membership.id).filter_by(season_id=season.id).limit(1)) is not None:
        flash("Cette saison a des adhésions : elle ne peut pas être supprimée.", "error")
    elif directory.current_season() is season:
        flash("C'est la saison en cours : choisissez-en une autre d'abord.", "error")
    else:
        log("season.delete", f"Saison supprimée : {season.label}")
        db.session.delete(season)
        db.session.commit()
        flash("Saison supprimée.", "success")
    return redirect(url_for(".seasons"))


# ---------------------------------------------------------------------------
# Rattachement des comptes à leur fiche
# ---------------------------------------------------------------------------

@bp.route("/rattachement")
@require("account.manage")
@require("directory.edit")
def link_accounts():
    rows = [(account, directory.suggestions(account)) for account in directory.unlinked_accounts()]
    return render_template("directory/link_accounts.html", rows=rows, status_of=accounts_service.status_of,
                           statuses=accounts_service.STATUSES)


@bp.route("/rattachement/<int:account_id>", methods=["POST"])
@require("account.manage")
@require("directory.edit")
def link_account(account_id):
    account = db.session.get(Account, account_id) or abort(404)
    person = db.session.get(Person, request.form.get("person_id", type=int) or 0) or abort(400)
    try:
        directory.link(account, person)
    except DirectoryError as exc:
        flash(str(exc), "error")
    else:
        log("account.link", f"Compte {account.email} rattaché à la fiche de {person.full_name}", target=account,
            person=person)
        db.session.commit()
        flash(f"Compte de {account.full_name} rattaché à sa fiche.", "success")
    return redirect(request.form.get("next") == "fiche" and url_for(".person", person_id=person.id)
                    or url_for(".link_accounts"))


@bp.route("/rattachement/<int:account_id>/fiche", methods=["POST"])
@require("account.manage")
@require("directory.edit")
def person_from_account(account_id):
    account = db.session.get(Account, account_id) or abort(404)
    try:
        person = directory.person_from_account(account)
    except DirectoryError as exc:
        flash(str(exc), "error")
        return redirect(url_for(".link_accounts"))
    db.session.flush()
    log("person.create", f"Fiche créée depuis le compte {account.email}", target=account, person=person)
    db.session.commit()
    flash(f"Fiche de {person.full_name} créée et rattachée à son compte.", "success")
    return redirect(url_for(".person", person_id=person.id))

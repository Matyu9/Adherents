"""Inscriptions, côté famille : le dossier pré-rempli d'un foyer, et le formulaire public des nouvelles familles.

Un responsable du foyer ouvre le dossier depuis son compte. Une nouvelle famille, sans compte, l'ouvre par un lien
reçu par email : le dossier est alors noté dans sa session, pour la durée du lien.
"""
import uuid
from datetime import datetime
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import (BooleanField, DateField, EmailField, SelectField, SelectMultipleField, StringField,
                     TextAreaField)
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError
from wtforms.widgets import CheckboxInput, ListWidget

from ..extensions import db
from ..models import Campaign, CampaignConsent, Dossier, Person
from ..services import bank, directory, mailer, registration, throttle
from ..services.activity import log
from ..services.bank import InvalidIban
from ..services.directory import DirectoryError
from ..services.registration import RegistrationError
from .directory import EMAIL, _blank, _strip, _text

bp = Blueprint("enrollment", __name__, url_prefix="/inscription")

ACCESS_KEY = "dossier_access"
_requests = throttle.Limiter("new-family", 5, 60 * 60)


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _access():
    return session.get(ACCESS_KEY, [])


def _with_dossier(view):
    """Charge le dossier `uuid` si la personne peut l'ouvrir ; sinon 404, sans dire s'il existe."""
    @wraps(view)
    def wrapped(uuid, *args, **kwargs):
        dossier = db.session.scalar(db.select(Dossier).filter_by(uuid=uuid))
        if dossier is None or not registration.can_open(current_user, dossier, _access()):
            if not current_user.is_authenticated and dossier is not None:
                return redirect(url_for("auth.login", next=request.path))
            abort(404)
        g.dossier = dossier
        return view(dossier, *args, **kwargs)
    return wrapped


def _editable(dossier):
    """Modifiable par la famille : brouillon ou à corriger, et campagne ouverte."""
    return dossier.editable and dossier.campaign.is_open()


def _refuse_if_locked(dossier):
    if not _editable(dossier):
        flash("Ce dossier ne se modifie plus." if not dossier.editable else "La campagne est fermée.", "error")
        return redirect(url_for(".dossier", uuid=dossier.uuid))
    return None


def _data(dossier):
    """Copie de travail du JSON : on la modifie, puis `registration.save` la remet en place."""
    import copy
    return copy.deepcopy(dossier.data)


# ---------------------------------------------------------------------------
# Accueil : campagnes ouvertes et dossiers de mes foyers
# ---------------------------------------------------------------------------

def my_dossiers(account):
    """[(campagne, foyer, dossier ou None)] pour les campagnes ouvertes, plus les dossiers que j'ai créés."""
    rows = []
    households = registration.guarded_households(account)
    for campaign in registration.open_campaigns():
        for household in households:
            dossier = db.session.scalar(db.select(Dossier).filter_by(campaign_id=campaign.id, household_id=household.id))
            rows.append((campaign, household, dossier))
    seen = {d.id for _, _, d in rows if d}
    mine = db.session.scalars(db.select(Dossier).where(Dossier.account_id == account.id)).all()
    rows += [(d.campaign, d.household, d) for d in mine if d.id not in seen and d.household not in households]
    return rows


@bp.route("")
@login_required
def index():
    return render_template("enrollment/index.html", rows=my_dossiers(current_user),
                           has_household=bool(registration.guarded_households(current_user)))


@bp.route("/<int:campaign_id>/foyer/<int:household_id>", methods=["POST"])
@login_required
def start(campaign_id, household_id):
    campaign = db.session.get(Campaign, campaign_id) or abort(404)
    household = next((h for h in registration.guarded_households(current_user) if h.id == household_id), None)
    if household is None:
        abort(404)
    if not campaign.is_open():
        flash("Cette campagne n'est pas ouverte.", "error")
        return redirect(url_for(".index"))
    dossier = registration.dossier_for(campaign, household, current_user)
    db.session.commit()
    return redirect(url_for(".dossier", uuid=dossier.uuid))


# ---------------------------------------------------------------------------
# Nouvelles familles
# ---------------------------------------------------------------------------

class NewFamilyForm(FlaskForm):
    email = EmailField("Votre adresse email", validators=[DataRequired(), Length(max=255), EMAIL], filters=[_strip])


@bp.route("/nouvelle/<int:campaign_id>", methods=["GET", "POST"])
def new_family(campaign_id):
    campaign = db.session.get(Campaign, campaign_id)
    if campaign is None or not campaign.allow_new:
        abort(404)
    form = NewFamilyForm()
    if campaign.is_open() and mailer.smtp_enabled() and form.validate_on_submit():
        email = form.email.data.strip().lower()
        keys = (f"email:{email}", f"ip:{request.remote_addr}")
        if _requests.blocked(keys):
            flash("Trop de demandes : réessayez dans une heure.", "error")
            return render_template("enrollment/new_family.html", form=form, campaign=campaign,
                                   smtp=mailer.smtp_enabled(), now=datetime.now()), 429
        _requests.hit(keys)
        dossier = registration.pending_new_dossier(campaign, email)
        if dossier is None:
            dossier, raw = registration.new_family_dossier(campaign, email)
            log("dossier.new_family", f"Dossier ouvert pour une nouvelle famille ({campaign.title})")
        else:
            raw = registration.renew_access(dossier)
        db.session.commit()
        if raw:
            mailer.send_in_background(email, *mailer.render(
                None, f"Votre dossier d'inscription : {campaign.title}",
                f"Voici le lien de votre dossier « {campaign.title} ». Il reste valable "
                f"{registration.ACCESS_TTL.days} jours : gardez cet email pour revenir compléter le dossier.\n\n"
                "Si vous n'avez rien demandé, ignorez ce message.", "Ouvrir mon dossier", registration.access_url(raw)))
        # Même réponse dans tous les cas : la page ne dit pas si un dossier existe déjà pour cette adresse.
        return render_template("enrollment/link_sent.html", email=email, campaign=campaign)
    return render_template("enrollment/new_family.html", form=form, campaign=campaign, smtp=mailer.smtp_enabled(),
                           now=datetime.now())


@bp.route("/acces/<token>")
def access(token):
    dossier = registration.find_by_access(token)
    if dossier is None:
        flash("Ce lien n'est plus valable. Demandez-en un nouveau avec votre adresse email.", "error")
        return redirect(url_for("auth.login"))
    if dossier.email_verified_at is None:
        dossier.email_verified_at = datetime.now()
        db.session.commit()
    session[ACCESS_KEY] = [*[u for u in _access() if u != dossier.uuid], dossier.uuid][-5:]
    return redirect(url_for(".dossier", uuid=dossier.uuid))


# ---------------------------------------------------------------------------
# Le dossier
# ---------------------------------------------------------------------------

@bp.route("/dossier/<uuid>")
@_with_dossier
def dossier(dossier):
    courses, counts = registration.offered_courses(dossier.campaign)
    return render_template("enrollment/dossier.html", dossier=dossier, editable=_editable(dossier),
                           problems=registration.problems(dossier) if dossier.editable else [],
                           courses={c.id: c for c in courses}, counts=counts, registration=registration,
                           send_form=SendForm())


def _guardian_fields(prefix, required):
    """Champs d'un responsable, préfixés g1_ ou g2_ dans HouseholdForm."""
    req = [DataRequired()] if required else [Optional()]
    return {
        f"{prefix}civility": SelectField("Civilité", default="", coerce=_text, validate_choice=False,
                                         choices=[("", "—"), *((c, c) for c in Person.CIVILITIES)]),
        f"{prefix}first_name": StringField("Prénom", validators=[*req, Length(max=80)], filters=[_strip]),
        f"{prefix}last_name": StringField("Nom", validators=[*req, Length(max=80)], filters=[_strip]),
        f"{prefix}email": EmailField("Email", validators=[Optional(), Length(max=255), EMAIL], filters=[_strip]),
        f"{prefix}phone": StringField("Téléphone", validators=[Optional(), Length(max=30)]),
        f"{prefix}relationship": StringField("Lien avec les élèves", validators=[Optional(), Length(max=40)],
                                             filters=[_strip], description="Mère, père, tuteur, lui-même…"),
    }


class HouseholdForm(FlaskForm):
    name = StringField("Nom du foyer", validators=[DataRequired(), Length(max=120)], filters=[_strip],
                       description="Par exemple « Famille Martin ».")
    address_recipient = StringField("Destinataire (s'il diffère)", validators=[Optional(), Length(max=120)])
    address_street = TextAreaField("Adresse", validators=[DataRequired(), Length(max=500)])
    postal_code = StringField("Code postal", validators=[DataRequired(), Length(max=10)])
    city = StringField("Ville", validators=[DataRequired(), Length(max=80)])
    country = StringField("Pays (code ISO)", default="FR",
                          validators=[DataRequired(), Regexp(r"^[A-Za-z]{2}$", message="Deux lettres : FR, BE…")])
    phone = StringField("Téléphone du domicile", validators=[Optional(), Length(max=30)])

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        for name in ("phone", "g1_phone", "g2_phone"):
            field = getattr(self, name)
            if field.data:
                try:
                    field.data = directory.normalize_phone(field.data)
                except DirectoryError as exc:
                    field.errors.append(str(exc))
                    ok = False
        if any(getattr(self, f"g2_{f}").data for f in ("first_name", "last_name", "email", "phone")):
            for f in ("first_name", "last_name"):
                if not getattr(self, f"g2_{f}").data:
                    getattr(self, f"g2_{f}").errors.append("Obligatoire pour un second responsable.")
                    ok = False
        if not self.g1_email.data and not self.g2_email.data:
            self.g1_email.errors.append("Indiquez au moins une adresse email.")
            ok = False
        return ok


for _name, _field in {**_guardian_fields("g1_", True), **_guardian_fields("g2_", False)}.items():
    setattr(HouseholdForm, _name, _field)  # WTForms les ajoute au formulaire comme s'ils étaient déclarés dedans


@bp.route("/dossier/<uuid>/foyer", methods=["GET", "POST"])
@_with_dossier
def household(dossier):
    if locked := _refuse_if_locked(dossier):
        return locked
    data = _data(dossier)
    form = HouseholdForm()
    if request.method == "GET":
        for f in ("name", "address_recipient", "address_street", "postal_code", "city", "country", "phone"):
            getattr(form, f).data = data["household"].get(f) if f != "phone" else \
                directory.display_phone(data["household"].get(f))
        form.country.data = form.country.data or "FR"
        for index, g_ in enumerate(data["guardians"][:2], start=1):
            for f in ("civility", "first_name", "last_name", "email", "relationship"):
                getattr(form, f"g{index}_{f}").data = g_.get(f) or ""
            getattr(form, f"g{index}_phone").data = directory.display_phone(g_.get("phone"))
        if not data["guardians"] and dossier.email:
            form.g1_email.data = dossier.email
    if form.validate_on_submit():
        data["household"] = {f: _blank(getattr(form, f).data) for f in
                             ("name", "address_recipient", "address_street", "postal_code", "city", "phone")}
        data["household"]["country"] = form.country.data.upper()
        guardians = []
        for index in (1, 2):
            values = {f: _blank(getattr(form, f"g{index}_{f}").data) for f in
                      ("civility", "first_name", "last_name", "email", "phone", "relationship")}
            if not values["first_name"]:
                continue
            values["email"] = values["email"].lower() if values["email"] else None
            old = data["guardians"][index - 1] if len(data["guardians"]) >= index else {}
            guardians.append({"person_id": old.get("person_id"), **values})
        # Un élève qui était le responsable 2 retiré n'a plus de fiche : il redevient un élève à part.
        for student in data["students"]:
            if student.get("guardian") is not None and student["guardian"] >= len(guardians):
                student["guardian"] = None
        data["guardians"] = guardians
        registration.save(dossier, data)
        db.session.commit()
        flash("Foyer enregistré.", "success")
        return redirect(url_for(".dossier", uuid=dossier.uuid))
    return render_template("enrollment/household.html", form=form, dossier=dossier)


class StudentForm(FlaskForm):
    enrolled = BooleanField("Inscrire cet élève cette saison")
    first_name = StringField("Prénom", validators=[Optional(), Length(max=80)], filters=[_strip])
    last_name = StringField("Nom", validators=[Optional(), Length(max=80)], filters=[_strip])
    birth_date = DateField("Date de naissance", validators=[Optional()])
    sex = SelectField("Sexe", default="", coerce=_text, validate_choice=False,
                      choices=[("", "—"), *Person.SEXES.items()])
    category = SelectField("Catégorie", default="", coerce=_text, validate_choice=False,
                           choices=[("", "—"), ("Scolaire", "Scolaire"), ("Non scolaire", "Non scolaire"),
                                    ("Adulte", "Adulte")])
    school_level = StringField("Niveau scolaire", validators=[Optional(), Length(max=40)], filters=[_strip],
                               description="Primaire, collège, lycée, études…")
    school_class = StringField("Classe", validators=[Optional(), Length(max=40)], filters=[_strip])
    courses = SelectMultipleField("Cours", coerce=int, widget=ListWidget(prefix_label=False),
                                  option_widget=CheckboxInput(), validate_choice=False)
    wishes = TextAreaField("Souhaits, contraintes d'horaire", validators=[Optional(), Length(max=2000)])

    def __init__(self, *args, is_guardian=False, full=(), offered=(), already=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._is_guardian, self._full, self._offered, self._already = is_guardian, set(full), set(offered), set(already)

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if not self.enrolled.data:
            return ok
        if not self._is_guardian:
            for f in ("first_name", "last_name", "birth_date"):
                if not getattr(self, f).data:
                    getattr(self, f).errors.append("Obligatoire pour inscrire l'élève.")
                    ok = False
        chosen = set(self.courses.data or [])
        if chosen - self._offered:
            self.courses.errors.append("Un cours choisi n'est pas proposé.")
            ok = False
        if full := chosen & self._full - self._already:
            self.courses.errors.append("Un cours choisi est complet : choisissez-en un autre, ou indiquez votre "
                                       "souhait plus bas." if len(full) == 1 else "Des cours choisis sont complets.")
            ok = False
        return ok


def _student(data, key):
    student = next((s for s in data["students"] if s.get("key") == key), None)
    if student is None:
        abort(404)
    return student


def _student_consents(dossier):
    return [c for c in dossier.campaign.consents if c.scope == CampaignConsent.STUDENT]


@bp.route("/dossier/<uuid>/eleves/<key>", methods=["GET", "POST"])
@_with_dossier
def student(dossier, key):
    if locked := _refuse_if_locked(dossier):
        return locked
    data = _data(dossier)
    s = _student(data, key)
    courses, counts = registration.offered_courses(dossier.campaign)
    full = [c.id for c in courses if c.is_full(counts.get(c.id, 0))]
    is_guardian = s.get("guardian") is not None
    form = StudentForm(is_guardian=is_guardian, full=full, offered=[c.id for c in courses],
                       already=s.get("courses", []))
    form.courses.choices = [(c.id, c.title) for c in courses]
    if request.method == "GET":
        form.enrolled.data = bool(s.get("enrolled"))
        for f in ("first_name", "last_name", "sex", "category", "school_level", "school_class", "wishes"):
            getattr(form, f).data = s.get(f) or ""
        form.birth_date.data = datetime.fromisoformat(s["birth_date"]).date() if s.get("birth_date") else None
        form.courses.data = s.get("courses", [])
    if form.validate_on_submit():
        s["enrolled"] = form.enrolled.data
        if not is_guardian:
            s.update({f: _blank(getattr(form, f).data) for f in ("first_name", "last_name", "sex")})
            s["birth_date"] = form.birth_date.data.isoformat() if form.birth_date.data else None
        s.update({f: _blank(getattr(form, f).data) for f in ("category", "school_level", "school_class", "wishes")})
        s["courses"] = [c for c in form.courses.data or []]
        s["consents"] = {str(c.id): request.form.get(f"consent-{c.id}") == "y" for c in _student_consents(dossier)}
        registration.save(dossier, data)
        db.session.commit()
        flash("Élève enregistré.", "success")
        return redirect(url_for(".dossier", uuid=dossier.uuid))
    name = registration.student_name(s, data) or "Nouvel élève"
    return render_template("enrollment/student.html", form=form, dossier=dossier, student=s, name=name,
                           is_guardian=is_guardian, courses=courses, counts=counts, full=set(full),
                           consents=_student_consents(dossier))


@bp.route("/dossier/<uuid>/eleves/nouveau", methods=["POST"])
@_with_dossier
def add_student(dossier):
    if locked := _refuse_if_locked(dossier):
        return locked
    data = _data(dossier)
    who = request.form.get("qui")
    if who in ("0", "1") and int(who) < len(data["guardians"]):
        if any(s.get("guardian") == int(who) for s in data["students"]):
            flash("Ce responsable est déjà dans la liste des élèves.", "error")
            return redirect(url_for(".dossier", uuid=dossier.uuid))
        guardian = data["guardians"][int(who)]
        entry = {"guardian": int(who), "person_id": guardian.get("person_id"), "category": "Adulte"}
    else:
        entry = {"guardian": None, "person_id": None,
                 "last_name": (data["guardians"][0].get("last_name") if data["guardians"] else None)}
    key = uuid.uuid4().hex[:8]
    data["students"].append({"key": key, "enrolled": True, "courses": [], "consents": {}, **entry})
    registration.save(dossier, data)
    db.session.commit()
    return redirect(url_for(".student", uuid=dossier.uuid, key=key))


@bp.route("/dossier/<uuid>/eleves/<key>/retirer", methods=["POST"])
@_with_dossier
def remove_student(dossier, key):
    """Seul un élève ajouté dans ce dossier se retire ; un élève de l'annuaire se décoche (« ne se réinscrit pas »)."""
    if locked := _refuse_if_locked(dossier):
        return locked
    data = _data(dossier)
    s = _student(data, key)
    if s.get("person_id") and s.get("guardian") is None:
        flash("Cet élève est déjà connu : décochez plutôt « Inscrire cet élève cette saison ».", "error")
    else:
        data["students"].remove(s)
        registration.save(dossier, data)
        db.session.commit()
        flash("Élève retiré du dossier.", "success")
    return redirect(url_for(".dossier", uuid=dossier.uuid))


@bp.route("/dossier/<uuid>/consentements", methods=["GET", "POST"])
@_with_dossier
def consents(dossier):
    if locked := _refuse_if_locked(dossier):
        return locked
    items = [c for c in dossier.campaign.consents if c.scope == CampaignConsent.HOUSEHOLD]
    if request.method == "POST":
        data = _data(dossier)
        data["consents"] = {str(c.id): request.form.get(f"consent-{c.id}") == "y" for c in items}
        registration.save(dossier, data)
        db.session.commit()
        flash("Réponses enregistrées.", "success")
        return redirect(url_for(".dossier", uuid=dossier.uuid))
    return render_template("enrollment/consents.html", dossier=dossier, items=items)


class BankForm(FlaskForm):
    holder_name = StringField("Titulaire du compte", validators=[DataRequired(), Length(max=120)], filters=[_strip])
    iban = StringField("IBAN", validators=[DataRequired(), Length(max=50)])
    bic = StringField("BIC (facultatif)", validators=[Optional(), Length(max=11)])

    def validate_iban(self, field):
        try:
            field.data = bank.normalize_iban(field.data)
        except InvalidIban as exc:
            raise ValidationError(str(exc)) from None

    def validate_bic(self, field):
        try:
            field.data = bank.normalize_bic(field.data)
        except InvalidIban as exc:
            raise ValidationError(str(exc)) from None


@bp.route("/dossier/<uuid>/banque", methods=["GET", "POST"])
@_with_dossier
def bank_details(dossier):
    """Saisie de l'IBAN dans un champ, contrôlé (clé modulo 97) et chiffré aussitôt. Jamais réaffiché en clair."""
    if locked := _refuse_if_locked(dossier):
        return locked
    form = BankForm()
    if request.method == "GET":
        form.holder_name.data = dossier.data.get("bank", {}).get("holder_name")
    if form.validate_on_submit():
        if not bank.enabled():
            flash("Les coordonnées bancaires ne peuvent pas être enregistrées pour le moment : contactez le "
                  "secrétariat.", "error")
            return redirect(url_for(".dossier", uuid=dossier.uuid))
        data = _data(dossier)
        data["bank"] = {"holder_name": form.holder_name.data, "bic": form.bic.data}
        registration.save(dossier, data)
        bank.set_dossier_iban(dossier, form.iban.data)
        log("dossier.iban", "IBAN saisi dans un dossier d'inscription", user=current_user if current_user.is_authenticated
            else None, household=dossier.household)
        db.session.commit()
        flash(f"Coordonnées bancaires enregistrées (•••• {dossier.iban_last4}).", "success")
        return redirect(url_for(".dossier", uuid=dossier.uuid))
    return render_template("enrollment/bank.html", form=form, dossier=dossier)


@bp.route("/dossier/<uuid>/banque/retirer", methods=["POST"])
@_with_dossier
def remove_bank_details(dossier):
    if locked := _refuse_if_locked(dossier):
        return locked
    bank.clear_dossier_iban(dossier)
    data = _data(dossier)
    data["bank"] = {}
    registration.save(dossier, data)
    db.session.commit()
    flash("Coordonnées bancaires retirées du dossier.", "success")
    return redirect(url_for(".dossier", uuid=dossier.uuid))


class SendForm(FlaskForm):
    comment = TextAreaField("Un message pour le secrétariat (facultatif)", validators=[Optional(), Length(max=2000)])
    certify = BooleanField("Je certifie l'exactitude de ces informations",
                           validators=[DataRequired("Cochez cette case pour envoyer le dossier.")])


@bp.route("/dossier/<uuid>/envoyer", methods=["POST"])
@_with_dossier
def send(dossier):
    if locked := _refuse_if_locked(dossier):
        return locked
    form = SendForm()
    if not form.validate_on_submit():
        flash(" ".join(e for errors in form.errors.values() for e in errors), "error")
        return redirect(url_for(".dossier", uuid=dossier.uuid))
    data = _data(dossier)
    data["comment"] = _blank(form.comment.data)
    data["answered_at"] = datetime.now().isoformat(timespec="seconds")
    guardian = data["guardians"][0] if data["guardians"] else {}
    data["answered_by"] = current_user.full_name if current_user.is_authenticated else \
        f"{guardian.get('first_name') or ''} {guardian.get('last_name') or ''}".strip() or dossier.email
    registration.save(dossier, data)
    try:
        registration.submit(dossier, current_user)
    except RegistrationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for(".dossier", uuid=dossier.uuid))
    log("dossier.submit", f"Dossier envoyé ({dossier.campaign.title})",
        user=current_user if current_user.is_authenticated else None, household=dossier.household)
    db.session.commit()
    flash("Dossier envoyé. Le secrétariat va le vérifier ; vous serez prévenu(e) de sa validation.", "success")
    return redirect(url_for(".dossier", uuid=dossier.uuid))


# ---------------------------------------------------------------------------
# Professeurs : mes élèves
# ---------------------------------------------------------------------------

def teacher_of(account):
    person = getattr(account, "person", None)
    return person.teacher if person is not None else None


@bp.route("/mes-eleves")
@login_required
def my_students():
    """Un professeur voit ses élèves et leurs responsables, et rien d'autre de l'annuaire."""
    from flask import g as flask_g

    from ..services import guard
    teacher = teacher_of(current_user)
    if teacher is None:
        abort(404)
    flask_g.write_lock = True
    guard.count_read()
    seasons = directory.seasons()
    wanted = request.args.get("saison", type=int)
    season = next((s for s in seasons if s.id == wanted), None) or directory.current_season() or \
        (seasons[0] if seasons else None)
    rows = registration.students_of(teacher, season) if season else []
    return render_template("enrollment/my_students.html", season=season, seasons=seasons, rows=rows)

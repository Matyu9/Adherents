"""Inscriptions, côté gestion : campagnes, revue des dossiers, validation, report vers le logiciel de gestion.

Les dossiers contiennent des données personnelles : le navigateur ne garde pas ces pages (Cache-Control: no-store).
"""

import csv
import io
from datetime import datetime
from functools import wraps

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, DateTimeLocalField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from ..extensions import db
from ..models import Campaign, CampaignConsent, Dossier
from ..permissions import can, require
from ..services import directory, guard, notify, registration
from ..services.activity import diff, log
from ..services.registration import RegistrationError
from .directory import _blank, _strip

bp = Blueprint("campaigns", __name__, url_prefix="/campagnes")

CAMPAIGN_FIELDS = ("season_id", "title", "opens_at", "closes_at", "allow_new", "choice", "instructions")


@bp.before_request
def count_reads():
    if request.method == "GET" and current_user.is_authenticated:
        g.write_lock = True
        guard.count_read()


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _secure(view):
    """Fichier avec les IBAN en clair : mode sécurisé obligatoire (code TOTP récent), comme dans l'annuaire."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if guard.elevated():
            return view(*args, **kwargs)
        return redirect(url_for("directory.elevate", suite=request.full_path.rstrip("?")))

    return wrapped


# ---------------------------------------------------------------------------
# Formulaires
# ---------------------------------------------------------------------------


class CampaignForm(FlaskForm):
    season_id = SelectField("Saison", coerce=int, validators=[DataRequired()])
    title = StringField(
        "Titre",
        validators=[DataRequired(), Length(max=120)],
        filters=[_strip],
        description="Vu par les familles : « Réinscriptions 2027-2028 ».",
    )
    opens_at = DateTimeLocalField("Ouverture", format="%Y-%m-%dT%H:%M", validators=[DataRequired("Date et heure.")])
    closes_at = DateTimeLocalField("Clôture", format="%Y-%m-%dT%H:%M", validators=[DataRequired("Date et heure.")])
    allow_new = BooleanField(
        "Accueillir aussi les nouvelles familles, par un formulaire public avec vérification de leur adresse email"
    )
    choice = SelectField("Les familles choisissent", choices=list(Campaign.CHOICES.items()), default=Campaign.COURSE)
    instructions = TextAreaField(
        "Message aux familles",
        validators=[Optional(), Length(max=5000)],
        description="Affiché en tête de chaque dossier : pièces à prévoir, dates à retenir…",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season_id.choices = [(s.id, s.label) for s in directory.seasons()]

    def validate_closes_at(self, field):
        if field.data and self.opens_at.data and field.data <= self.opens_at.data:
            raise ValidationError("La clôture doit suivre l'ouverture.")

    def values(self):
        return {
            "season_id": self.season_id.data,
            "title": self.title.data,
            "opens_at": self.opens_at.data,
            "closes_at": self.closes_at.data,
            "allow_new": self.allow_new.data,
            "choice": self.choice.data if self.choice.data in Campaign.CHOICES else Campaign.COURSE,
            "instructions": _blank(self.instructions.data),
        }


class ConsentForm(FlaskForm):
    label = StringField("Intitulé", validators=[DataRequired(), Length(max=120)], filters=[_strip])
    text = TextAreaField("Texte affiché aux familles", validators=[DataRequired(), Length(max=5000)])
    scope = SelectField("Demandé", choices=list(CampaignConsent.SCOPES.items()))
    required = BooleanField("Obligatoire : sans lui, le dossier ne peut pas être envoyé")


class ReturnForm(FlaskForm):
    note = TextAreaField(
        "Ce que la famille doit corriger", validators=[DataRequired("Expliquez quoi corriger."), Length(max=5000)]
    )


# ---------------------------------------------------------------------------
# Campagnes
# ---------------------------------------------------------------------------


def _campaign(campaign_id):
    return db.session.get(Campaign, campaign_id) or abort(404)


def _counts():
    rows = db.session.execute(
        db.select(Dossier.campaign_id, Dossier.status, db.func.count(Dossier.id)).group_by(
            Dossier.campaign_id, Dossier.status
        )
    ).all()
    counts = {}
    for campaign_id, status, count in rows:
        counts.setdefault(campaign_id, {})[status] = count
    return counts


@bp.route("")
@require("registration.view")
def index():
    campaigns = db.session.scalars(db.select(Campaign).order_by(Campaign.opens_at.desc())).all()
    return render_template(
        "campaigns/index.html", campaigns=campaigns, counts=_counts(), statuses=Dossier.STATUSES, now=datetime.now()
    )


@bp.route("/nouvelle", methods=["GET", "POST"])
@require("registration.manage")
def create():
    if not directory.seasons():
        flash("Créez d'abord la saison.", "error")
        return redirect(url_for(".index"))
    form = CampaignForm()
    if form.validate_on_submit():
        campaign = Campaign(**form.values())
        db.session.add(campaign)
        db.session.flush()
        log("campaign.create", f"Campagne créée : {campaign.title}")
        db.session.commit()
        flash("Campagne créée. Ajoutez maintenant les consentements à demander.", "success")
        return redirect(url_for(".detail", campaign_id=campaign.id))
    return render_template("campaigns/form.html", form=form, campaign=None)


@bp.route("/<int:campaign_id>")
@require("registration.view")
def detail(campaign_id):
    campaign = _campaign(campaign_id)
    return render_template(
        "campaigns/detail.html",
        campaign=campaign,
        counts=_counts().get(campaign.id, {}),
        statuses=Dossier.STATUSES,
        consent_form=ConsentForm(),
        now=datetime.now(),
    )


@bp.route("/<int:campaign_id>/modifier", methods=["GET", "POST"])
@require("registration.manage")
def edit(campaign_id):
    campaign = _campaign(campaign_id)
    form = CampaignForm(obj=campaign)
    if form.validate_on_submit():
        has_dossiers = db.session.scalar(db.select(Dossier.id).filter_by(campaign_id=campaign.id).limit(1))
        if has_dossiers and form.season_id.data != campaign.season_id:
            form.season_id.errors.append("Des familles ont déjà commencé leur dossier : la saison ne change plus.")
        else:
            if changes := diff(campaign, CAMPAIGN_FIELDS, form.values()):
                log("campaign.update", f"Campagne modifiée : {campaign.title}", changes=changes)
            db.session.commit()
            flash("Campagne enregistrée.", "success")
            return redirect(url_for(".detail", campaign_id=campaign.id))
    return render_template("campaigns/form.html", form=form, campaign=campaign)


@bp.route("/<int:campaign_id>/supprimer", methods=["POST"])
@require("registration.manage")
def delete(campaign_id):
    campaign = _campaign(campaign_id)
    if db.session.scalar(db.select(Dossier.id).filter_by(campaign_id=campaign.id).limit(1)):
        flash("Des familles ont commencé leur dossier : la campagne ne se supprime plus. Avancez sa clôture.", "error")
        return redirect(url_for(".detail", campaign_id=campaign.id))
    log("campaign.delete", f"Campagne supprimée : {campaign.title}")
    db.session.delete(campaign)
    db.session.commit()
    flash("Campagne supprimée.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:campaign_id>/consentements", methods=["POST"])
@require("registration.manage")
def add_consent(campaign_id):
    campaign = _campaign(campaign_id)
    form = ConsentForm()
    if form.validate_on_submit():
        campaign.consents.append(
            CampaignConsent(
                label=form.label.data,
                text=form.text.data.strip(),
                scope=form.scope.data,
                required=form.required.data,
                position=len(campaign.consents),
            )
        )
        log("campaign.consent", f"Consentement « {form.label.data} » ajouté à {campaign.title}")
        db.session.commit()
        flash("Consentement ajouté.", "success")
    else:
        flash(" ".join(e for errors in form.errors.values() for e in errors), "error")
    return redirect(url_for(".detail", campaign_id=campaign.id) + "#consentements")


@bp.route("/consentements/<int:consent_id>/supprimer", methods=["POST"])
@require("registration.manage")
def delete_consent(consent_id):
    consent = db.session.get(CampaignConsent, consent_id) or abort(404)
    campaign = consent.campaign
    sent = db.session.scalar(
        db.select(Dossier.id).where(Dossier.campaign_id == campaign.id, Dossier.status != Dossier.DRAFT).limit(1)
    )
    if sent:
        flash("Des dossiers ont déjà été envoyés avec ce consentement : il reste.", "error")
    else:
        log("campaign.consent", f"Consentement « {consent.label} » retiré de {campaign.title}")
        db.session.delete(consent)
        db.session.commit()
        flash("Consentement retiré.", "success")
    return redirect(url_for(".detail", campaign_id=campaign.id) + "#consentements")


# ---------------------------------------------------------------------------
# Dossiers
# ---------------------------------------------------------------------------


@bp.route("/<int:campaign_id>/dossiers")
@require("registration.view")
def dossiers(campaign_id):
    campaign = _campaign(campaign_id)
    status = request.args.get("statut")
    query = db.select(Dossier).filter_by(campaign_id=campaign.id)
    if status in Dossier.STATUSES:
        query = query.filter_by(status=status)
    items = db.session.scalars(query.order_by(Dossier.submitted_at.desc().nulls_last(), Dossier.id.desc())).all()
    return render_template(
        "campaigns/dossiers.html",
        campaign=campaign,
        items=items,
        status=status,
        statuses=Dossier.STATUSES,
        registration=registration,
    )


def _dossier(dossier_id):
    return db.session.get(Dossier, dossier_id) or abort(404)


@bp.route("/dossiers/<int:dossier_id>")
@require("registration.view")
def dossier(dossier_id):
    item = _dossier(dossier_id)
    courses, counts = registration.offered_courses(item.campaign)
    return render_template(
        "campaigns/dossier.html",
        dossier=item,
        changes=(
            registration.changes_at_validation(item)
            if item.status in (Dossier.VALIDATED, Dossier.TRANSFERRED)
            else registration.changes(item)
        ),
        problems=registration.problems(item) if item.status != Dossier.VALIDATED else [],
        courses={c.id: c for c in courses},
        counts=counts,
        return_form=ReturnForm(),
        registration=registration,
    )


def _tell_family(item, message):
    """Prévient la famille : notification (et email) pour les comptes, email direct pour une nouvelle famille."""
    accounts = registration.recipients(item)
    if accounts:
        notify.notify(accounts, message, link=url_for("enrollment.dossier", uuid=item.uuid))
    elif item.email:
        from ..services import mailer

        if mailer.smtp_enabled():  # nouvelle famille sans compte : un nouveau lien d'accès, par email
            mailer.send_in_background(
                item.email,
                *mailer.render(
                    None,
                    message,
                    message,
                    "Ouvrir le dossier",
                    registration.access_url(registration.renew_access(item)),
                ),
            )


@bp.route("/dossiers/<int:dossier_id>/valider", methods=["POST"])
@require("registration.manage")
def validate(dossier_id):
    item = _dossier(dossier_id)
    try:
        warnings = registration.validate(item, current_user)
    except (RegistrationError, directory.DirectoryError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for(".dossier", dossier_id=item.id))
    log("dossier.validate", f"Dossier validé : {item.household.name} ({item.campaign.title})", household=item.household)
    _tell_family(item, f"Votre dossier « {item.campaign.title} » est validé.")
    db.session.commit()
    from ..services import mailer

    mailer.deliver_quietly()
    for warning in warnings:
        flash(warning, "warning")
    flash("Dossier validé : l'annuaire est à jour.", "success")
    return redirect(url_for(".dossier", dossier_id=item.id))


@bp.route("/dossiers/<int:dossier_id>/renvoyer", methods=["POST"])
@require("registration.manage")
def send_back(dossier_id):
    item = _dossier(dossier_id)
    form = ReturnForm()
    try:
        if not form.validate_on_submit():
            raise RegistrationError("Expliquez à la famille ce qu'elle doit corriger.")
        registration.return_for_fix(item, form.note.data)
    except RegistrationError as exc:
        flash(str(exc), "error")
        return redirect(url_for(".dossier", dossier_id=item.id))
    log("dossier.return", f"Dossier renvoyé à la famille ({item.campaign.title})", household=item.household)
    _tell_family(item, f"Votre dossier « {item.campaign.title} » est à corriger : {item.review_note}")
    db.session.commit()
    from ..services import mailer

    mailer.deliver_quietly()
    flash("Dossier renvoyé à la famille, avec votre message.", "success")
    return redirect(url_for(".dossier", dossier_id=item.id))


@bp.route("/dossiers/<int:dossier_id>/reporte", methods=["POST"])
@require("registration.manage")
def mark_transferred(dossier_id):
    item = _dossier(dossier_id)
    try:
        registration.mark_transferred(item)
    except RegistrationError as exc:
        flash(str(exc), "error")
        return redirect(url_for(".dossier", dossier_id=item.id))
    log("dossier.transfer", "Dossier reporté dans le logiciel de gestion", household=item.household)
    db.session.commit()
    flash("Dossier noté comme reporté.", "success")
    return redirect(url_for(".dossier", dossier_id=item.id))


# ---------------------------------------------------------------------------
# Report vers le logiciel de gestion
# ---------------------------------------------------------------------------


def _to_report(campaign):
    return db.session.scalars(
        db.select(Dossier).filter_by(campaign_id=campaign.id, status=Dossier.VALIDATED).order_by(Dossier.validated_at)
    ).all()


def _csv(rows, filename):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(registration.EXPORT_COLUMNS)
    writer.writerows(rows)
    response = Response("﻿" + buffer.getvalue(), mimetype="text/csv")  # BOM : Excel lit les accents
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@bp.route("/<int:campaign_id>/report")
@require("registration.manage")
def report(campaign_id):
    campaign = _campaign(campaign_id)
    return render_template(
        "campaigns/report.html", campaign=campaign, items=_to_report(campaign), with_iban=can(current_user, "bank.view")
    )


@bp.route("/<int:campaign_id>/report.csv")
@require("registration.manage")
def report_csv(campaign_id):
    campaign = _campaign(campaign_id)
    log("dossier.export", f"Fichier de report téléchargé ({campaign.title}), sans IBAN")
    db.session.commit()
    return _csv(registration.export_rows(_to_report(campaign)), f"report-{campaign.id}.csv")


@bp.route("/<int:campaign_id>/report-iban.csv")
@require("bank.view")
@_secure
def report_csv_iban(campaign_id):
    if not can(current_user, "registration.manage"):
        abort(403)
    campaign = _campaign(campaign_id)
    items = _to_report(campaign)
    log("dossier.export_iban", f"Fichier de report AVEC IBAN téléchargé ({campaign.title}, {len(items)} dossier(s))")
    db.session.commit()
    return _csv(registration.export_rows(items, with_iban=True), f"report-{campaign.id}-iban.csv")


@bp.route("/<int:campaign_id>/report/fait", methods=["POST"])
@require("registration.manage")
def report_done(campaign_id):
    campaign = _campaign(campaign_id)
    ids = {int(i) for i in request.form.getlist("dossier") if i.isdigit()}
    done = 0
    for item in _to_report(campaign):
        if item.id in ids:
            registration.mark_transferred(item)
            done += 1
    log("dossier.transfer", f"{done} dossier(s) notés comme reportés ({campaign.title})")
    db.session.commit()
    flash(
        f"{done} dossier(s) notés comme reportés. Pensez à supprimer le fichier téléchargé de votre ordinateur.",
        "success",
    )
    return redirect(url_for(".report", campaign_id=campaign.id))

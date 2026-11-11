"""Conservation des données : durée choisie par l'association, anonymisation des anciens adhérents, purge des
anciens dossiers d'inscription. Rien n'est automatique : chaque anonymisation est décidée à l'écran."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import IntegerField
from wtforms.validators import NumberRange, Optional

from ..extensions import db
from ..permissions import require
from ..services import retention
from ..services.activity import log

bp = Blueprint("retention", __name__, url_prefix="/annuaire/conservation")


class DurationForm(FlaskForm):
    seasons = IntegerField(
        "Durée de conservation (en saisons)",
        validators=[Optional(), NumberRange(min=1, max=50, message="Entre 1 et 50 saisons.")],
        description="Une personne sans adhésion depuis ce nombre de saisons peut être anonymisée. "
        "Vide : pas encore décidé, rien n'est proposé.",
    )


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("", methods=["GET", "POST"])
@require("directory.anonymize")
def index():
    form = DurationForm(seasons=retention.seasons_kept())
    if form.validate_on_submit():
        before = retention.seasons_kept()
        retention.set_seasons_kept(form.seasons.data)
        if before != form.seasons.data:
            log(
                "retention.duration",
                f"Durée de conservation : {form.seasons.data or 'non décidée'} saison(s)",
                changes={"seasons": [before, form.seasons.data]},
            )
        db.session.commit()
        flash("Durée enregistrée.", "success")
        return redirect(url_for(".index"))
    return render_template(
        "retention/index.html",
        form=form,
        candidates=retention.candidates(),
        limit=retention.oldest_kept_season(),
        dossiers=len(retention.old_dossiers()),
        kept=retention.seasons_kept(),
    )


@bp.route("/anonymiser", methods=["POST"])
@require("directory.anonymize")
def anonymize():
    allowed = {p.id: p for p in retention.candidates()}
    chosen = [allowed[int(i)] for i in request.form.getlist("personne") if i.isdigit() and int(i) in allowed]
    for person in chosen:
        retention.anonymize(person)
    if chosen:
        log("retention.anonymize", f"{len(chosen)} fiche(s) anonymisée(s)")
    db.session.commit()
    flash(f"{len(chosen)} fiche(s) anonymisée(s).", "success")
    return redirect(url_for(".index"))


@bp.route("/dossiers", methods=["POST"])
@require("directory.anonymize")
def purge_dossiers():
    count = retention.purge_dossiers()
    if count:
        log("retention.dossiers", f"{count} ancien(s) dossier(s) d'inscription supprimé(s)")
    db.session.commit()
    flash(f"{count} ancien(s) dossier(s) d'inscription supprimé(s).", "success")
    return redirect(url_for(".index"))

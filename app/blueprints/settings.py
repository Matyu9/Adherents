import re
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import RadioField, StringField
from wtforms.validators import Length

from ..extensions import db
from ..permissions import require
from ..services import branding, mailer, sessions, tasks
from ..services.activity import log
from ..services.photos import InvalidImage

bp = Blueprint("settings", __name__)


class BrandingForm(FlaskForm):
    name = StringField("Nom de l'association", validators=[Length(max=branding.NAME_MAX)])
    subtitle = StringField("Sous-titre", validators=[Length(max=branding.SUBTITLE_MAX)])
    palette = RadioField("Couleur principale", choices=[(k, label) for k, (label, *_) in branding.PALETTES.items()])


def _branding_form():
    brand = branding.current()
    return BrandingForm(data={"name": brand.name, "subtitle": brand.subtitle, "palette": brand.palette})


def _render_index(branding_form=None):
    open_sessions, connected = sessions.stats()
    return render_template(
        "settings/index.html",
        cfg=current_app.config,
        smtp=mailer.smtp_enabled(),
        open_sessions=open_sessions,
        connected=connected,
        default_subtitle=branding.DEFAULT_SUBTITLE,
        branding_form=branding_form or _branding_form(),
    )


@bp.route("/parametres")
@require("settings.manage")
def index():
    return _render_index()


# ---------------------------------------------------------------------------
# Personnalisation : nom, sous-titre, couleur et logo
# ---------------------------------------------------------------------------


@bp.route("/parametres/apparence", methods=["POST"])
@require("settings.manage")
def save_branding():
    form = BrandingForm()
    if not form.validate_on_submit():
        return _render_index(form)
    branding.save(form.name.data, form.subtitle.data, form.palette.data)
    brand = branding.current()
    log("settings.branding", f"Apparence modifiée : « {brand.name} », couleur {branding.PALETTES[brand.palette][0]}")
    db.session.commit()
    flash("Apparence enregistrée.", "success")
    return redirect(url_for(".index") + "#apparence")


@bp.route("/parametres/logo", methods=["POST"])
@require("settings.manage")
def set_logo():
    file = request.files.get("logo")
    if not file or not file.filename:
        flash("Choisissez une image.", "warning")
    else:
        try:
            branding.set_logo(file)
        except InvalidImage as exc:
            flash(str(exc), "error")
        else:
            log("settings.logo", "Logo de l'association modifié")
            db.session.commit()
            flash("Logo enregistré.", "success")
    return redirect(url_for(".index") + "#apparence")


@bp.route("/parametres/logo/supprimer", methods=["POST"])
@require("settings.manage")
def remove_logo():
    branding.remove_logo()
    log("settings.logo", "Logo de l'association retiré")
    db.session.commit()
    flash("Logo retiré.", "success")
    return redirect(url_for(".index") + "#apparence")


@bp.route("/logo/<name>")
def logo_file(name):
    """Logo de l'association : public, il apparaît sur la page de connexion."""
    if not re.fullmatch(r"[0-9a-f]{32}\.png", name):
        abort(404)
    response = send_from_directory(Path(current_app.config["UPLOAD_DIR"]) / "logo", name)
    response.cache_control.max_age = 30 * 24 * 3600  # un nouveau logo a un nouveau nom
    response.cache_control.public = True
    return response


@bp.route("/parametres/email-test", methods=["POST"])
@require("settings.manage")
def test_email():
    if not mailer.smtp_enabled():
        flash("Le SMTP n'est pas configuré (variables SMTP_* du fichier .env).", "error")
    else:
        try:
            mailer.send(current_user.email, *mailer.compose_test(current_user))
        except Exception as exc:  # noqa: BLE001
            flash(f"Échec de l'envoi : {exc}", "error")
        else:
            flash(f"Email de test envoyé à {current_user.email}.", "success")
    return redirect(url_for(".index"))


@bp.route("/parametres/taches", methods=["POST"])
@require("settings.manage")
def run_tasks():
    flash(tasks.run(), "info")
    return redirect(url_for(".index"))


@bp.route("/parametres/deconnecter-tout", methods=["POST"])
@require("settings.manage")
def revoke_sessions():
    """Ferme toutes les sessions (sauf la sienne) : en cas de doute sur un compte ou un appareil perdu."""
    closed = sessions.revoke_all()
    log("sessions.revoke_all", f"Déconnexion de tout le monde ({closed} session(s) fermée(s))")
    db.session.commit()
    flash(f"{closed} session(s) fermée(s) : chacun devra se reconnecter. Vous restez connecté.", "success")
    return redirect(url_for(".index"))


# ---------------------------------------------------------------------------
# Adhérents : ce que les familles modifient elles-mêmes
# ---------------------------------------------------------------------------


@bp.route("/parametres/adherents", methods=["GET", "POST"])
@require("settings.manage")
def members():
    from ..services import options

    if request.method == "POST":
        options.set_member_editable(request.form.getlist("fields"))
        log("settings.members", "Coordonnées modifiables par les familles : " + (", ".join(sorted(options.member_editable())) or "aucune"))
        db.session.commit()
        flash("Réglages enregistrés.", "success")
        return redirect(url_for(".members"))
    return render_template("settings/members.html", fields=options.MEMBER_FIELDS, editable=options.member_editable())

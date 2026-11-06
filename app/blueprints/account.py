"""Mon compte : chacun gère son profil, son affichage et ses appareils connectés."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import RadioField, StringField
from wtforms.validators import DataRequired, Length

from ..extensions import db
from ..services import preferences, sessions
from ..services.activity import log

bp = Blueprint("account", __name__, url_prefix="/mon-compte")


@bp.before_request
@login_required
def logged_in():
    pass


class ProfileForm(FlaskForm):
    first_name = StringField("Prénom", validators=[DataRequired(), Length(max=80)])
    last_name = StringField("Nom", validators=[DataRequired(), Length(max=80)])


class DisplayForm(FlaskForm):
    theme = RadioField("Thème", choices=[("auto", "Comme l'appareil"), ("light", "Clair"), ("dark", "Sombre")])
    text_size = RadioField("Taille du texte", choices=list(preferences.TEXT_SIZES.items()))


def _display_form(formdata=None):
    """Formulaire prérempli : un champ absent de l'envoi garde sa valeur actuelle."""
    return DisplayForm(formdata=formdata, data={
        "theme": current_user.theme or "auto",
        "text_size": current_user.text_size or preferences.DEFAULT_TEXT_SIZE})


@bp.route("", methods=["GET", "POST"])
def index():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        current_user.first_name = form.first_name.data.strip()
        current_user.last_name = form.last_name.data.strip()
        log("account.profile", f"{current_user.full_name} a modifié son profil", target=current_user)
        db.session.commit()
        flash("Profil enregistré.", "success")
        return redirect(url_for(".index"))
    return render_template("account/index.html", profile=form, display=_display_form(),
                           sessions=sessions.of(current_user), device_label=sessions.device_label)


@bp.route("/affichage", methods=["POST"])
def display():
    form = _display_form(request.form)
    if form.validate_on_submit():
        current_user.theme = None if form.theme.data == "auto" else form.theme.data
        current_user.text_size = None if form.text_size.data == preferences.DEFAULT_TEXT_SIZE else form.text_size.data
        db.session.commit()
        flash("Affichage enregistré.", "success")
    else:
        flash("Choix d'affichage invalide.", "error")
    return redirect(url_for(".index") + "#affichage")


@bp.route("/sessions/<session_id>/fermer", methods=["POST"])
def revoke_session(session_id):
    if session_id == current_user.session_token:
        flash("Pour fermer cette session, utilisez « Déconnexion ».", "info")
    elif sessions.revoke_one(current_user, session_id):
        db.session.commit()
        flash("Appareil déconnecté.", "success")
    return redirect(url_for(".index") + "#sessions")


@bp.route("/sessions/fermer", methods=["POST"])
def revoke_others():
    sessions.revoke(current_user, keep_current=True)
    db.session.commit()
    flash("Vos autres appareils sont déconnectés.", "success")
    return redirect(url_for(".index") + "#sessions")

"""Mon compte : chacun gère son profil, son adresse, sa double authentification, son affichage et ses appareils."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import EmailField, PasswordField, RadioField, StringField
from wtforms.validators import DataRequired, Length, Regexp

from ..extensions import db
from ..models import Account, AccountToken
from ..services import accounts, logout, mailer, oidc, preferences, sessions, throttle
from ..services import totp as totp_service
from ..services.activity import log

bp = Blueprint("account", __name__, url_prefix="/mon-compte")

# Une session volée ne doit pas pouvoir deviner le mot de passe, ni servir à envoyer des emails en rafale.
_reauth_failures = throttle.Limiter("reauth", 8, 15 * 60)
_email_requests = throttle.Limiter("email-request", 5, 60 * 60)


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


class EmailForm(FlaskForm):
    email = EmailField("Nouvelle adresse", validators=[
        DataRequired(), Length(max=255), Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Adresse email invalide.")],
        filters=[lambda value: value.strip() if value else value])
    password = PasswordField("Mot de passe actuel", validators=[DataRequired()])


class TotpForm(FlaskForm):
    password = PasswordField("Mot de passe actuel", validators=[DataRequired()])
    code = StringField("Code à 6 chiffres", validators=[DataRequired(), Length(max=12)])


def _password_ok(field):
    """Revérifie le mot de passe avant un changement sensible. Sinon, note l'erreur sur le champ."""
    keys = (f"account:{current_user.id}",)
    if _reauth_failures.blocked(keys):
        field.errors = [*field.errors, "Trop de tentatives : réessayez dans quelques minutes."]
        return False
    if current_user.check_password(field.data):
        return True
    _reauth_failures.hit(keys)
    db.session.commit()
    field.errors = [*field.errors, "Mot de passe incorrect."]
    return False


def _code_ok(field):
    """Vérifie un code TOTP (le compteur anti-rejeu est validé par la route). Sinon, note l'erreur sur le champ."""
    keys = (f"account:{current_user.id}",)
    if totp_service.failures.blocked(keys):
        field.errors = [*field.errors, "Trop de tentatives : réessayez dans quelques minutes."]
        return False
    if totp_service.verify_code(current_user, field.data):
        totp_service.failures.clear(keys)
        return True
    totp_service.failures.hit(keys)
    db.session.commit()
    field.errors = [*field.errors, "Code incorrect."]
    return False


def _display_form(formdata=None):
    """Formulaire prérempli : un champ absent de l'envoi garde sa valeur actuelle."""
    return DisplayForm(formdata=formdata, data={
        "theme": current_user.theme or "auto",
        "text_size": current_user.text_size or preferences.DEFAULT_TEXT_SIZE})


def _render_index(profile=None, email_form=None):
    return render_template("account/index.html", profile=profile or ProfileForm(obj=current_user),
                           display=_display_form(), email_form=email_form or EmailForm(formdata=None),
                           smtp=mailer.smtp_enabled(), sessions=sessions.of(current_user),
                           device_label=sessions.device_label, consents=oidc.consents_of(current_user))


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
    return _render_index(profile=form)


@bp.route("/email", methods=["POST"])
def change_email():
    """Le changement n'a lieu qu'au clic sur le lien reçu à la nouvelle adresse."""
    if not mailer.smtp_enabled():
        flash("L'envoi d'emails n'est pas configuré : demandez à un responsable de changer votre adresse.", "error")
        return redirect(url_for(".index"))
    form = EmailForm()
    if not form.validate_on_submit() or not _password_ok(form.password):
        return _render_index(email_form=form)
    email = form.email.data.strip().lower()
    if email == current_user.email:
        form.email.errors = ["C'est déjà votre adresse."]
        return _render_index(email_form=form)
    keys = (f"account:{current_user.id}",)
    if _email_requests.blocked(keys):
        flash("Trop de demandes : réessayez dans une heure.", "error")
        return redirect(url_for(".index"))
    _email_requests.hit(keys)
    raw = None
    if db.session.scalar(db.select(Account.id).filter_by(email=email)) is None:
        raw = accounts.issue(current_user, AccountToken.EMAIL, email=email, proves_email=True)
        log("account.email_request", f"{current_user.full_name} a demandé à changer son email pour {email}",
            target=current_user)
    db.session.commit()
    if raw:
        accounts.send_email_confirmation(current_user, email, raw)
    # Même message si l'adresse est déjà prise : ce formulaire ne doit pas révéler qui a un compte.
    flash(f"Si {email} est disponible, un lien de confirmation vient d'y être envoyé. "
          "Votre adresse changera une fois confirmée.", "info")
    return redirect(url_for(".index"))


@bp.route("/email/verifier", methods=["POST"])
def verify_email():
    """Prouve que l'adresse actuelle est la bonne, si le compte n'a jamais cliqué sur un lien reçu par email."""
    if current_user.email_verified:
        flash("Votre adresse est déjà vérifiée.", "info")
    elif not mailer.smtp_enabled():
        flash("L'envoi d'emails n'est pas configuré.", "error")
    elif _email_requests.blocked((f"account:{current_user.id}",)):
        flash("Trop de demandes : réessayez dans une heure.", "error")
    else:
        _email_requests.hit((f"account:{current_user.id}",))
        raw = accounts.issue(current_user, AccountToken.EMAIL, email=current_user.email, proves_email=True)
        db.session.commit()
        if accounts.send_email_confirmation(current_user, current_user.email, raw):
            flash("Un lien de vérification vient de vous être envoyé.", "success")
        else:
            flash("L'email n'a pas pu partir : réessayez plus tard.", "error")
    return redirect(url_for(".index"))


@bp.route("/double-authentification", methods=["GET", "POST"])
def totp():
    """Configuration, ou état si elle est déjà active. Le mot de passe protège d'une session volée qui verrouillerait le compte."""
    if current_user.totp_enabled:
        return render_template("account/totp_active.html", form=TotpForm(formdata=None),
                               remaining=totp_service.remaining_recovery_codes(current_user),
                               required=totp_service.required(current_user))
    secret = totp_service.pending_secret(current_user)
    db.session.commit()  # le même secret après un rechargement, sinon l'application du téléphone ne correspondrait plus
    form = TotpForm()
    if form.validate_on_submit() and _password_ok(form.password) and _code_ok(form.code):
        codes = totp_service.activate(current_user)
        sessions.revoke(current_user, keep_current=True)
        log("account.totp_enable", f"{current_user.full_name} a activé la double authentification",
            target=current_user)
        db.session.commit()
        flash("Double authentification activée.", "success")
        return render_template("account/recovery_codes.html", codes=codes)
    return render_template("account/totp_setup.html", form=form, secret=secret,
                           qr=totp_service.qr_data_uri(totp_service.uri(current_user, secret)),
                           required=totp_service.required(current_user))


@bp.route("/double-authentification/codes", methods=["POST"])
def totp_codes():
    if not current_user.totp_enabled:
        return redirect(url_for(".totp"))
    form = TotpForm()
    if form.validate_on_submit() and _password_ok(form.password) and _code_ok(form.code):
        codes = totp_service.new_recovery_codes(current_user)
        log("account.totp_codes", f"{current_user.full_name} a généré de nouveaux codes de secours",
            target=current_user)
        db.session.commit()
        return render_template("account/recovery_codes.html", codes=codes)
    return _totp_form_error(form)


@bp.route("/double-authentification/desactiver", methods=["POST"])
def totp_disable():
    if not current_user.totp_enabled:
        return redirect(url_for(".totp"))
    if totp_service.required(current_user):
        flash("La double authentification est obligatoire pour votre rôle.", "error")
        return redirect(url_for(".totp"))
    form = TotpForm()
    if form.validate_on_submit() and _password_ok(form.password) and _code_ok(form.code):
        totp_service.disable(current_user)
        log("account.totp_disable", f"{current_user.full_name} a désactivé la double authentification",
            target=current_user)
        db.session.commit()
        flash("Double authentification désactivée.", "success")
        return redirect(url_for(".index"))
    return _totp_form_error(form)


def _totp_form_error(form):
    return render_template("account/totp_active.html", form=form,
                           remaining=totp_service.remaining_recovery_codes(current_user),
                           required=totp_service.required(current_user))


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


@bp.route("/sessions/<sid>/fermer", methods=["POST"])
def revoke_session(sid):
    if sid == sessions.current_sid():
        flash("Pour fermer cette session, utilisez « Déconnexion ».", "info")
    elif sessions.revoke_one(current_user, sid):
        db.session.commit()
        flash("Appareil déconnecté.", "success")
    return redirect(url_for(".index") + "#sessions")


@bp.route("/applications/<int:client_pk>/retirer", methods=["POST"])
def revoke_consent(client_pk):
    if oidc.revoke_consent(current_user, client_pk):
        logout.end_client_sessions(current_user, client_pk)
        log("oidc.consent_revoked", f"{current_user.full_name} a retiré une autorisation", target=current_user)
        db.session.commit()
        flash("Autorisation retirée.", "success")
    return redirect(url_for(".index") + "#applications")


@bp.route("/sessions/fermer", methods=["POST"])
def revoke_others():
    sessions.revoke(current_user, keep_current=True)
    db.session.commit()
    flash("Vos autres appareils sont déconnectés.", "success")
    return redirect(url_for(".index") + "#sessions")

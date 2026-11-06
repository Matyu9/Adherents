from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField
from wtforms.validators import DataRequired, EqualTo, Length

from ..extensions import db
from ..models import Account
from ..services import sessions, throttle
from ..services.activity import log

# Limitation des tentatives : au-delà de MAX_FAILURES échecs en WINDOW secondes pour un même
# email (ou une même IP), la connexion est refusée temporairement.
MAX_FAILURES = 8
WINDOW = 15 * 60
_failures = throttle.Limiter("login", MAX_FAILURES, WINDOW)
# Hash factice pour que la vérification prenne le même temps si le compte n'existe pas.
_DUMMY = Account()
_DUMMY.set_password("pas-un-vrai-mot-de-passe")

bp = Blueprint("auth", __name__)


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired()])
    password = PasswordField("Mot de passe", validators=[DataRequired()])
    remember = BooleanField("Rester connecté")


class PasswordForm(FlaskForm):
    current = PasswordField("Mot de passe actuel", validators=[DataRequired()])
    new = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirmation", validators=[EqualTo("new", "Les mots de passe ne correspondent pas.")])


@bp.before_app_request
def force_password_change():
    """Un compte créé avec un mot de passe provisoire doit le changer avant tout."""
    if (current_user.is_authenticated and current_user.must_change_password
            and request.endpoint not in ("auth.password", "auth.logout", "static", "csp.report",
                                         "settings.logo_file")):
        if request.method == "GET" and not request.headers.get("HX-Request"):
            flash("Choisissez un nouveau mot de passe pour continuer.", "warning")
        return redirect(url_for("auth.password"))


def safe_next(target, default=None):
    """N'accepte qu'un chemin local (« /… »), jamais « //hôte » ni « /\\hôte » (normalisé en // par les navigateurs)."""
    if (target and target.startswith("/") and not target.startswith("//") and "\\" not in target
            and not urlsplit(target).netloc and not urlsplit(target).scheme):
        return target
    return default or url_for("dashboard.index")


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        keys = (f"email:{email}", f"ip:{request.remote_addr}")
        if _failures.blocked(keys):
            flash("Trop de tentatives : réessayez dans quelques minutes.", "error")
            return render_template("auth/login.html", form=form), 429
        account = db.session.scalar(db.select(Account).filter_by(email=email))
        valid = (account or _DUMMY).check_password(form.password.data)
        if account and account.active and valid:
            _failures.clear(keys)
            sessions.start(account, remember=form.remember.data)  # commit
            return redirect(safe_next(request.args.get("next")))
        _failures.hit(keys)
        # Le texte saisi n'est gardé que s'il désigne un compte : ce peut être un mot de passe tapé au mauvais endroit.
        log("auth.failed", f"Échec de connexion pour {account.email if account else 'une adresse inconnue'} "
                           f"({request.remote_addr})", target=account)
        db.session.commit()
        flash("Email ou mot de passe incorrect.", "error")
    return render_template("auth/login.html", form=form)


@bp.route("/deconnexion", methods=["POST"])
@login_required
def logout():
    sessions.end()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/mot-de-passe", methods=["GET", "POST"])
@login_required
def password():
    form = PasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current.data):
            form.current.errors.append("Mot de passe actuel incorrect.")
        elif form.new.data == form.current.data:
            form.new.errors.append("Le nouveau mot de passe doit être différent de l'actuel.")
        else:
            current_user.set_password(form.new.data)
            current_user.must_change_password = False
            sessions.revoke(current_user, keep_current=True)  # une session volée ne survit pas au changement
            log("account.password", f"{current_user.full_name} a changé son mot de passe", target=current_user)
            db.session.commit()
            flash("Mot de passe modifié.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/password.html", form=form)

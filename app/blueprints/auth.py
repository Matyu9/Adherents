import time
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, StringField
from wtforms.validators import DataRequired, EqualTo, Length

from ..extensions import db
from ..models import Account, AccountToken
from ..services import accounts, mailer, oidc, sessions, throttle, totp
from ..services.activity import log

# Limitation des tentatives : au-delà de MAX_FAILURES échecs en WINDOW secondes pour un même
# email (ou une même IP), la connexion est refusée temporairement.
MAX_FAILURES = 8
WINDOW = 15 * 60
_failures = throttle.Limiter("login", MAX_FAILURES, WINDOW)
# Chaque demande compte, aboutie ou non : pas d'envoi d'emails en rafale vers une même adresse.
_reset_requests = throttle.Limiter("reset", 5, 60 * 60)
# Entre le mot de passe et le code : l'état tient dans la session, et ne vaut que quelques minutes.
PENDING = "pending_login"
PENDING_LIFETIME = 5 * 60
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
    new = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(min=8, max=200)])
    confirm = PasswordField("Confirmation", validators=[EqualTo("new", "Les mots de passe ne correspondent pas.")])


class NewPasswordForm(FlaskForm):
    new = PasswordField("Mot de passe", validators=[DataRequired(), Length(min=8, max=200)])
    confirm = PasswordField("Confirmation", validators=[EqualTo("new", "Les mots de passe ne correspondent pas.")])


class ForgotForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired()])


class CodeForm(FlaskForm):
    code = StringField("Code", validators=[DataRequired(), Length(max=40)])


# Les pages du serveur OIDC traitent elles-mêmes ces règles : elles doivent pouvoir répondre à l'appli cliente.
ALWAYS_OPEN = ("auth.logout", "auth.token_link", "static", "csp.report", "settings.logo_file", "oidc.discovery",
               "oidc.jwks", "oidc.authorize", "oidc.resume", "oidc.token", "oidc.userinfo", "oidc.end_session", "oidc.directory",
               "oidc.end_session_confirm")


@bp.before_app_request
def enforce_account_rules():
    """Avant tout le reste : remplacer un mot de passe imposé, puis configurer la double authentification si elle est due."""
    if not current_user.is_authenticated or request.endpoint in ALWAYS_OPEN:
        return None
    page = request.method == "GET" and not request.headers.get("HX-Request")
    if current_user.must_change_password:
        if request.endpoint == "auth.password":
            return None
        if page:
            flash("Choisissez un nouveau mot de passe pour continuer.", "warning")
        return redirect(url_for("auth.password"))
    if request.endpoint != "account.totp" and not current_user.totp_enabled and totp.required(current_user):
        if page:
            flash("La double authentification est obligatoire pour votre rôle : configurez-la pour continuer.",
                  "warning")
        return redirect(url_for("account.totp"))
    # « Mon compte » : c'est là que mène l'écran des codes de secours, à la fin de la double authentification.
    if (request.method == "GET" and request.endpoint in ("dashboard.index", "account.index")
            and (query := oidc.pop_resume()) is not None):
        return redirect(url_for("oidc.resume") + "?" + query)  # l'étape imposée est faite : retour vers l'appli
    return None


def safe_next(target, default=None):
    """N'accepte qu'un chemin local (« /… »), jamais « //hôte » ni « /\\hôte » (normalisé en // par les navigateurs)."""
    if (target and target.startswith("/") and not target.startswith("//") and "\\" not in target
            and not urlsplit(target).netloc and not urlsplit(target).scheme):
        return target
    return default or url_for("dashboard.index")


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    # `reauth` : une appli exige une nouvelle saisie. La session en cours ne change qu'une fois celle-ci réussie.
    if current_user.is_authenticated and request.args.get("reauth") != "1":
        return redirect(safe_next(request.args.get("next")))
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
            target = safe_next(request.args.get("next"))
            if account.totp_enabled:
                session[PENDING] = {"account": account.id, "remember": bool(form.remember.data), "next": target,
                                    "at": int(time.time())}
                return redirect(url_for(".two_factor"))
            _failures.clear(keys)
            sessions.start(account, remember=form.remember.data)  # commit
            return redirect(target)
        _failures.hit(keys)
        # Le texte saisi n'est gardé que s'il désigne un compte : ce peut être un mot de passe tapé au mauvais endroit.
        log("auth.failed", f"Échec de connexion pour {account.email if account else 'une adresse inconnue'} "
                           f"({request.remote_addr})", target=account)
        db.session.commit()
        flash("Email ou mot de passe incorrect.", "error")
    return render_template("auth/login.html", form=form)


def _pending_account():
    """Compte dont le mot de passe vient d'être vérifié, sinon None (rien en cours, trop ancien, compte coupé)."""
    pending = session.get(PENDING)
    if not pending or time.time() - pending["at"] > PENDING_LIFETIME:
        return None
    account = db.session.get(Account, pending["account"])
    return account if account is not None and account.active and account.totp_enabled else None


@bp.route("/connexion/verification", methods=["GET", "POST"])
def two_factor():
    if current_user.is_authenticated and PENDING not in session:
        return redirect(url_for("dashboard.index"))
    account = _pending_account()
    if account is None:
        session.pop(PENDING, None)
        flash("Reprenez la connexion depuis le début.", "info")
        return redirect(url_for(".login"))
    form = CodeForm()
    if form.validate_on_submit():
        keys = (f"account:{account.id}",)
        if totp.failures.blocked(keys):
            flash("Trop de tentatives : réessayez dans quelques minutes.", "error")
            return render_template("auth/two_factor.html", form=form), 429
        method = totp.check(account, form.code.data)
        if method is None:
            totp.failures.hit(keys)
            log("auth.totp_failed", f"Code de double authentification refusé pour {account.email} "
                                    f"({request.remote_addr})", target=account)
            db.session.commit()
            flash("Code incorrect.", "error")
            return render_template("auth/two_factor.html", form=form)
        pending = session.pop(PENDING)
        totp.failures.clear(keys)
        _failures.clear((f"email:{account.email}", f"ip:{request.remote_addr}"))
        if method == "recovery":
            left = totp.remaining_recovery_codes(account)
            log("auth.recovery_code", f"{account.full_name} s'est connecté avec un code de secours ({left} restant(s))",
                user=account, target=account)
            flash(f"Code de secours utilisé. Il vous en reste {left} : "
                  "pensez à en générer de nouveaux dans Mon compte.", "warning")
        sessions.start(account, remember=pending["remember"])  # commit
        return redirect(safe_next(pending["next"]))
    return render_template("auth/two_factor.html", form=form)


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


@bp.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def forgot():
    if current_user.is_authenticated:
        return redirect(url_for(".password"))
    form = ForgotForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        keys = (f"email:{email}", f"ip:{request.remote_addr}")
        if _reset_requests.blocked(keys):
            flash("Trop de demandes : réessayez dans quelques minutes.", "error")
            return render_template("auth/forgot.html", form=form, smtp=mailer.smtp_enabled()), 429
        _reset_requests.hit(keys)
        account = db.session.scalar(db.select(Account).filter_by(email=email))
        raw = None
        if account and account.active and mailer.smtp_enabled():
            raw = accounts.issue(account, AccountToken.RESET, proves_email=True)
            log("account.reset_request", f"Lien de réinitialisation demandé pour {account.full_name}", user=account,
                target=account)
        db.session.commit()
        if raw:
            accounts.send_reset_in_background(account, raw)
        # Même réponse que le compte existe ou non : la page ne révèle pas qui a un compte.
        flash("Si un compte actif correspond à cette adresse, un lien de réinitialisation vient d'y être envoyé.",
              "info")
        return redirect(url_for(".login"))
    return render_template("auth/forgot.html", form=form, smtp=mailer.smtp_enabled())


@bp.route("/lien/<token>", methods=["GET", "POST"])
def token_link(token):
    """Lien reçu par email. Ouvrir la page ne consomme rien (un antivirus suit les liens) : il faut envoyer le formulaire."""
    found = accounts.find(token)
    if found is None:
        flash("Ce lien n'est plus valable : il a expiré ou a déjà servi.", "error")
        return redirect(url_for(".login"))
    if found.purpose == AccountToken.EMAIL:
        return _confirm_email(found)
    account = found.account
    invited = found.purpose == AccountToken.INVITE
    form = NewPasswordForm()
    if form.validate_on_submit():
        if current_user.is_authenticated:
            sessions.end()
        account.set_password(form.new.data)
        account.must_change_password = False
        if found.proves_email:
            account.email_verified = True
        accounts.consume(found, AccountToken.INVITE, AccountToken.RESET)
        sessions.revoke(account)  # un appareil volé ne survit pas à la réinitialisation
        log("account.password", f"{account.full_name} a choisi son mot de passe"
            + (" (invitation)" if invited else " (mot de passe oublié)"), user=account, target=account)
        if account.totp_enabled:
            # Le lien remplace le mot de passe, pas le second facteur : la boîte mail seule n'ouvre pas le compte.
            db.session.commit()
            flash("Mot de passe modifié. Connectez-vous, avec votre code de double authentification.", "success")
            return redirect(url_for(".login"))
        sessions.start(account)  # commit
        flash("Bienvenue ! Votre mot de passe est enregistré." if invited else "Mot de passe modifié.", "success")
        return redirect(url_for("dashboard.index"))
    return render_template("auth/token_password.html", form=form, account=account, invited=invited)


def _confirm_email(found):
    account = found.account
    taken = db.session.scalar(db.select(Account.id).where(Account.email == found.email, Account.id != account.id))
    form = FlaskForm()
    if form.validate_on_submit() and not taken:
        old = account.email
        account.email = found.email
        account.email_verified = True
        accounts.revoke(account)  # y compris un lien de réinitialisation parti vers l'ancienne adresse
        changed = old != account.email
        log("account.email", f"Email de {account.full_name} modifié : {old} → {account.email}" if changed
            else f"Email de {account.full_name} confirmé", user=account, target=account)
        db.session.commit()
        if changed:
            accounts.send_email_changed_notice(account, old)
        flash("Adresse email confirmée.", "success")
        return redirect(url_for("dashboard.index") if current_user.is_authenticated else url_for(".login"))
    return render_template("auth/token_email.html", account=account, email=found.email, taken=taken, form=form)

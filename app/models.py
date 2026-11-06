"""Modèle de données."""
import uuid
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now(),
                           onupdate=datetime.now)


# ---------------------------------------------------------------------------
# Comptes
# ---------------------------------------------------------------------------

class Role(db.Model):
    """Rôle attribuable aux comptes, avec ses droits (cochés dans l'écran « Rôles »).

    Les trois rôles de base existent toujours (non supprimables) ; l'administrateur a tous les droits
    quelle que soit sa liste, pour qu'on ne puisse pas se retirer l'accès à l'appli.
    """
    ADMIN = "admin"
    GESTIONNAIRE = "gestionnaire"
    ADHERENT = "adherent"
    BUILTIN = (ADMIN, GESTIONNAIRE, ADHERENT)
    DEFAULT_NAMES = {ADMIN: "Administrateur", GESTIONNAIRE: "Gestionnaire", ADHERENT: "Adhérent"}

    key = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.String(255))
    permissions = db.Column(db.JSON, nullable=False, default=list)

    @property
    def builtin(self):
        return self.key in self.BUILTIN

    def __repr__(self):
        return f"<Role {self.key}>"


account_access_group = db.Table(
    "account_access_group",
    db.Column("account_id", db.ForeignKey("account.id", ondelete="CASCADE"), primary_key=True),
    db.Column("group_id", db.ForeignKey("access_group.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class AccessGroup(db.Model):
    """Groupe de comptes : décidera qui peut entrer dans quelle appli, et sera transmis aux applis (OIDC).

    `key` est la valeur transmise aux applis : elle ne change jamais, même si le groupe est renommé.
    """
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.String(255))

    members = db.relationship("Account", secondary=account_access_group, back_populates="groups")


class Account(UserMixin, TimestampMixin, db.Model):
    """Compte de connexion : ce qui permet d'entrer dans l'appli, et plus tard dans les applis clientes (OIDC)."""
    id = db.Column(db.Integer, primary_key=True)
    # Identifiant public, stable et opaque (claim OIDC « sub ») : jamais l'email ni `id`.
    sub = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    # Vrai seulement après un clic sur un lien reçu à cette adresse : une appli cliente pourra s'y fier.
    email_verified = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(20), nullable=False, default=Role.ADHERENT)
    # Un compte inactif ne peut pas se connecter.
    active = db.Column(db.Boolean, nullable=False, default=False)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    last_login_at = db.Column(db.DateTime)
    # Double authentification : secret chiffré (cf. services/totp.py), actif seulement une fois `totp_enabled_at` posé.
    totp_secret = db.Column(db.String(255))
    totp_enabled_at = db.Column(db.DateTime)
    totp_last_counter = db.Column(db.Integer)  # dernier code accepté : un code ne sert qu'une fois
    theme = db.Column(db.String(10))  # "light", "dark" ou None (celui du système)
    text_size = db.Column(db.String(10))  # "large", "xlarge" ou None (normale)

    groups = db.relationship("AccessGroup", secondary=account_access_group, back_populates="members",
                             order_by="AccessGroup.name")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def initials(self):
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    @property
    def is_active(self):  # utilisé par Flask-Login
        return self.active

    @property
    def is_admin(self):
        return self.role == Role.ADMIN

    @property
    def totp_enabled(self):
        return self.totp_enabled_at is not None

    @property
    def role_label(self):
        role = db.session.get(Role, self.role)
        return role.name if role else Role.DEFAULT_NAMES.get(self.role, self.role)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)

    # Jeton de la session en cours (voir LoginSession), fixé à la connexion ou au chargement.
    session_token = None

    def get_id(self):  # utilisé par Flask-Login : stocké dans le cookie de session et « rester connecté »
        return f"{self.id}:{self.session_token}"

    def __repr__(self):
        return f"<Account {self.full_name} ({self.role})>"


class LoginSession(db.Model):
    """Connexion ouverte sur un appareil. La supprimer invalide ses cookies, même copiés ailleurs."""
    id = db.Column(db.String(64), primary_key=True)  # jeton secret
    account_id = db.Column(db.ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    last_seen_at = db.Column(db.DateTime)
    user_agent = db.Column(db.String(255))
    ip = db.Column(db.String(45))

    account = db.relationship("Account")


class AccountToken(db.Model):
    """Lien à usage unique envoyé par email : invitation, mot de passe oublié, changement d'email.

    Seule l'empreinte du jeton est stockée : une fuite de la base ne donne aucun lien valable.
    """
    INVITE = "invite"
    RESET = "reset"
    EMAIL = "email"

    id = db.Column(db.String(64), primary_key=True)  # empreinte du jeton
    account_id = db.Column(db.ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    purpose = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(255))  # adresse à confirmer
    # Vrai si le lien n'a circulé que par email : un lien transmis à la main ne prouve pas que l'adresse est la bonne.
    proves_email = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    expires_at = db.Column(db.DateTime, nullable=False)

    account = db.relationship("Account")


class RecoveryCode(db.Model):
    """Code de secours à usage unique, pour un téléphone perdu. Seule son empreinte est stockée."""
    id = db.Column(db.String(64), primary_key=True)  # empreinte du code
    account_id = db.Column(db.ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)


@login_manager.user_loader
def load_user(user_id):
    account_id, _, token = user_id.partition(":")
    login = db.session.get(LoginSession, token) if token else None
    if login is None or str(login.account_id) != account_id:
        return None
    login.account.session_token = token
    from .services.sessions import touch
    touch(login)
    return login.account


class ThrottleEvent(db.Model):
    """Tentative comptée par services/throttle.py. En base, pour que la limite vaille pour tous les workers.

    `key_hash` est une empreinte : le texte d'origine (email saisi, IP) n'est jamais conservé.
    """
    id = db.Column(db.Integer, primary_key=True)
    key_hash = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)


# ---------------------------------------------------------------------------
# Journal, notifications, réglages
# ---------------------------------------------------------------------------

class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"))
    action = db.Column(db.String(50), nullable=False)  # ex. role.update, auth.login
    message = db.Column(db.String(500), nullable=False)
    changes = db.Column(db.JSON)  # {champ: [ancien, nouveau]}
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now(), index=True)
    # Compte concerné par l'action (rôle changé…), à distinguer de son auteur `account_id`.
    target_account_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"), index=True)

    account = db.relationship("Account", foreign_keys=[account_id])
    target_account = db.relationship("Account", foreign_keys=[target_account_id])


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    message = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(255))
    read = db.Column(db.Boolean, nullable=False, default=False)
    # Clé de déduplication pour les relances automatiques : relancer une tâche ne crée pas de doublon.
    dedup_key = db.Column(db.String(120), unique=True)
    emailed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())

    account = db.relationship("Account")


class AppSetting(db.Model):
    """Réglage de l'appli modifiable depuis l'interface (nom, logo, couleur…), stocké en clé / valeur."""
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, nullable=False)

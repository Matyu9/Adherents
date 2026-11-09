"""Modèle de données."""
import hashlib
import hmac
import uuid
from datetime import date, datetime

from authlib.oauth2.rfc6749 import ClientMixin
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
    # Groupes dont chaque compte de ce rôle fait partie, sans les cocher un par un (voir Account.all_groups).
    groups = db.relationship("AccessGroup", secondary="role_access_group", order_by="AccessGroup.name")

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


role_access_group = db.Table(
    "role_access_group",
    db.Column("role_key", db.ForeignKey("role.key", ondelete="CASCADE"), primary_key=True),
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
    def role_groups(self):
        """Groupes donnés par le rôle du compte."""
        role = db.session.get(Role, self.role)
        return list(role.groups) if role is not None else []

    @property
    def all_groups(self):
        """Groupes dont le compte fait vraiment partie : les siens et ceux de son rôle. C'est ce qui ouvre les applis."""
        return sorted(set(self.groups) | set(self.role_groups), key=lambda group: group.name)

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
    # Identifiant public de la session (claim OIDC « sid »), qui peut circuler sans rien donner : jamais `id`.
    sid = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
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
    if not touch(login):  # fermée pour inactivité
        return None
    return login.account


class ThrottleEvent(db.Model):
    """Tentative comptée par services/throttle.py. En base, pour que la limite vaille pour tous les workers.

    `key_hash` est une empreinte : le texte d'origine (email saisi, IP) n'est jamais conservé.
    """
    id = db.Column(db.Integer, primary_key=True)
    key_hash = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)


# ---------------------------------------------------------------------------
# Serveur OIDC
# ---------------------------------------------------------------------------

oidc_client_allowed_group = db.Table(
    "oidc_client_allowed_group",
    db.Column("client_id", db.ForeignKey("oidc_client.id", ondelete="CASCADE"), primary_key=True),
    db.Column("group_id", db.ForeignKey("access_group.id", ondelete="CASCADE"), primary_key=True, index=True),
)
oidc_client_shared_group = db.Table(
    "oidc_client_shared_group",
    db.Column("client_id", db.ForeignKey("oidc_client.id", ondelete="CASCADE"), primary_key=True),
    db.Column("group_id", db.ForeignKey("access_group.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class OidcClient(TimestampMixin, ClientMixin, db.Model):
    """Appli qui délègue sa connexion à ce serveur. Les méthodes `check_*` et `get_*` sont celles qu'appelle Authlib."""
    SCOPES = ("openid", "profile", "email", "groups")
    AUTH_METHODS = ("client_secret_basic", "client_secret_post")

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(64), nullable=False, unique=True)
    secret_hash = db.Column(db.String(64), nullable=False)  # empreinte : le secret n'est affiché qu'une fois
    name = db.Column(db.String(80), nullable=False, unique=True)
    redirect_uris = db.Column(db.JSON, nullable=False, default=list)
    # Adresses où renvoyer la personne après une déconnexion demandée par l'appli, comparées à l'identique.
    post_logout_redirect_uris = db.Column(db.JSON, nullable=False, default=list, server_default="[]")
    # Adresse où l'appli reçoit les avis de déconnexion (Back-Channel Logout). Sans elle, elle n'est pas prévenue.
    backchannel_logout_uri = db.Column(db.Text)
    # Appli de l'association : la personne n'a pas à confirmer ce que l'association lui transmet déjà.
    first_party = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    # Peut lire la liste des comptes qui y ont accès (/oidc/comptes), avec son identifiant et son secret.
    directory_access = db.Column(db.Boolean, nullable=False, default=False, server_default="0")

    # Aucun groupe autorisé : personne n'entre. L'accès s'accorde, il n'est jamais ouvert par défaut.
    allowed_groups = db.relationship("AccessGroup", secondary=oidc_client_allowed_group, order_by="AccessGroup.name")
    shared_groups = db.relationship("AccessGroup", secondary=oidc_client_shared_group, order_by="AccessGroup.name")

    def get_client_id(self):
        return self.client_id

    def get_default_redirect_uri(self):
        return None  # l'adresse de retour est toujours demandée explicitement

    def get_allowed_scope(self, scope):
        return " ".join(s for s in self.SCOPES if s in (scope or "").split())

    def check_redirect_uri(self, redirect_uri):
        return redirect_uri in self.redirect_uris  # à l'identique : ni préfixe, ni joker

    def check_client_secret(self, client_secret):
        return hmac.compare_digest(hashlib.sha256(client_secret.encode()).hexdigest(), self.secret_hash)

    def check_endpoint_auth_method(self, method, endpoint):
        return endpoint == "token" and method in self.AUTH_METHODS

    def check_response_type(self, response_type):
        return response_type == "code"

    def check_grant_type(self, grant_type):
        return grant_type == "authorization_code"


class OidcCode(db.Model):
    """Code d'autorisation : stocké en empreinte, à usage unique. `used_at` permet de reconnaître un code rejoué."""
    id = db.Column(db.String(64), primary_key=True)  # empreinte du code
    client_id = db.Column(db.ForeignKey("oidc_client.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    redirect_uri = db.Column(db.Text, nullable=False)
    scope = db.Column(db.String(255), nullable=False)
    nonce = db.Column(db.Text)
    code_challenge = db.Column(db.String(128), nullable=False)
    code_challenge_method = db.Column(db.String(10), nullable=False)
    auth_time = db.Column(db.Integer, nullable=False)
    sid = db.Column(db.String(36))  # session de connexion qui a obtenu le code (LoginSession.sid)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime)

    account = db.relationship("Account")
    client = db.relationship("OidcClient")

    def get_redirect_uri(self):
        return self.redirect_uri

    def get_scope(self):
        return self.scope

    def get_nonce(self):
        return self.nonce

    def get_auth_time(self):
        return self.auth_time

    def get_acr(self):
        return None

    def get_amr(self):
        return None


class OidcToken(db.Model):
    """Jeton d'accès opaque, stocké en empreinte : il ne sert qu'à /userinfo, qui revérifie les droits à chaque appel."""
    id = db.Column(db.String(64), primary_key=True)  # empreinte du jeton
    client_id = db.Column(db.ForeignKey("oidc_client.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    code_id = db.Column(db.ForeignKey("oidc_code.id", ondelete="SET NULL"), index=True)
    scope = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)

    account = db.relationship("Account")
    client = db.relationship("OidcClient")


class OidcConsent(db.Model):
    """Ce qu'une personne a accepté de transmettre à une appli. Elle peut le retirer depuis « Mon compte »."""
    account_id = db.Column(db.ForeignKey("account.id", ondelete="CASCADE"), primary_key=True)
    client_id = db.Column(db.ForeignKey("oidc_client.id", ondelete="CASCADE"), primary_key=True, index=True)
    scope = db.Column(db.String(255), nullable=False)
    granted_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    client = db.relationship("OidcClient")


class OidcSession(db.Model):
    """Appli où une session de connexion a servi : elle sera prévenue quand cette session se fermera.

    Pas de clé étrangère vers LoginSession : la ligne doit survivre à la session, le temps de prévenir l'appli.
    """
    __table_args__ = (db.UniqueConstraint("sid", "client_id"),)

    id = db.Column(db.Integer, primary_key=True)
    sid = db.Column(db.String(36), nullable=False, index=True)
    client_id = db.Column(db.ForeignKey("oidc_client.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    # Groupes transmis à la connexion, None si l'appli ne les a pas demandés : s'ils changent, l'appli est prévenue.
    groups = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    account = db.relationship("Account")
    client = db.relationship("OidcClient")


class OidcLogout(db.Model):
    """Avis de déconnexion à envoyer à une appli (Back-Channel Logout), réessayé tant qu'elle ne l'a pas reçu.

    `sub` et `sid` sont recopiés : l'avis doit pouvoir partir même si la session ou le compte n'existent plus.
    """
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.ForeignKey("oidc_client.id", ondelete="CASCADE"), nullable=False, index=True)
    sub = db.Column(db.String(36), nullable=False)
    sid = db.Column(db.String(36))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    next_attempt_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    last_error = db.Column(db.String(255))

    client = db.relationship("OidcClient")


class OidcKey(db.Model):
    """Clé de signature des jetons d'identité. Retirée, elle reste publiée sans sa partie privée, le temps que ses jetons expirent."""
    kid = db.Column(db.String(64), primary_key=True)
    public_jwk = db.Column(db.JSON, nullable=False)
    private_key = db.Column(db.Text)  # chiffrée avec une clé dérivée de SECRET_KEY, effacée au retrait
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    retired_at = db.Column(db.DateTime)


# ---------------------------------------------------------------------------
# Annuaire : personnes, foyers, saisons (voir docs/annuaire.md)
# ---------------------------------------------------------------------------

def _uuid():
    return str(uuid.uuid4())


class AddressMixin:
    """Adresse postale. Celle d'une personne reste vide en général : on prend celle de son foyer."""
    address_recipient = db.Column(db.String(120))  # destinataire, s'il diffère du nom
    address_street = db.Column(db.Text)
    postal_code = db.Column(db.String(10))
    city = db.Column(db.String(80))
    country = db.Column(db.String(2), nullable=False, default="FR", server_default="FR")

    @property
    def has_address(self):
        return bool(self.address_street or self.postal_code or self.city)


class Person(AddressMixin, TimestampMixin, db.Model):
    """Une personne, quel que soit le nombre de ses rôles : élève, responsable, professeur."""
    CIVILITIES = ("M.", "Mme")
    SEXES = {"F": "Féminin", "M": "Masculin"}

    id = db.Column(db.Integer, primary_key=True)
    # Identifiant public : c'est lui qui sortira de l'appli (Inventaire, phase 7), jamais `id`.
    uuid = db.Column(db.String(36), nullable=False, unique=True, default=_uuid)
    civility = db.Column(db.String(10))
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False, index=True)
    birth_name = db.Column(db.String(80))
    sex = db.Column(db.String(1))
    birth_date = db.Column(db.Date)
    birth_place = db.Column(db.String(120))  # réservé aux gestionnaires (directory.edit)
    # Adresse propre à la personne, jamais celle d'un parent recopiée : non unique, un couple peut la partager.
    email = db.Column(db.String(255), index=True)
    emergency_contact = db.Column(db.String(255))
    notes = db.Column(db.Text)
    archived_at = db.Column(db.DateTime)
    # Compte de connexion rattaché (0 ou 1), porté par la fiche : un enfant a une fiche et pas de compte.
    account_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"), unique=True)

    account = db.relationship("Account", backref=db.backref("person", uselist=False))
    phones = db.relationship("Phone", back_populates="person", cascade="all, delete-orphan",
                             order_by="(Phone.position, Phone.id)")
    external_refs = db.relationship("ExternalRef", back_populates="person", cascade="all, delete-orphan")
    household_links = db.relationship("HouseholdMember", back_populates="person", cascade="all, delete-orphan")
    memberships = db.relationship("Membership", back_populates="person", cascade="all, delete-orphan",
                                  order_by="Membership.season_id.desc()")
    teacher = db.relationship("Teacher", back_populates="person", uselist=False, cascade="all, delete-orphan")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def sort_name(self):
        return f"{self.last_name.upper()} {self.first_name}"

    def age(self, on=None):
        if self.birth_date is None:
            return None
        on = on or date.today()
        return on.year - self.birth_date.year - ((on.month, on.day) < (self.birth_date.month, self.birth_date.day))

    @property
    def is_minor(self):
        age = self.age()
        return age is not None and age < 18

    def __repr__(self):
        return f"<Person {self.full_name}>"


class Household(AddressMixin, TimestampMixin, db.Model):
    """Un foyer : le payeur. Les cotisations, l'adresse postale et les IBAN s'y rattachent."""
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), nullable=False, unique=True, default=_uuid)
    name = db.Column(db.String(120), nullable=False, index=True)
    notes = db.Column(db.Text)
    archived_at = db.Column(db.DateTime)

    members = db.relationship("HouseholdMember", back_populates="household", cascade="all, delete-orphan",
                              order_by="(HouseholdMember.role.desc(), HouseholdMember.guardian_rank)")
    phones = db.relationship("Phone", back_populates="household", cascade="all, delete-orphan",
                             order_by="(Phone.position, Phone.id)")
    external_refs = db.relationship("ExternalRef", back_populates="household", cascade="all, delete-orphan")
    bank_accounts = db.relationship("BankAccount", back_populates="household", cascade="all, delete-orphan",
                                    order_by="BankAccount.id")

    @property
    def guardians(self):
        return [m for m in self.members if m.role == HouseholdMember.GUARDIAN]

    @property
    def dependents(self):
        return [m for m in self.members if m.role == HouseholdMember.DEPENDENT]

    def __repr__(self):
        return f"<Household {self.name}>"


class HouseholdMember(db.Model):
    """Appartenance d'une personne à un foyer : responsable 1 ou 2, ou à charge."""
    GUARDIAN = "guardian"
    DEPENDENT = "dependent"
    ROLES = {GUARDIAN: "Responsable", DEPENDENT: "À charge"}
    __table_args__ = (
        db.UniqueConstraint("household_id", "guardian_rank"),  # un seul responsable 1, un seul responsable 2
        db.CheckConstraint("(role = 'guardian' AND guardian_rank IS NOT NULL AND guardian_rank IN (1, 2)) OR "
                           "(role = 'dependent' AND guardian_rank IS NULL)", name="ck_household_member_role"),
    )

    household_id = db.Column(db.ForeignKey("household.id", ondelete="CASCADE"), primary_key=True)
    person_id = db.Column(db.ForeignKey("person.id", ondelete="CASCADE"), primary_key=True, index=True)
    role = db.Column(db.String(10), nullable=False)
    guardian_rank = db.Column(db.Integer)
    relationship = db.Column(db.String(40))  # lien juridique : mère, père, tuteur, « lui-même »…

    household = db.relationship("Household", back_populates="members")
    person = db.relationship("Person", back_populates="household_links")

    @property
    def label(self):
        return f"Responsable {self.guardian_rank}" if self.role == self.GUARDIAN else "À charge"


class Phone(db.Model):
    """Téléphone d'une personne ou d'un foyer, jamais des deux."""
    __table_args__ = (
        db.CheckConstraint("(person_id IS NULL) <> (household_id IS NULL)", name="ck_phone_owner"),
    )

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.ForeignKey("person.id", ondelete="CASCADE"), index=True)
    household_id = db.Column(db.ForeignKey("household.id", ondelete="CASCADE"), index=True)
    number = db.Column(db.String(20), nullable=False)  # format international : +33…
    label = db.Column(db.String(40))
    position = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    person = db.relationship("Person", back_populates="phones")
    household = db.relationship("Household", back_populates="phones")


class ExternalRef(db.Model):
    """Numéro d'une fiche dans le logiciel de gestion : ce qui rend l'import rejouable sans doublons."""
    KINDS = {"student": "N° élève", "household": "N° famille", "teacher": "N° professeur"}
    __table_args__ = (
        db.UniqueConstraint("kind", "value"),
        db.CheckConstraint("(person_id IS NULL) <> (household_id IS NULL)", name="ck_external_ref_owner"),
        db.CheckConstraint("kind IN ('student', 'household', 'teacher')", name="ck_external_ref_kind"),
    )

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    value = db.Column(db.String(40), nullable=False)
    person_id = db.Column(db.ForeignKey("person.id", ondelete="CASCADE"), index=True)
    household_id = db.Column(db.ForeignKey("household.id", ondelete="CASCADE"), index=True)

    person = db.relationship("Person", back_populates="external_refs")
    household = db.relationship("Household", back_populates="external_refs")


class Season(db.Model):
    """Saison (« 2026-2027 »). La saison en cours se choisit dans l'annuaire, elle n'est pas déduite de la date."""
    __table_args__ = (db.CheckConstraint("ends_on > starts_on", name="ck_season_dates"),)

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(20), nullable=False, unique=True)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)

    def __repr__(self):
        return f"<Season {self.label}>"


class Membership(TimestampMixin, db.Model):
    """Adhésion d'une personne, comme élève, pour une saison. Jamais supprimée : c'est l'historique."""
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    STATUSES = {ACTIVE: "Active", WITHDRAWN: "Abandon"}
    __table_args__ = (
        db.UniqueConstraint("person_id", "season_id"),
        db.CheckConstraint("status IN ('active', 'withdrawn')", name="ck_membership_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.ForeignKey("person.id", ondelete="CASCADE"), nullable=False)
    # Une saison qui a des adhésions ne se supprime pas.
    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default=ACTIVE)
    withdrawn_on = db.Column(db.Date)
    category = db.Column(db.String(40))  # scolaire, non scolaire, adulte…
    school_level = db.Column(db.String(40))
    school_class = db.Column(db.String(40))

    person = db.relationship("Person", back_populates="memberships")
    season = db.relationship("Season")


class Teacher(db.Model):
    """Profil de professeur. Les droits dans l'appli passent par le compte, pas par cette table."""
    STATUSES = ("CDI", "CDD", "Vacataire", "Bénévole", "Autre")

    person_id = db.Column(db.ForeignKey("person.id", ondelete="CASCADE"), primary_key=True)
    status = db.Column(db.String(20))
    started_on = db.Column(db.Date)
    left_on = db.Column(db.Date)  # un départ n'efface pas la fiche
    work_email = db.Column(db.String(255))
    notes = db.Column(db.Text)

    person = db.relationship("Person", back_populates="teacher")

    @property
    def active(self):
        return self.left_on is None or self.left_on > date.today()


class BankAccount(TimestampMixin, db.Model):
    """Coordonnées bancaires d'un foyer. L'IBAN est chiffré (services/bank.py) : seuls ses 4 derniers caractères
    sont en clair, et son empreinte sert à repérer un même IBAN sans rien déchiffrer."""
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.ForeignKey("household.id", ondelete="CASCADE"), nullable=False, index=True)
    holder_person_id = db.Column(db.ForeignKey("person.id", ondelete="SET NULL"), index=True)
    holder_name = db.Column(db.String(120), nullable=False)
    iban_ciphertext = db.Column(db.LargeBinary, nullable=False)
    iban_last4 = db.Column(db.String(4), nullable=False)
    iban_fingerprint = db.Column(db.String(64), nullable=False, index=True)
    bic = db.Column(db.String(11))
    key_version = db.Column(db.Integer, nullable=False)

    household = db.relationship("Household", back_populates="bank_accounts")
    holder = db.relationship("Person")
    mandates = db.relationship("SepaMandate", back_populates="bank_account", cascade="all, delete-orphan",
                               order_by="SepaMandate.id")

    @property
    def masked_iban(self):
        return f"•••• •••• {self.iban_last4}"


class SepaMandate(db.Model):
    """Mandat de prélèvement SEPA. Le document signé n'est pas conservé ici."""
    id = db.Column(db.Integer, primary_key=True)
    bank_account_id = db.Column(db.ForeignKey("bank_account.id", ondelete="CASCADE"), nullable=False, index=True)
    rum = db.Column(db.String(35), nullable=False, unique=True)  # référence unique du mandat
    signed_on = db.Column(db.Date, nullable=False)
    first_debit_on = db.Column(db.Date)
    last_debit_on = db.Column(db.Date)
    revoked_on = db.Column(db.Date)

    bank_account = db.relationship("BankAccount", back_populates="mandates")

    @property
    def active(self):
        return self.revoked_on is None


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
    # Fiche de l'annuaire concernée, pour retrouver son historique.
    person_id = db.Column(db.ForeignKey("person.id", ondelete="SET NULL"), index=True)
    household_id = db.Column(db.ForeignKey("household.id", ondelete="SET NULL"), index=True)

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

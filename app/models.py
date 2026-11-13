"""Modèle de données."""

import hashlib
import hmac
import uuid
from datetime import date, datetime, timedelta

from authlib.oauth2.rfc6749 import ClientMixin
from flask_login import UserMixin
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager

# Clé de session de la vue adhérent (voir Account.member_view).
MEMBER_VIEW_KEY = "vue_adherent"


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.now, server_default=func.now(), onupdate=datetime.now
    )


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
    # Empreinte du jeton de l'agenda à s'abonner (.ics) ; le jeton lui-même n'est montré qu'une fois.
    calendar_token = db.Column(db.String(64), unique=True)
    text_size = db.Column(db.String(10))  # "large", "xlarge" ou None (normale)

    groups = db.relationship(
        "AccessGroup", secondary=account_access_group, back_populates="members", order_by="AccessGroup.name"
    )

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
        return self.role == Role.ADMIN and not self.member_view

    @property
    def member_view(self):
        """Vue adhérent activée par ce compte pour sa session : ses droits sont alors ceux d'un adhérent."""
        from flask import has_request_context, session

        return has_request_context() and session.get(MEMBER_VIEW_KEY) == self.id

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
    anonymized_at = db.Column(db.DateTime)  # fiche vidée de ce qui identifie la personne (services/retention.py)
    # Compte de connexion rattaché (0 ou 1), porté par la fiche : un enfant a une fiche et pas de compte.
    account_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"), unique=True)

    account = db.relationship("Account", backref=db.backref("person", uselist=False))
    phones = db.relationship(
        "Phone", back_populates="person", cascade="all, delete-orphan", order_by="(Phone.position, Phone.id)"
    )
    external_refs = db.relationship("ExternalRef", back_populates="person", cascade="all, delete-orphan")
    household_links = db.relationship("HouseholdMember", back_populates="person", cascade="all, delete-orphan")
    memberships = db.relationship(
        "Membership", back_populates="person", cascade="all, delete-orphan", order_by="Membership.season_id.desc()"
    )
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

    members = db.relationship(
        "HouseholdMember",
        back_populates="household",
        cascade="all, delete-orphan",
        order_by="(HouseholdMember.role.desc(), HouseholdMember.guardian_rank)",
    )
    phones = db.relationship(
        "Phone", back_populates="household", cascade="all, delete-orphan", order_by="(Phone.position, Phone.id)"
    )
    external_refs = db.relationship("ExternalRef", back_populates="household", cascade="all, delete-orphan")
    bank_accounts = db.relationship(
        "BankAccount", back_populates="household", cascade="all, delete-orphan", order_by="BankAccount.id"
    )

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
        db.CheckConstraint(
            "(role = 'guardian' AND guardian_rank IS NOT NULL AND guardian_rank IN (1, 2)) OR "
            "(role = 'dependent' AND guardian_rank IS NULL)",
            name="ck_household_member_role",
        ),
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

    __table_args__ = (db.CheckConstraint("(person_id IS NULL) <> (household_id IS NULL)", name="ck_phone_owner"),)

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
    # Reprise et fin des cours ; vides : les dates de la saison. Voir docs/cours.md.
    lessons_from = db.Column(db.Date)
    lessons_until = db.Column(db.Date)
    # En préparation : invisible pour qui n'a pas accès au catalogue (professeurs, familles), le temps de la préparer.
    hidden = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    breaks = db.relationship(
        "SeasonBreak", back_populates="season", cascade="all, delete-orphan", order_by="SeasonBreak.starts_on"
    )

    @property
    def lessons_window(self):
        return self.lessons_from or self.starts_on, self.lessons_until or self.ends_on

    def __repr__(self):
        return f"<Season {self.label}>"


class SeasonBreak(db.Model):
    """Période sans cours d'une saison : jour férié (calculé sur demande), vacances (saisies ou importées d'un
    fichier) ou fermeture. Avec « cours maintenus », elle reste affichée dans les calendriers sans retirer de séance."""

    HOLIDAY, VACATION, CLOSURE = "ferie", "vacances", "fermeture"
    KINDS = {HOLIDAY: "Jour férié", VACATION: "Vacances", CLOSURE: "Fermeture"}

    __table_args__ = (db.CheckConstraint("ends_on >= starts_on", name="ck_season_break_dates"),)

    id = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.ForeignKey("season.id", ondelete="CASCADE"), nullable=False, index=True)
    label = db.Column(db.String(80), nullable=False)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)
    kind = db.Column(db.String(12), nullable=False, default=CLOSURE, server_default=CLOSURE)
    keep_lessons = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    @property
    def kind_label(self):
        return self.KINDS.get(self.kind, self.kind)

    season = db.relationship("Season", back_populates="breaks")

    def covers(self, day):
        return self.starts_on <= day <= self.ends_on


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
    # Étiquettes de l'élève pour les tarifs de la saison (« personnel », « boursier »…), voir docs/tarifs.md.
    fee_tags = db.Column(db.JSON, nullable=False, default=list, server_default="[]")

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
    notes = db.Column(db.Text)  # internes : jamais montrées aux familles
    # Présentation et photo : visibles des familles de ses élèves.
    bio = db.Column(db.Text)
    photo = db.Column(db.String(60))  # chemin relatif dans UPLOAD_DIR (« profs/<uuid>.jpg »)
    diplomas = db.Column(db.Text)  # DE, CA… internes
    # Créneaux où il peut donner cours, pour préparer la saison : [{"day": 0-6, "from": "HH:MM", "to": "HH:MM"}].
    availability = db.Column(db.JSON, nullable=False, default=list, server_default="[]")

    person = db.relationship("Person", back_populates="teacher")

    @property
    def active(self):
        return self.left_on is None or self.left_on > date.today()

    def teaches_during(self, season):
        """Arrivé avant la fin de la saison et pas parti avant son début : on peut lui confier un cours."""
        return (self.started_on is None or self.started_on <= season.ends_on) and (
            self.left_on is None or self.left_on > season.starts_on
        )

    def leaves_during(self, season):
        """Parti avant la fin de la saison : ses cours de cette saison sont à reprendre."""
        return self.left_on is not None and self.left_on <= season.ends_on


# ---------------------------------------------------------------------------
# Catalogue : disciplines, niveaux, lieux, cours (voir docs/catalogue.md)
# ---------------------------------------------------------------------------


class ArchivableMixin:
    """Une valeur du catalogue qui a servi ne se supprime pas : on l'archive, et elle n'est plus proposée."""

    archived_at = db.Column(db.DateTime)

    @property
    def archived(self):
        return self.archived_at is not None


class DisciplineType(ArchivableMixin, db.Model):
    """Type de discipline (« Instrument », « Atelier », « Sport collectif »…), choisi par l'association : il regroupe
    les disciplines dans les listes."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False, unique=True)
    position = db.Column(db.Integer, nullable=False, default=0, server_default="0")  # ordre dans les listes

    def __repr__(self):
        return f"<DisciplineType {self.name}>"


class Discipline(ArchivableMixin, db.Model):
    """Discipline enseignée (« Flûte traversière », « Formation musicale »…), commune à toutes les saisons."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    # Facultatif. Un type qui a des disciplines ne se supprime pas.
    type_id = db.Column(db.ForeignKey("discipline_type.id", ondelete="RESTRICT"), index=True)

    type = db.relationship("DisciplineType")

    def __repr__(self):
        return f"<Discipline {self.name}>"


class Level(ArchivableMixin, db.Model):
    """Niveau (« Cycle 1 - 2e année », « Débutant »…), commun à toutes les disciplines et saisons."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False, unique=True)
    position = db.Column(db.Integer, nullable=False, default=0, server_default="0")  # ordre dans les listes

    def __repr__(self):
        return f"<Level {self.name}>"


class Site(ArchivableMixin, db.Model):
    """Lieu d'enseignement : l'école, une salle municipale, un collège…"""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    address = db.Column(db.String(255))
    # Identifiant du bâtiment dans l'agenda de l'organisation (Google Workspace), s'il vient de là.
    external_id = db.Column(db.String(255), unique=True)

    rooms = db.relationship("Room", back_populates="site", order_by="Room.name")

    def __repr__(self):
        return f"<Site {self.name}>"


class Room(ArchivableMixin, db.Model):
    __table_args__ = (db.UniqueConstraint("site_id", "name"),)

    id = db.Column(db.Integer, primary_key=True)
    # Un site qui a des salles ne se supprime pas.
    site_id = db.Column(db.ForeignKey("site.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    # Adresse de l'agenda de la salle (ressource Google Workspace, « …@resource.calendar.google.com »).
    calendar_id = db.Column(db.String(255), unique=True)
    capacity = db.Column(db.Integer)  # places, d'après l'agenda de l'organisation ou saisies

    site = db.relationship("Site", back_populates="rooms")

    @property
    def label(self):
        return f"{self.name} ({self.site.name})"

    def __repr__(self):
        return f"<Room {self.label}>"


class Course(TimestampMixin, db.Model):
    """Cours d'une saison : un créneau d'un professeur, pour une discipline et un niveau.

    Un cours individuel regroupe plusieurs élèves à des heures de passage différentes : l'horaire est celui du créneau.
    """

    WEEKDAYS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
    # Couleurs des plannings : des noms, rendus par des classes CSS (thèmes clair et sombre), jamais un style en ligne.
    COLORS = {
        "rouge": "Rouge",
        "orange": "Orange",
        "jaune": "Jaune",
        "vert": "Vert",
        "sarcelle": "Sarcelle",
        "bleu": "Bleu",
        "indigo": "Indigo",
        "violet": "Violet",
        "rose": "Rose",
        "brun": "Brun",
    }
    __table_args__ = (
        db.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_course_weekday"),
        db.CheckConstraint("(starts_at IS NULL) = (ends_at IS NULL)", name="ck_course_times_together"),
        db.CheckConstraint("ends_at > starts_at", name="ck_course_times_order"),
        db.CheckConstraint("capacity > 0", name="ck_course_capacity"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Une saison, une discipline, un niveau ou une salle qui ont des cours ne se suppriment pas.
    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), nullable=False, index=True)
    discipline_id = db.Column(db.ForeignKey("discipline.id", ondelete="RESTRICT"), nullable=False, index=True)
    level_id = db.Column(db.ForeignKey("level.id", ondelete="RESTRICT"), index=True)
    # Vide : professeur à trouver. Un profil de professeur retiré (erreur de saisie) libère ses cours.
    teacher_id = db.Column(db.ForeignKey("teacher.person_id", ondelete="SET NULL"), index=True)
    room_id = db.Column(db.ForeignKey("room.id", ondelete="RESTRICT"), index=True)
    group_label = db.Column(db.String(80))  # « groupe A », « ados »…
    weekday = db.Column(db.SmallInteger)  # 0 = lundi
    starts_at = db.Column(db.Time)
    ends_at = db.Column(db.Time)
    capacity = db.Column(db.Integer)  # effectif maximum ; vide : pas de limite
    color = db.Column(db.String(12))  # une clé de COLORS ; vide : choisie d'après la discipline
    # Cours individuel : chaque élève a son heure de passage et sa durée dans le créneau. Voir docs/cours.md.
    individual = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # Fermé aux inscriptions à la main. Les inscriptions (phase 6) le rendront complet aussi une fois l'effectif atteint.
    marked_full = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    notes = db.Column(db.Text)
    # Élément de cotisation facturé à chaque élève du cours (phase 9), de la même saison. Vide : rien de facturé.
    fee_item_id = db.Column(db.ForeignKey("fee_item.id", ondelete="RESTRICT"), index=True)

    season = db.relationship("Season")
    discipline = db.relationship("Discipline")
    level = db.relationship("Level")
    teacher = db.relationship("Teacher", backref=db.backref("courses", passive_deletes=True))
    room = db.relationship("Room")
    fee_item = db.relationship("FeeItem")

    @property
    def weekday_name(self):
        return self.WEEKDAYS[self.weekday] if self.weekday is not None else None

    @property
    def color_key(self):
        """La couleur choisie, sinon une couleur stable tirée de la discipline (ses cours se ressemblent)."""
        if self.color in self.COLORS:
            return self.color
        keys = list(self.COLORS)
        discipline_id = self.discipline_id or (self.discipline.id if self.discipline is not None else 0)
        return keys[(discipline_id or 0) % len(keys)]

    @property
    def title(self):
        parts = [self.discipline.name]
        if self.level is not None:
            parts.append(self.level.name)
        if self.group_label:
            parts.append(self.group_label)
        return " · ".join(parts)

    def is_full(self, enrolled=0):
        """Complet : fermé à la main, ou effectif maximum atteint par `enrolled` inscrits."""
        return self.marked_full or (self.capacity is not None and enrolled >= self.capacity)

    def overlaps(self, other):
        """Même jour, horaires qui se chevauchent (un cours qui finit à 18 h et un qui commence à 18 h ne se gênent pas)."""
        return (
            self.weekday is not None
            and self.weekday == other.weekday
            and self.starts_at is not None
            and other.starts_at is not None
            and self.starts_at < other.ends_at
            and other.starts_at < self.ends_at
        )

    def __repr__(self):
        return f"<Course {self.id}>"


class Lesson(db.Model):
    """Séance d'un cours, à une date. Générée depuis le créneau du cours, ou ajoutée (rattrapage).

    Une séance modifiée à la main (`edited`) n'est plus touchée par une nouvelle génération. `previous` garde l'état
    d'avant la dernière modification, pour que le secrétariat puisse revenir en arrière. `revision` augmente à chaque
    changement : c'est le SEQUENCE des agendas. Voir docs/cours.md.
    """

    PLANNED = "planned"
    CANCELLED = "cancelled"
    STATUSES = {PLANNED: "Prévue", CANCELLED: "Annulée"}
    __table_args__ = (
        db.UniqueConstraint("course_id", "scheduled_on"),
        db.CheckConstraint("ends_at > starts_at", name="ck_lesson_times"),
        db.CheckConstraint("status IN ('planned', 'cancelled')", name="ck_lesson_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.ForeignKey("course.id", ondelete="CASCADE"), nullable=False, index=True)
    # Date du créneau régulier dont la séance vient ; vide pour une séance ajoutée.
    scheduled_on = db.Column(db.Date)
    day = db.Column(db.Date, nullable=False, index=True)
    starts_at = db.Column(db.Time, nullable=False)
    ends_at = db.Column(db.Time, nullable=False)
    room_id = db.Column(db.ForeignKey("room.id", ondelete="SET NULL"), index=True)
    substitute_id = db.Column(db.ForeignKey("teacher.person_id", ondelete="SET NULL"), index=True)
    status = db.Column(db.String(10), nullable=False, default=PLANNED)
    reason = db.Column(db.String(200))  # motif d'annulation ou de déplacement, montré aux familles
    edited = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    previous = db.Column(db.JSON)
    changed_by_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"))
    changed_at = db.Column(db.DateTime)
    revision = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    course = db.relationship("Course", backref=db.backref("lessons", passive_deletes=True, order_by="Lesson.day"))
    room = db.relationship("Room")
    substitute = db.relationship("Teacher")
    changed_by = db.relationship("Account")

    @property
    def extra(self):
        return self.scheduled_on is None

    @property
    def cancelled(self):
        return self.status == self.CANCELLED

    @property
    def teacher(self):
        """Qui fait la séance : le remplaçant s'il y en a un, sinon le professeur du cours."""
        return self.substitute or self.course.teacher

    def starts(self):
        return datetime.combine(self.day, self.starts_at)

    def ends(self):
        return datetime.combine(self.day, self.ends_at)

    def __repr__(self):
        return f"<Lesson {self.course_id} {self.day}>"


class Attendance(db.Model):
    """Présence d'un élève à une séance. Pas de motif en texte libre : il révélerait vite des données de santé.
    Voir docs/cours.md."""

    PRESENT = "present"
    ABSENT = "absent"
    EXCUSED = "excused"
    LATE = "late"  # venu, mais en retard : compte comme une présence
    STATUSES = {PRESENT: "Présent", ABSENT: "Absent", EXCUSED: "Excusé", LATE: "Retard"}
    ATTENDED = (PRESENT, LATE)
    __table_args__ = (
        db.UniqueConstraint("lesson_id", "enrollment_id"),
        db.CheckConstraint("status IN ('present', 'absent', 'excused', 'late')", name="ck_attendance_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.ForeignKey("lesson.id", ondelete="CASCADE"), nullable=False, index=True)
    enrollment_id = db.Column(db.ForeignKey("enrollment.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False)
    recorded_by_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"))
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    lesson = db.relationship("Lesson", backref=db.backref("attendance", passive_deletes=True))
    enrollment = db.relationship("Enrollment", backref=db.backref("attendance", passive_deletes=True))
    recorded_by = db.relationship("Account")

    @property
    def label(self):
        return self.STATUSES[self.status]


class AbsenceNotice(db.Model):
    """Absence prévenue à l'avance par la famille pour une séance. À l'appel, l'élève est proposé « excusé »."""

    __table_args__ = (db.UniqueConstraint("lesson_id", "enrollment_id"),)

    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.ForeignKey("lesson.id", ondelete="CASCADE"), nullable=False, index=True)
    enrollment_id = db.Column(db.ForeignKey("enrollment.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    lesson = db.relationship("Lesson", backref=db.backref("absence_notices", passive_deletes=True))
    enrollment = db.relationship("Enrollment", backref=db.backref("absence_notices", passive_deletes=True))
    account = db.relationship("Account")


class RoomCalendarJob(db.Model):
    """Envoi en attente vers l'agenda d'une salle : créer ou mettre à jour l'événement d'une séance (`put`), ou le
    retirer (`delete`). Réessayé par `flask run-tasks` jusqu'à réussite. Voir docs/cours.md."""

    PUT = "put"
    DELETE = "delete"
    __table_args__ = (db.CheckConstraint("action IN ('put', 'delete')", name="ck_room_calendar_job_action"),)

    id = db.Column(db.Integer, primary_key=True)
    calendar_id = db.Column(db.String(255), nullable=False)
    event_id = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(10), nullable=False)
    payload = db.Column(db.JSON)
    attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    next_try_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    last_error = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)


class TeacherDiscipline(db.Model):
    """Discipline enseignée par un professeur pendant une saison."""

    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), primary_key=True)
    teacher_id = db.Column(db.ForeignKey("teacher.person_id", ondelete="CASCADE"), primary_key=True, index=True)
    discipline_id = db.Column(db.ForeignKey("discipline.id", ondelete="RESTRICT"), primary_key=True, index=True)

    season = db.relationship("Season")
    teacher = db.relationship(
        "Teacher", backref=db.backref("taught", cascade="all, delete-orphan", passive_deletes=True)
    )
    discipline = db.relationship("Discipline")


# ---------------------------------------------------------------------------
# Inscriptions : campagnes, dossiers, inscriptions aux cours, consentements (voir docs/inscriptions.md)
# ---------------------------------------------------------------------------


class Campaign(TimestampMixin, db.Model):
    """Campagne d'inscription d'une saison : les familles déposent leur dossier entre ces deux dates."""

    __table_args__ = (db.CheckConstraint("closes_at > opens_at", name="ck_campaign_dates"),)

    id = db.Column(db.Integer, primary_key=True)
    # Une saison qui a des campagnes ne se supprime pas.
    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False)
    opens_at = db.Column(db.DateTime, nullable=False)
    closes_at = db.Column(db.DateTime, nullable=False)
    allow_new = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())  # formulaire public
    instructions = db.Column(db.Text)  # affichées en tête du dossier

    season = db.relationship("Season")
    consents = db.relationship(
        "CampaignConsent",
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="(CampaignConsent.position, CampaignConsent.id)",
    )

    def is_open(self, now=None):
        now = now or datetime.now()
        return self.opens_at <= now < self.closes_at

    def __repr__(self):
        return f"<Campaign {self.title}>"


class CampaignConsent(db.Model):
    """Consentement demandé dans une campagne : droit à l'image, règlement intérieur… Son texte est recopié dans
    chaque réponse, pour garder la preuve de ce qui a été accepté même s'il change ensuite."""

    STUDENT = "student"
    HOUSEHOLD = "household"
    SCOPES = {STUDENT: "Pour chaque élève", HOUSEHOLD: "Pour le foyer"}
    __table_args__ = (db.CheckConstraint("scope IN ('student', 'household')", name="ck_campaign_consent_scope"),)

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.ForeignKey("campaign.id", ondelete="CASCADE"), nullable=False, index=True)
    label = db.Column(db.String(120), nullable=False)
    text = db.Column(db.Text, nullable=False)
    scope = db.Column(db.String(10), nullable=False, default=STUDENT)
    required = db.Column(db.Boolean, nullable=False, default=False)  # sans lui, le dossier ne part pas
    position = db.Column(db.Integer, nullable=False, default=0)

    campaign = db.relationship("Campaign", back_populates="consents")


class Dossier(TimestampMixin, db.Model):
    """Dossier d'inscription d'un foyer pour une campagne.

    Ce que la famille saisit reste dans `data` (JSON) : l'annuaire ne change qu'à la validation, par un gestionnaire.
    Une nouvelle famille (formulaire public) n'a pas encore de foyer : `household_id` est vide jusque-là.
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    TO_FIX = "to_fix"
    VALIDATED = "validated"
    TRANSFERRED = "transferred"
    STATUSES = {
        DRAFT: "Brouillon",
        SUBMITTED: "Soumis",
        TO_FIX: "À corriger",
        VALIDATED: "Validé",
        TRANSFERRED: "Reporté dans le logiciel de gestion",
    }
    EDITABLE = (DRAFT, TO_FIX)  # la famille peut encore le modifier
    __table_args__ = (
        db.UniqueConstraint("campaign_id", "household_id"),  # plusieurs NULL permis : les nouvelles familles
        db.CheckConstraint(
            "status IN ('draft', 'submitted', 'to_fix', 'validated', 'transferred')", name="ck_dossier_status"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), nullable=False, unique=True, default=_uuid)
    # Un dossier fait partie de l'historique de la campagne : elle ne se supprime pas tant qu'elle en a.
    campaign_id = db.Column(db.ForeignKey("campaign.id", ondelete="RESTRICT"), nullable=False, index=True)
    household_id = db.Column(db.ForeignKey("household.id", ondelete="SET NULL"), index=True)
    account_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"), index=True)  # qui l'a rempli
    email = db.Column(db.String(255))  # contact d'une nouvelle famille, sans compte
    email_verified_at = db.Column(db.DateTime)
    # Accès d'une nouvelle famille par lien envoyé par email : seule l'empreinte du jeton est gardée.
    access_digest = db.Column(db.String(64), unique=True)
    access_expires_at = db.Column(db.DateTime)
    status = db.Column(db.String(12), nullable=False, default=DRAFT)
    data = db.Column(db.JSON, nullable=False, default=dict)
    review_note = db.Column(db.Text)  # ce que la famille doit corriger
    submitted_at = db.Column(db.DateTime)
    validated_at = db.Column(db.DateTime)
    validated_by_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"))
    transferred_at = db.Column(db.DateTime)
    reminded_at = db.Column(db.DateTime)
    # IBAN saisi par la famille, chiffré comme ceux de l'annuaire, mais lié au dossier (le foyer peut ne pas exister).
    iban_ciphertext = db.Column(db.LargeBinary)
    iban_last4 = db.Column(db.String(4))
    iban_key_version = db.Column(db.Integer)

    campaign = db.relationship("Campaign")
    household = db.relationship("Household")
    account = db.relationship("Account", foreign_keys=[account_id])
    validated_by = db.relationship("Account", foreign_keys=[validated_by_id])

    @property
    def editable(self):
        return self.status in self.EDITABLE

    @property
    def status_label(self):
        return self.STATUSES[self.status]

    def __repr__(self):
        return f"<Dossier {self.id} {self.status}>"


class Enrollment(TimestampMixin, db.Model):
    """Inscription d'un élève à un cours, rattachée à son adhésion de la saison."""

    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    STATUSES = {ACTIVE: "Inscrit", WITHDRAWN: "Abandon"}
    __table_args__ = (
        db.UniqueConstraint("membership_id", "course_id"),
        db.CheckConstraint("status IN ('active', 'withdrawn')", name="ck_enrollment_status"),
        db.CheckConstraint("withdrawn_on >= enrolled_on", name="ck_enrollment_dates"),
        db.CheckConstraint("replaces_id <> id", name="ck_enrollment_replaces"),
        db.CheckConstraint("slot_minutes > 0", name="ck_enrollment_slot_minutes"),
        db.CheckConstraint("slot_starts_at IS NULL OR slot_minutes IS NOT NULL", name="ck_enrollment_slot"),
    )

    id = db.Column(db.Integer, primary_key=True)
    membership_id = db.Column(db.ForeignKey("membership.id", ondelete="CASCADE"), nullable=False, index=True)
    # Un cours qui a des élèves ne se supprime pas.
    course_id = db.Column(db.ForeignKey("course.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default=ACTIVE)
    # Date d'entrée dans le cours : la validation du dossier, ou l'arrivée en cours de saison.
    enrolled_on = db.Column(db.Date, nullable=False, default=date.today, server_default=db.text("CURRENT_DATE"))
    withdrawn_on = db.Column(db.Date)
    dossier_id = db.Column(db.ForeignKey("dossier.id", ondelete="SET NULL"), index=True)
    # Transfert : l'inscription quittée pour celle-ci (même saison). Voir docs/cours.md.
    replaces_id = db.Column(db.ForeignKey("enrollment.id", ondelete="SET NULL"), index=True)
    # Cours individuel : heure de passage et durée (minutes). La durée peut être connue avant l'heure.
    slot_starts_at = db.Column(db.Time)
    slot_minutes = db.Column(db.SmallInteger)

    membership = db.relationship(
        "Membership", backref=db.backref("enrollments", cascade="all, delete-orphan", passive_deletes=True)
    )
    course = db.relationship("Course", backref=db.backref("enrollments", passive_deletes=True))
    dossier = db.relationship("Dossier")
    replaces = db.relationship("Enrollment", remote_side=[id])

    @property
    def active(self):
        return self.status == self.ACTIVE

    @property
    def slot_ends_at(self):
        if self.slot_starts_at is None or not self.slot_minutes:
            return None
        start = datetime.combine(date.min, self.slot_starts_at)
        return (start + timedelta(minutes=self.slot_minutes)).time()

    def attends_on(self, day):
        """Suit le cours ce jour-là : entré au plus tard ce jour, pas encore parti."""
        return self.enrolled_on <= day and (self.withdrawn_on is None or day < self.withdrawn_on)

    @property
    def status_label(self):
        return self.STATUSES[self.status]


class WaitlistEntry(db.Model):
    """Place sur la liste d'attente d'un cours. L'ordre est une position explicite, que le secrétariat change.
    Une entrée n'est jamais supprimée par l'appli : elle passe à « inscrit » ou « retiré ». Voir docs/cours.md."""

    WAITING = "waiting"
    ENROLLED = "enrolled"
    REMOVED = "removed"
    STATUSES = {WAITING: "En attente", ENROLLED: "Inscrit", REMOVED: "Retiré"}
    __table_args__ = (
        db.UniqueConstraint("course_id", "membership_id"),
        db.CheckConstraint("status IN ('waiting', 'enrolled', 'removed')", name="ck_waitlist_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Un cours qui a une liste d'attente ne se supprime pas.
    course_id = db.Column(db.ForeignKey("course.id", ondelete="RESTRICT"), nullable=False, index=True)
    membership_id = db.Column(db.ForeignKey("membership.id", ondelete="CASCADE"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(10), nullable=False, default=WAITING)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    closed_at = db.Column(db.DateTime)
    dossier_id = db.Column(db.ForeignKey("dossier.id", ondelete="SET NULL"), index=True)

    course = db.relationship("Course", backref=db.backref("waitlist", passive_deletes=True))
    membership = db.relationship(
        "Membership", backref=db.backref("waitlist", cascade="all, delete-orphan", passive_deletes=True)
    )
    dossier = db.relationship("Dossier")

    @property
    def waiting(self):
        return self.status == self.WAITING


class Consent(db.Model):
    """Consentement donné (ou refusé) pour une saison, daté, avec le texte tel qu'il était affiché."""

    __table_args__ = (db.CheckConstraint("(person_id IS NULL) <> (household_id IS NULL)", name="ck_consent_owner"),)

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.ForeignKey("person.id", ondelete="CASCADE"), index=True)
    household_id = db.Column(db.ForeignKey("household.id", ondelete="CASCADE"), index=True)
    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), nullable=False, index=True)
    label = db.Column(db.String(120), nullable=False)
    text = db.Column(db.Text, nullable=False)
    granted = db.Column(db.Boolean, nullable=False)
    given_at = db.Column(db.DateTime, nullable=False)
    given_by_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"))
    given_by_name = db.Column(db.String(160))  # nom de la personne qui a répondu, gardé si son compte disparaît
    dossier_id = db.Column(db.ForeignKey("dossier.id", ondelete="SET NULL"), index=True)

    person = db.relationship(
        "Person",
        backref=db.backref(
            "consents", cascade="all, delete-orphan", passive_deletes=True, order_by="Consent.given_at.desc()"
        ),
    )
    household = db.relationship(
        "Household", backref=db.backref("consents", cascade="all, delete-orphan", passive_deletes=True)
    )
    season = db.relationship("Season")
    dossier = db.relationship("Dossier")


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
    mandates = db.relationship(
        "SepaMandate", back_populates="bank_account", cascade="all, delete-orphan", order_by="SepaMandate.id"
    )

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
# Tarifs et cotisations (voir docs/tarifs.md)
# ---------------------------------------------------------------------------


class Regie(ArchivableMixin, db.Model):
    """Régie qui encaisse (« École », « Orchestre »…). Chaque élément de cotisation relève de l'une d'elles."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False, unique=True)

    def __repr__(self):
        return f"<Regie {self.name}>"


class FeeSeason(db.Model):
    """Réglages des tarifs d'une saison : classement des élèves, arrondi, tranches de QF, communes résidentes."""

    season_id = db.Column(db.ForeignKey("season.id", ondelete="CASCADE"), primary_key=True)
    # Réglages du moteur (services/pricing.py : DEFAULT_SETTINGS), vérifiés par validate_settings.
    engine = db.Column(db.JSON, nullable=False, default=dict)
    # Tranches de quotient familial, de la plus basse à la plus haute : [{"label": "A", "max": 600}, …, {"label": "C"}].
    qf_bands = db.Column(db.JSON, nullable=False, default=list)
    default_band = db.Column(db.String(10))  # tranche d'un foyer qui ne donne pas son quotient
    resident_cities = db.Column(db.JSON, nullable=False, default=list)  # communes « résidentes »

    season = db.relationship(
        "Season", backref=db.backref("fee_season", uselist=False, cascade="all, delete-orphan", passive_deletes=True)
    )


class FeeItem(TimestampMixin, db.Model):
    """Élément de cotisation d'une saison : adhésion, formule, pratique collective, location…

    Le code repris du logiciel de gestion n'est qu'une information : les codes changent d'une saison à l'autre.
    """

    __table_args__ = (
        db.UniqueConstraint("season_id", "label"),
        db.CheckConstraint("price >= 0", name="ck_fee_item_price"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Une saison qui a des éléments ne se supprime pas.
    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), nullable=False, index=True)
    label = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20))
    category = db.Column(db.String(60))
    regie_id = db.Column(db.ForeignKey("regie.id", ondelete="RESTRICT"), index=True)
    price = db.Column(db.Integer, nullable=False)  # prix de base, en centimes
    position = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    notes = db.Column(db.Text)

    season = db.relationship("Season")
    regie = db.relationship("Regie")

    def __repr__(self):
        return f"<FeeItem {self.label}>"


class FeeRule(TimestampMixin, db.Model):
    """Règle de tarif d'une saison. Les règles s'appliquent dans l'ordre de `position`, de la plus petite à la plus
    grande. Conditions, cible et effet sont vérifiés par services/pricing.py avant tout enregistrement."""

    __table_args__ = (db.CheckConstraint("position >= 0", name="ck_fee_rule_position"),)

    id = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())  # sinon : brouillon
    conditions = db.Column(db.JSON, nullable=False, default=list)
    target = db.Column(db.JSON, nullable=False, default=dict)
    effect = db.Column(db.JSON, nullable=False)
    exclusive_group = db.Column(db.String(60))
    notes = db.Column(db.Text)

    season = db.relationship("Season")

    def as_dict(self):
        """La règle telle que le moteur la lit."""
        return {
            "name": self.name,
            "conditions": self.conditions,
            "target": self.target,
            "effect": self.effect,
            "group": self.exclusive_group,
        }

    def __repr__(self):
        return f"<FeeRule {self.position} {self.name}>"


class HouseholdSeason(db.Model):
    """Situation d'un foyer pour une saison : ce qui fixe ses tarifs. Réservée au trésorier et à la direction."""

    household_id = db.Column(db.ForeignKey("household.id", ondelete="CASCADE"), primary_key=True)
    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), primary_key=True, index=True)
    qf_band = db.Column(db.String(10))  # tranche saisie ; vide : déduite du quotient, sinon tranche par défaut
    quotient = db.Column(db.Integer)  # quotient familial, seulement si nécessaire
    qf_proof = db.Column(db.String(40))  # justificatif : attestation CAF, avis d'imposition…
    resident = db.Column(db.Boolean)  # vide : déduit de la commune du foyer
    tags = db.Column(db.JSON, nullable=False, default=list)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    household = db.relationship(
        "Household", backref=db.backref("seasons", cascade="all, delete-orphan", passive_deletes=True)
    )
    season = db.relationship("Season")


class FeePick(db.Model):
    """Élément pris en dehors des cours : location d'instrument, frais particuliers… ajouté par un gestionnaire."""

    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.ForeignKey("household.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = db.Column(db.ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True)
    item_id = db.Column(db.ForeignKey("fee_item.id", ondelete="RESTRICT"), nullable=False, index=True)
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    household = db.relationship("Household")
    person = db.relationship("Person")
    item = db.relationship("FeeItem")


class Assessment(TimestampMixin, db.Model):
    """Cotisation d'un foyer pour une saison : calculée (proposée), éventuellement corrigée, puis validée.

    Les lignes gardent libellés, régies et montants tels qu'ils étaient : supprimer un élément ou une fiche ne change
    pas une cotisation validée.
    """

    PROPOSED = "proposed"
    VALIDATED = "validated"
    STATUSES = {PROPOSED: "Proposée", VALIDATED: "Validée"}
    __table_args__ = (
        db.UniqueConstraint("household_id", "season_id"),
        db.CheckConstraint("status IN ('proposed', 'validated')", name="ck_assessment_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # L'historique des cotisations reste : un foyer ou une saison qui en ont ne se suppriment pas.
    household_id = db.Column(db.ForeignKey("household.id", ondelete="RESTRICT"), nullable=False, index=True)
    season_id = db.Column(db.ForeignKey("season.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default=PROPOSED)
    computed_total = db.Column(db.Integer, nullable=False)  # total calculé par le moteur, avant corrections
    computed_at = db.Column(db.DateTime, nullable=False)
    validated_at = db.Column(db.DateTime)
    validated_by_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"))
    warnings = db.Column(db.JSON, nullable=False, default=list)
    # Changements d'inscription survenus depuis le calcul (« à revoir »). Vidé par un nouveau calcul, ou quand un
    # gestionnaire constate qu'il n'y a rien à changer. Voir docs/cours.md.
    review_reasons = db.Column(db.JSON, nullable=False, default=list, server_default="[]")

    household = db.relationship("Household")
    season = db.relationship("Season")
    validated_by = db.relationship("Account")
    lines = db.relationship(
        "AssessmentLine", back_populates="assessment", cascade="all, delete-orphan", order_by="AssessmentLine.position"
    )

    @property
    def total(self):
        return sum(line.amount for line in self.lines)

    @property
    def status_label(self):
        return self.STATUSES[self.status]

    def __repr__(self):
        return f"<Assessment {self.household_id}/{self.season_id} {self.status}>"


class AssessmentLine(db.Model):
    """Ligne d'une cotisation : un élément pour un élève, une règle sur le total du foyer, ou l'arrondi."""

    ITEM = "item"
    ADJUSTMENT = "adjustment"
    ROUNDING = "rounding"
    __table_args__ = (
        db.CheckConstraint("kind IN ('item', 'adjustment', 'rounding')", name="ck_assessment_line_kind"),
        db.CheckConstraint(
            "(corrected_at IS NULL) = (correction_reason IS NULL)", name="ck_assessment_line_correction"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.ForeignKey("assessment.id", ondelete="CASCADE"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(10), nullable=False, default=ITEM)
    person_id = db.Column(db.ForeignKey("person.id", ondelete="SET NULL"), index=True)
    person_name = db.Column(db.String(160))
    item_id = db.Column(db.ForeignKey("fee_item.id", ondelete="SET NULL"), index=True)
    label = db.Column(db.String(160), nullable=False)
    regie_name = db.Column(db.String(60))
    base = db.Column(db.Integer, nullable=False, default=0)  # prix de base de l'élément
    computed = db.Column(db.Integer, nullable=False)  # montant calculé par le moteur
    amount = db.Column(db.Integer, nullable=False)  # montant retenu : le calculé, ou la correction
    steps = db.Column(db.JSON, nullable=False, default=list)  # règles appliquées : [{rule, before, after, note}]
    correction_reason = db.Column(db.String(255))
    corrected_at = db.Column(db.DateTime)
    corrected_by_id = db.Column(db.ForeignKey("account.id", ondelete="SET NULL"))

    assessment = db.relationship("Assessment", back_populates="lines")
    person = db.relationship("Person")
    item = db.relationship("FeeItem")
    corrected_by = db.relationship("Account")

    @property
    def corrected(self):
        return self.corrected_at is not None


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
    # Supprimée par son destinataire : cachée, mais gardée pour que sa clé de déduplication empêche de la recréer.
    dismissed_at = db.Column(db.DateTime)

    account = db.relationship("Account")


class AppSetting(db.Model):
    """Réglage de l'appli modifiable depuis l'interface (nom, logo, couleur…), stocké en clé / valeur."""

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, nullable=False)

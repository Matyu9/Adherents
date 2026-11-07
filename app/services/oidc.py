"""Serveur OIDC, côté données : clés de signature, applis clientes, consentements, jetons et informations transmises.

Le protocole lui-même est dans oidc_server.py (Authlib) et blueprints/oidc.py. Les services ne font pas de commit.
"""
import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from flask import current_app, session
from joserfc import jws, jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey

from ..extensions import db
from ..models import LoginSession, OidcClient, OidcCode, OidcConsent, OidcKey, OidcToken
from . import crypto
from .activity import log

CODE_LIFETIME = timedelta(seconds=60)
KEY_ROTATION = timedelta(days=90)
# Une clé retirée reste publiée bien au-delà de la vie de ses jetons, pour vérifier un `id_token_hint` ancien.
RETIRED_KEY_KEPT = timedelta(days=30)
# Un code déjà utilisé est gardé un peu après son expiration : le rejouer doit encore annuler ses jetons.
USED_CODE_KEPT = timedelta(days=1)
RESUME_LIFETIME = 10 * 60
MAX_REDIRECT_URI = 2000
SCOPE_LABELS = {
    "profile": "Votre nom et votre prénom",
    "email": "Votre adresse email",
    "groups": "Les groupes dont vous faites partie, parmi ceux que l'association partage avec cette application",
}
_LOOPBACK = ("localhost", "127.0.0.1", "::1")


def utcnow():
    """Pour les durées de vie : l'heure locale saute au changement d'heure, et un code vivrait une heure de trop."""
    return datetime.now(UTC).replace(tzinfo=None)


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issuer():
    return current_app.config["OIDC_ISSUER"]


# ---------------------------------------------------------------------------
# Clés de signature
# ---------------------------------------------------------------------------

def _fernet():
    return crypto.fernet(b"adherents/oidc-signing-key")


def _create_key():
    key = RSAKey.generate_key(2048)
    kid = key.thumbprint()
    row = OidcKey(kid=kid, public_jwk={**key.as_dict(private=False), "kid": kid, "use": "sig", "alg": "RS256"},
                  private_key=_fernet().encrypt(key.as_pem(private=True)).decode(), created_at=datetime.now())
    db.session.add(row)
    db.session.flush()
    return row


def _retire(row, now=None):
    row.retired_at = now or datetime.now()
    row.private_key = None  # plus rien à signer : autant ne plus la garder


def active_key():
    """(kid, clé privée) de la clé qui signe, créée au premier besoin. La plus récente, si deux workers en ont créé une."""
    rows = db.session.scalars(db.select(OidcKey).where(OidcKey.retired_at.is_(None))
                              .order_by(OidcKey.created_at.desc(), OidcKey.kid)).all()
    for row in rows:
        try:
            return row.kid, _fernet().decrypt(row.private_key.encode())
        except InvalidToken:
            # SECRET_KEY a changé : cette clé est perdue. Mieux vaut une nouvelle clé qu'une connexion impossible partout.
            current_app.logger.error("Clé de signature OIDC %s illisible (SECRET_KEY changée ?) : remplacée.", row.kid)
            _retire(row)
    row = _create_key()
    return row.kid, _fernet().decrypt(row.private_key.encode())


def rotate_key(now=None):
    """Retire la clé en cours et en crée une autre. Les applis la découvrent seules, grâce au `kid` du jeton."""
    for row in db.session.scalars(db.select(OidcKey).where(OidcKey.retired_at.is_(None))):
        _retire(row, now)
    return _create_key()


def published_keys(now=None):
    limit = (now or datetime.now()) - RETIRED_KEY_KEPT
    return db.session.scalars(db.select(OidcKey).where(db.or_(OidcKey.retired_at.is_(None), OidcKey.retired_at > limit))
                              .order_by(OidcKey.created_at.desc(), OidcKey.kid)).all()


def jwks():
    keys = published_keys()
    if not any(row.retired_at is None for row in keys):
        active_key()
        keys = published_keys()
    return {"keys": [row.public_jwk for row in keys]}


def hint_claims(raw):
    """Contenu d'un `id_token_hint` que ce serveur a bien signé (même expiré, comme le permet la norme), sinon None.

    Un jeton de déconnexion, signé avec la même clé, n'est pas un jeton d'identité : il est refusé.
    """
    try:
        header = jws.extract_compact(raw.encode()).protected
        kid = header.get("kid")
        row = db.session.get(OidcKey, kid) if kid and isinstance(kid, str) else None
        if row is None or row not in published_keys() or header.get("typ") not in (None, "JWT"):
            return None
        claims = jwt.decode(raw, RSAKey.import_key(row.public_jwk), algorithms=["RS256"]).claims
    except (JoseError, ValueError, TypeError, AttributeError):
        return None
    if claims.get("iss") != issuer() or "events" in claims or not isinstance(claims.get("sub"), str):
        return None
    return claims


def subject_of_hint(raw):
    claims = hint_claims(raw)
    return claims["sub"] if claims else None


# ---------------------------------------------------------------------------
# Applis clientes
# ---------------------------------------------------------------------------

def set_secret(client):
    """Remplace le secret. Renvoie sa valeur en clair : c'est le seul moment où elle existe. Sans commit."""
    raw = secrets.token_urlsafe(32)
    client.secret_hash = digest(raw)
    return raw


def new_client_id():
    return secrets.token_urlsafe(18)


def redirect_uri_problem(uri):
    """Motif de refus d'une adresse de retour, sinon None. Le navigateur y sera renvoyé avec le code d'autorisation."""
    try:
        parts = urlsplit(uri)
        host = parts.hostname
    except ValueError:
        return "adresse illisible"
    if len(uri) > MAX_REDIRECT_URI:
        return "adresse trop longue"
    if parts.scheme not in ("http", "https") or not host:
        return "elle doit commencer par https://"
    if parts.scheme == "http" and host not in _LOOPBACK:
        return "http:// n'est accepté que sur cet ordinateur (localhost)"
    if parts.fragment or "#" in uri:
        return "elle ne peut pas contenir de #"
    if parts.username or parts.password:
        return "elle ne peut pas contenir d'identifiants"
    if "*" in uri:
        return "les jokers ne sont pas acceptés"
    return None


def may_access(client, account):
    """L'appli est ouverte et cette personne est dans l'un de ses groupes autorisés. Un administrateur n'y échappe pas."""
    return (client.active and account.active
            and any(group in client.allowed_groups for group in account.groups))


def clients_using(group):
    return db.session.scalars(db.select(OidcClient).where(db.or_(
        OidcClient.allowed_groups.any(id=group.id), OidcClient.shared_groups.any(id=group.id)))
        .order_by(OidcClient.name)).all()


def all_clients():
    return db.session.scalars(db.select(OidcClient).order_by(OidcClient.name)).all()


def find_client(client_id):
    """Appli ouverte portant cet identifiant, sinon None."""
    client = db.session.scalar(db.select(OidcClient).filter_by(client_id=client_id)) if client_id else None
    return client if client is not None and client.active else None


# ---------------------------------------------------------------------------
# Connexion en cours
# ---------------------------------------------------------------------------

def _login_of(account):
    return db.session.get(LoginSession, account.session_token) if account.session_token else None


def auth_time(account):
    """Moment où la personne a vraiment saisi son mot de passe : l'ouverture de la session, « rester connecté » compris."""
    login = _login_of(account)
    return int((login.created_at if login else datetime.now()).timestamp())


def sid_of(account):
    """`sid` de la session de la requête en cours, celle qui ouvre l'appli."""
    login = _login_of(account)
    return login.sid if login else None


def session_alive(sid):
    return db.session.scalar(db.select(LoginSession.sid).filter_by(sid=sid)) is not None


def remember_resume(query):
    session["oidc_resume"] = {"query": query, "at": int(time.time())}


def pop_resume():
    """Demande de connexion mise en attente par une étape imposée (mot de passe, double authentification), sinon None."""
    pending = session.pop("oidc_resume", None)
    return pending["query"] if pending and time.time() - pending["at"] <= RESUME_LIFETIME else None


# ---------------------------------------------------------------------------
# Consentements
# ---------------------------------------------------------------------------

def _meaningful(scope):
    return set(scope.split()) - {"openid"}


def consent_covers(client, account, scope):
    consent = db.session.get(OidcConsent, (account.id, client.id))
    return consent is not None and _meaningful(scope) <= set(consent.scope.split())


def grant_consent(client, account, scope):
    consent = db.session.get(OidcConsent, (account.id, client.id))
    if consent is None:
        consent = OidcConsent(account_id=account.id, client_id=client.id, scope="")
        db.session.add(consent)
    consent.scope = " ".join(sorted(set(consent.scope.split()) | set(scope.split())))
    consent.granted_at = datetime.now()


def consents_of(account):
    return db.session.scalars(db.select(OidcConsent).filter_by(account_id=account.id)
                              .order_by(OidcConsent.granted_at.desc())).all()


def revoke_consent(account, client_id):
    """Retire un consentement, avec les jetons et les codes pas encore échangés. Renvoie False s'il n'existe pas."""
    consent = db.session.get(OidcConsent, (account.id, client_id))
    if consent is None:
        return False
    db.session.delete(consent)
    revoke_tokens(account=account, client_id=client_id)
    db.session.execute(db.delete(OidcCode).where(OidcCode.account_id == account.id, OidcCode.client_id == client_id))
    return True


def what_is_shared(client, account, scope):
    """Lignes du formulaire de consentement."""
    lines = [SCOPE_LABELS[s] for s in OidcClient.SCOPES if s in scope.split() and s in SCOPE_LABELS]
    names = sorted(group.name for group in account.groups if group in client.shared_groups)
    return lines, names if "groups" in scope.split() else []


# ---------------------------------------------------------------------------
# Jetons et informations transmises
# ---------------------------------------------------------------------------

def shared_groups(client, account):
    return sorted(group.key for group in account.groups if group in client.shared_groups)


def claims_for(account, client, scope):
    """Ce que l'appli reçoit : `sub` toujours, le reste selon ce qu'elle a demandé."""
    scopes = scope.split()
    claims = {"sub": account.sub}
    if "profile" in scopes:
        claims.update(name=account.full_name, given_name=account.first_name, family_name=account.last_name,
                      updated_at=int(account.updated_at.timestamp()))
    if "email" in scopes:
        claims.update(email=account.email, email_verified=account.email_verified)
    if "groups" in scopes:
        claims["groups"] = shared_groups(client, account)
    return claims


def find_token(raw):
    """Jeton encore valable, pour une personne qui a toujours accès à cette appli, sinon None."""
    token = db.session.get(OidcToken, digest(raw)) if raw else None
    if token is None or token.expires_at < utcnow() or not may_access(token.client, token.account):
        return None
    return token


def revoke_tokens(account=None, client=None, client_id=None):
    query = db.delete(OidcToken)
    if account is not None:
        query = query.where(OidcToken.account_id == account.id)
    if client is not None or client_id is not None:
        query = query.where(OidcToken.client_id == (client.id if client is not None else client_id))
    db.session.execute(query)


def maintain(now=None):
    """Tâche périodique : renouvelle la clé si elle a l'âge, efface ce qui a expiré. Renvoie le nombre d'éléments effacés."""
    now = now or datetime.now()
    newest = db.session.scalar(db.select(OidcKey).where(OidcKey.retired_at.is_(None))
                               .order_by(OidcKey.created_at.desc()))
    if newest is not None and now - newest.created_at >= KEY_ROTATION:
        rotate_key(now)
        log("oidc.key_rotate", "Clé de signature renouvelée automatiquement")
    deleted = 0
    now_utc = now.astimezone(UTC).replace(tzinfo=None)
    for query in (db.delete(OidcToken).where(OidcToken.expires_at < now_utc),
                  db.delete(OidcCode).where(OidcCode.expires_at < now_utc - USED_CODE_KEPT),
                  db.delete(OidcKey).where(OidcKey.retired_at < now - RETIRED_KEY_KEPT)):
        deleted += db.session.execute(query).rowcount
    return deleted

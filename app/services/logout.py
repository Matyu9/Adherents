"""Déconnexion des applis connectées : OpenID Connect Back-Channel Logout 1.0.

Chaque appli où une session a servi est notée (OidcSession). Quand cette session se ferme, que le compte perd l'accès,
ou que les groupes transmis à l'appli changent, un avis signé part vers son `backchannel_logout_uri`. Les avis
attendent dans une file (OidcLogout) : ils partent juste après la requête, puis `flask run-tasks` réessaie ceux
qu'une appli injoignable n'a pas reçus.

Les fonctions qui modifient la file ne font pas de commit, comme les autres services. `deliver_pending` en fait :
elle valide chaque avis à part, pour ne jamais garder la base verrouillée pendant un appel réseau.
"""
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlencode

from flask import current_app, g
from joserfc import jwt
from joserfc.jwk import RSAKey

from ..extensions import db
from ..models import Activity, LoginSession, OidcLogout, OidcSession
from . import oidc

logger = logging.getLogger(__name__)

EVENT = "http://schemas.openid.net/event/backchannel-logout"
TOKEN_LIFETIME = 120  # secondes : l'appli reçoit l'avis aussitôt, ou un nouvel avis est signé au prochain essai
TIMEOUT = 5
# Un avis en cours d'envoi est réservé : un autre worker ne l'envoie pas en même temps.
LEASE = timedelta(minutes=2)
# Délais entre deux essais, puis le dernier se répète. Le scheduler passe toutes les 15 minutes.
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1),
                timedelta(hours=3), timedelta(hours=6))
# Au-delà, l'avis est abandonné et noté au journal : l'appli a été injoignable trop longtemps.
GIVE_UP = timedelta(days=3)


# ---------------------------------------------------------------------------
# Ce qui déclenche un avis
# ---------------------------------------------------------------------------

def remember(code, account, client):
    """Note que la session qui a obtenu `code` a servi à entrer dans `client`, avec les groupes transmis."""
    if not code.sid:
        return
    groups = oidc.shared_groups(client, account) if "groups" in code.scope.split() else None
    row = db.session.scalar(db.select(OidcSession).filter_by(sid=code.sid, client_id=client.id))
    if row is None:
        db.session.add(OidcSession(sid=code.sid, client_id=client.id, account_id=account.id, groups=groups,
                                   created_at=datetime.now()))
    else:  # nouvelle connexion à la même appli depuis la même session : les groupes transmis sont ceux-ci
        row.groups = groups
        row.account_id = account.id


def _end(row):
    """Retire `row` et met en file l'avis pour son appli, si elle sait en recevoir un."""
    client = row.client
    if client.backchannel_logout_uri:
        now = datetime.now()
        pending = db.session.scalar(db.select(OidcLogout).filter_by(client_id=client.id, sid=row.sid).limit(1))
        if pending is None:
            db.session.add(OidcLogout(client_id=client.id, sub=row.account.sub, sid=row.sid, created_at=now,
                                      next_attempt_at=now))
        elif pending.next_attempt_at > now:  # un avis attend déjà pour cette session : il part maintenant
            pending.next_attempt_at = now
        g.oidc_logout_queued = True
    db.session.delete(row)


def sessions_ended(sids):
    """Ces sessions de connexion sont fermées : chaque appli où elles ont servi est prévenue."""
    sids = [sid for sid in sids if sid]
    if not sids:
        return 0
    rows = db.session.scalars(db.select(OidcSession).where(OidcSession.sid.in_(sids))).all()
    for row in rows:
        _end(row)
    return len(rows)


def review_access(accounts=None, clients=None):
    """Après un changement de groupes, de compte ou d'appli : prévient chaque appli dont une session n'est plus juste.

    Une personne qui n'a plus accès à l'appli en est déconnectée, et ses jetons sont annulés. Si seuls les groupes
    transmis ont changé, l'appli est aussi prévenue : à la reconnexion, sans rien ressaisir, elle reçoit les bons.
    Renvoie le nombre de sessions d'appli fermées.
    """
    query = db.select(OidcSession)
    if accounts is not None:
        query = query.where(OidcSession.account_id.in_([account.id for account in accounts]))
    if clients is not None:
        query = query.where(OidcSession.client_id.in_([client.id for client in clients]))
    db.session.flush()  # les groupes juste modifiés doivent être ceux que l'on compare
    ended = 0
    for row in db.session.scalars(query).all():
        client, account = row.client, row.account
        if not oidc.may_access(client, account):
            oidc.revoke_tokens(account=account, client=client)
        elif row.groups is None or row.groups == oidc.shared_groups(client, account):
            continue
        _end(row)
        ended += 1
    return ended


def end_client_sessions(account, client_id):
    """La personne retire son autorisation à une appli : celle-ci la déconnecte."""
    for row in db.session.scalars(db.select(OidcSession).filter_by(account_id=account.id, client_id=client_id)):
        _end(row)


def orphans():
    """Sessions d'appli dont la session de connexion n'existe plus (filet de sécurité de la tâche périodique)."""
    alive = db.select(LoginSession.sid)
    return sessions_ended(db.session.scalars(db.select(OidcSession.sid).where(OidcSession.sid.not_in(alive))
                                             .distinct()).all())


def pending_for(client):
    return db.session.scalar(db.select(db.func.count(OidcLogout.id)).filter_by(client_id=client.id))


# ---------------------------------------------------------------------------
# Envoi
# ---------------------------------------------------------------------------

def logout_token(client, sub, sid):
    """Jeton de déconnexion signé (Back-Channel Logout, section 2.4) : jamais de `nonce`, `typ` dédié."""
    kid, private = oidc.active_key()
    now = int(time.time())
    claims = {"iss": oidc.issuer(), "aud": client.client_id, "iat": now, "exp": now + TOKEN_LIFETIME,
              "jti": secrets.token_urlsafe(16), "sub": sub, "events": {EVENT: {}}}
    if sid:
        claims["sid"] = sid
    return jwt.encode({"alg": "RS256", "typ": "logout+jwt", "kid": kid}, claims, RSAKey.import_key(private))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """L'avis va à l'adresse déclarée, nulle part ailleurs : une redirection compte comme un échec."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def post(uri, token):
    """Envoie l'avis. Renvoie None s'il est reçu (réponse 2xx, en pratique 200 ou 204), sinon le motif de l'échec."""
    request = urllib.request.Request(uri, data=urlencode({"logout_token": token}).encode(), method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Adherents-OIDC"})
    try:
        with _opener.open(request, timeout=TIMEOUT) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        return f"réponse {error.code}"
    except (urllib.error.URLError, OSError, ValueError) as error:
        return f"injoignable : {getattr(error, 'reason', error)}"[:255]
    # urllib lève HTTPError pour tout le reste, redirections comprises (voir _NoRedirect).
    return None if 200 <= status < 300 else f"réponse {status}"


def _next_delay(attempts):
    return RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]


def _claim(row, now):
    """Réserve l'avis pour cet envoi. Faux si un autre worker l'a pris entre-temps."""
    taken = db.session.execute(db.update(OidcLogout).where(OidcLogout.id == row.id,
                                                           OidcLogout.next_attempt_at == row.next_attempt_at)
                               .values(next_attempt_at=now + LEASE)).rowcount
    db.session.commit()
    return taken == 1


def deliver_pending(now=None):
    """Envoie les avis dus. Renvoie (reçus, en échec, abandonnés). Valide la base après chaque avis."""
    immediate = g.get("write_lock", False)
    g.write_lock = True  # chaque transaction d'ici lit puis écrit : elle prend le verrou d'écriture d'emblée
    try:
        return _deliver(now or datetime.now())
    finally:
        g.write_lock = immediate


def _deliver(now):
    sent = failed = dropped = 0
    due = db.session.execute(db.select(OidcLogout.id, OidcLogout.next_attempt_at)
                             .where(OidcLogout.next_attempt_at <= now).order_by(OidcLogout.id)).all()
    db.session.commit()
    for row in due:
        if not _claim(row, now):
            continue
        notice = db.session.get(OidcLogout, row.id)
        client = notice.client if notice else None
        if client is None or not client.backchannel_logout_uri:  # adresse retirée entre-temps : plus personne à prévenir
            if notice is not None:
                db.session.delete(notice)
            db.session.commit()
            continue
        uri, token = client.backchannel_logout_uri, logout_token(client, notice.sub, notice.sid)
        db.session.commit()  # la clé de signature, si elle vient d'être créée ; et surtout pas de verrou pendant l'envoi
        problem = post(uri, token)
        notice = db.session.get(OidcLogout, row.id)
        if notice is None:  # appli supprimée pendant l'envoi
            db.session.commit()
            continue
        if problem is None:
            db.session.delete(notice)
            sent += 1
        elif now - notice.created_at >= GIVE_UP:
            # Sans auteur : la personne dont la requête a déclenché cet envoi n'y est pour rien.
            db.session.add(Activity(action="oidc.logout_abandoned", message=(
                f"Avis de déconnexion abandonné pour {client.name} après {notice.attempts + 1} essai(s) : {problem}")))
            db.session.delete(notice)
            dropped += 1
        else:
            notice.attempts += 1
            notice.last_error = problem
            notice.next_attempt_at = now + _next_delay(notice.attempts)
            logger.warning("Avis de déconnexion non reçu par %s (%s) : nouvel essai prévu", client.name, problem)
            failed += 1
        db.session.commit()
    return sent, failed, dropped


def deliver_after_request(response):
    """Après une requête qui a mis des avis en file : ils partent sans faire attendre la personne."""
    if not g.pop("oidc_logout_queued", False):
        return response
    app = current_app._get_current_object()
    # Ce que la route n'a pas validé n'a pas à l'être ici : de toute façon, la fin de la requête l'annulerait.
    db.session.rollback()
    if not app.config["BACKCHANNEL_IN_BACKGROUND"]:
        deliver_pending()
        return response

    def run():
        with app.app_context():
            try:
                deliver_pending()
            except Exception:  # noqa: BLE001  le scheduler réessaiera : surtout ne pas perdre l'erreur
                logger.exception("Échec de l'envoi des avis de déconnexion")
            finally:
                db.session.remove()

    threading.Thread(target=run, daemon=True).start()
    return response


def init_app(app):
    app.after_request(deliver_after_request)


"""Points d'accès du serveur OIDC.

Après un formulaire (connexion, code, consentement), le retour vers l'appli passe par une page intermédiaire. Chrome
applique `form-action 'self'` à toute la chaîne de redirections qui suit un formulaire, et bloquerait la dernière, vers
l'appli. Une nouvelle navigation casse cette chaîne, sans avoir à assouplir la CSP.
"""
import time
from urllib.parse import parse_qsl, urlencode

from authlib.common.urls import add_params_to_uri
from authlib.oauth2.rfc6749 import InsecureTransportError, OAuth2Error
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from werkzeug.datastructures import MultiDict

from ..extensions import csrf, db
from ..models import OidcClient
from ..services import oidc, sessions, throttle, totp
from ..services.activity import log

bp = Blueprint("oidc", __name__)


def _server():
    return current_app.extensions["oidc_server"]


def _endpoint(name):
    # L'émetteur, jamais l'en-tête Host : une requête ne doit pas pouvoir changer les adresses annoncées.
    return oidc.issuer() + url_for(name)


@bp.route("/.well-known/openid-configuration")
def discovery():
    response = jsonify(
        issuer=oidc.issuer(),
        authorization_endpoint=_endpoint(".authorize"),
        token_endpoint=_endpoint(".token"),
        userinfo_endpoint=_endpoint(".userinfo"),
        jwks_uri=_endpoint(".jwks"),
        end_session_endpoint=_endpoint(".end_session"),
        scopes_supported=list(OidcClient.SCOPES),
        response_types_supported=["code"],
        response_modes_supported=["query"],
        grant_types_supported=["authorization_code"],
        subject_types_supported=["public"],
        id_token_signing_alg_values_supported=["RS256"],
        token_endpoint_auth_methods_supported=list(OidcClient.AUTH_METHODS),
        code_challenge_methods_supported=["S256"],
        claims_supported=["iss", "sub", "aud", "exp", "iat", "auth_time", "nonce", "at_hash", "name", "given_name",
                          "family_name", "updated_at", "email", "email_verified", "groups", "sid"],
        prompt_values_supported=["none", "login", "consent"],
        claims_parameter_supported=False,
        request_parameter_supported=False,
        request_uri_parameter_supported=False,
        backchannel_logout_supported=True,
        backchannel_logout_session_supported=True,
        frontchannel_logout_supported=False,
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@bp.route("/oidc/jwks")
def jwks():
    keys = oidc.jwks()
    db.session.commit()  # la clé créée au premier appel
    response = jsonify(keys)
    # Court : une clé renouvelée signe aussitôt (une appli qui ne connaît pas son `kid` recharge cette liste).
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


def _query(args):
    return urlencode(list(args.items(multi=True)))


def _without_prompt(args, *words):
    """La même demande, sans ces valeurs de `prompt` : une fois satisfaites, elles feraient tourner en boucle."""
    remaining = MultiDict(args)
    prompts = [word for word in args.get("prompt", "").split() if word not in words]
    if prompts:
        remaining.setlist("prompt", [" ".join(prompts)])
    else:
        remaining.poplist("prompt")
    return remaining


def _resume_url(args):
    return url_for(".resume") + "?" + _query(args)


def _send_to_login(args, again=False):
    # La connexion qui suit répond à ces deux exigences : les garder ferait tourner en boucle. `id_token_hint` reste :
    # il faudra vérifier que c'est bien cette personne qui s'est connectée.
    fresh = _without_prompt(args, "login")
    fresh.poplist("max_age")
    options = {"reauth": "1"} if again else {}
    return redirect(url_for("auth.login", next=_resume_url(fresh), **options))


def _error_uri(redirect_uri, error, state):
    params = [("error", error)] + ([("state", state)] if state else [])
    return add_params_to_uri(redirect_uri, params)


INVALID_REQUEST = ("Cette demande de connexion n'est pas valable\u00a0: l'application n'est pas reconnue, "
                   "ou son adresse de retour n'est pas celle qui a été déclarée.")


def _refused_page(message, status, title=None):
    return render_template("oidc/error.html", message=message, title=title), status


def _oauth_error(error):
    """Une adresse de retour vérifiée reçoit l'erreur. Sinon, il ne faut surtout pas y envoyer le navigateur."""
    if error.redirect_uri:
        return _server().handle_response(*error())
    return _refused_page(INVALID_REQUEST, 400)


def _insecure_page():
    current_app.logger.error("Connexion OIDC refusée : requête reçue sans https (%s). Vérifiez OIDC_ISSUER et "
                             "TRUST_PROXY.", request.host_url)
    return _refused_page("Ce serveur doit être joint en https pour connecter une application. "
                         "Prévenez un responsable de l'association.", 400)


def _must_authenticate_again(args, account):
    """Vrai si la session en cours ne suffit pas : `prompt=login`, ou `max_age` dépassé."""
    if "login" in args.get("prompt", "").split():
        return True
    return "max_age" in args and time.time() - oidc.auth_time(account) > int(args["max_age"])


@bp.route("/oidc/authorize", methods=["GET", "POST"])
@csrf.exempt
def authorize():
    if request.method == "POST":  # autorisé par la norme : ramené à un seul chemin, que le cookie de session accompagne
        return redirect(url_for(".authorize") + "?" + _query(request.form), 303)
    account = current_user._get_current_object() if current_user.is_authenticated else None
    args = request.args
    try:
        grant = _server().get_consent_grant(end_user=account)
    except InsecureTransportError:
        return _insecure_page()
    except OAuth2Error as error:
        return _oauth_error(error)
    except ValueError:  # Authlib refuse certains caractères dans ses messages, qui recopient la demande
        return _refused_page(INVALID_REQUEST, 400)
    client, redirect_uri, state = grant.client, grant.redirect_uri, args.get("state")
    silent = "none" in args.get("prompt", "").split()

    def tell_client(error):
        return redirect(_error_uri(redirect_uri, error, state))

    if len(args.getlist("prompt")) > 1:
        return tell_client("invalid_request")
    max_age = args.get("max_age")
    if max_age is not None and not (max_age.isascii() and max_age.isdigit() and len(max_age) <= 12):
        return tell_client("invalid_request")
    if account is None:
        return tell_client("login_required") if silent else _send_to_login(args)
    if "id_token_hint" in args and oidc.subject_of_hint(args["id_token_hint"]) != account.sub:
        return tell_client("login_required")  # l'appli attend quelqu'un d'autre : ne pas lui donner cette personne
    if account.must_change_password or (not account.totp_enabled and totp.required(account)):
        if silent:
            return tell_client("interaction_required")
        oidc.remember_resume(_query(args))
        return redirect(url_for("dashboard.index"))  # les règles du compte y conduisent à l'étape imposée
    if _must_authenticate_again(args, account):
        return tell_client("login_required") if silent else _send_to_login(args, again=True)
    if not oidc.may_access(client, account):
        log("oidc.denied", f"{account.full_name} n'a pas accès à {client.name}", user=account, target=account)
        db.session.commit()
        if silent:
            return tell_client("access_denied")
        return _refused_page(f"Votre compte ne donne pas accès à « {client.name} ». "
                             "Si vous pensez que c'est une erreur, contactez un responsable de l'association.", 403)
    scope = grant.request.scope
    wants_consent = "consent" in args.get("prompt", "").split()
    if wants_consent or not (client.first_party or oidc.consent_covers(client, account, scope)):
        if silent:
            return tell_client("consent_required")
        lines, groups = oidc.what_is_shared(client, account, scope)
        return render_template("oidc/consent.html", client=client, lines=lines, groups=groups, form=FlaskForm(),
                               query=_query(args))
    response = _server().create_authorization_response(grant_user=account, grant=grant)
    log("oidc.authorize", f"{account.full_name} s'est connecté à {client.name}", user=account)
    db.session.commit()
    return response


@bp.route("/oidc/reprendre")
def resume():
    """Page intermédiaire : voir le haut de ce fichier. `_query` encode tout, et la destination est toujours locale."""
    return render_template("oidc/continue.html", target=url_for(".authorize") + "?" + _query(request.args))


@bp.route("/oidc/consentement", methods=["POST"])
def consent():
    form = FlaskForm()
    if not form.validate_on_submit():
        abort(400)
    args = MultiDict(parse_qsl(request.form.get("query", ""), keep_blank_values=True))
    if not current_user.is_authenticated:  # session fermée, page restée ouverte : reprendre la demande après connexion
        return _send_to_login(args)
    client = oidc.find_client(args.get("client_id"))
    redirect_uri = args.get("redirect_uri")
    if client is None or not client.check_redirect_uri(redirect_uri):
        return _refused_page("Cette demande de connexion n'est plus valable.", 400)
    account = current_user._get_current_object()
    if not oidc.may_access(client, account):
        abort(403)
    if request.form.get("decision") != "allow":
        log("oidc.consent_refused", f"{account.full_name} a refusé de se connecter à {client.name}", target=account)
        db.session.commit()
        return render_template("oidc/continue.html", target=_error_uri(redirect_uri, "access_denied", args.get("state")))
    oidc.grant_consent(client, account, client.get_allowed_scope(args.get("scope")))
    log("oidc.consent", f"{account.full_name} a autorisé {client.name}", target=account)
    db.session.commit()
    return redirect(_resume_url(_without_prompt(args, "consent")))


@bp.route("/oidc/token", methods=["POST"])
@csrf.exempt
def token():
    try:
        response = _server().create_token_response()
    except InsecureTransportError:
        current_app.logger.error("Échange de jeton refusé : requête reçue sans https (%s).", request.host_url)
        return jsonify(error="invalid_request"), 400
    except UnicodeDecodeError:  # identifiants illisibles dans l'en-tête Authorization
        response = jsonify(error="invalid_client")
        response.status_code = 401
        response.headers["WWW-Authenticate"] = "Basic"
        return response
    if response.status_code == 200:
        db.session.commit()
    else:
        db.session.rollback()
    return response


def _unauthorized():
    response = jsonify(error="invalid_token")
    response.status_code = 401
    response.headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/oidc/userinfo", methods=["GET", "POST"])
@csrf.exempt
def userinfo():
    scheme, _, raw = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        raw = request.form.get("access_token", "")  # jamais dans l'URL : elle finirait dans les journaux d'accès
    found = oidc.find_token(raw.strip())
    if found is None:
        return _unauthorized()
    response = jsonify(oidc.claims_for(found.account, found.client, found.scope))
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Déconnexion demandée par une appli (OpenID Connect RP-Initiated Logout 1.0)
# ---------------------------------------------------------------------------

LOGOUT_INVALID = ("Cette demande de déconnexion n'est pas valable\u00a0: l'application n'est pas reconnue, "
                  "ou son adresse de retour n'est pas celle qui a été déclarée.")


def _logout_request(args):
    """(appli, adresse où revenir, contenu du `id_token_hint`), ou None si la demande est refusée.

    L'adresse de retour n'est suivie que si elle a été déclarée par l'appli, désignée par `client_id` ou par le jeton.
    """
    if any(len(args.getlist(name)) > 1 for name in ("id_token_hint", "client_id", "post_logout_redirect_uri", "state")):
        return None
    hint = args.get("id_token_hint")
    claims = oidc.hint_claims(hint) if hint else None
    audience = claims.get("aud") if claims else None
    if isinstance(audience, list):
        audience = audience[0] if len(audience) == 1 else None
    client_id = args.get("client_id") or None
    if client_id and audience and client_id != audience:
        return None
    client = oidc.find_client(client_id or audience) if (client_id or audience) else None
    target = None
    if uri := args.get("post_logout_redirect_uri"):
        if client is None or uri not in client.post_logout_redirect_uris:
            return None
        state = args.get("state")
        target = add_params_to_uri(uri, [("state", state)]) if state else uri
    return client, target, claims


def _logged_out(target, after_form=False):
    if target is None:
        flash("Vous êtes déconnecté.", "info")
        return redirect(url_for("auth.login"))
    # Après un formulaire, la page intermédiaire : voir le haut de ce fichier.
    return render_template("oidc/continue.html", target=target) if after_form else redirect(target)


@bp.route("/oidc/deconnexion", methods=["GET", "POST"])
@csrf.exempt
def end_session():
    """Sans confirmation seulement si l'appli prouve, par son jeton, qu'elle déconnecte cette personne et cette session.

    Sinon un simple lien, posé n'importe où, déconnecterait qui le suit : la personne confirme.
    """
    args = request.values
    parsed = _logout_request(args)
    if parsed is None:
        return _refused_page(LOGOUT_INVALID, 400, title="Déconnexion impossible")
    client, target, claims = parsed
    if not current_user.is_authenticated:
        return _logged_out(target)
    if claims and claims["sub"] == current_user.sub and claims.get("sid", sessions.current_sid()) == sessions.current_sid():
        sessions.end(via=client.name if client else None)
        return _logged_out(target)
    return render_template("oidc/logout.html", client=client, form=FlaskForm(), query=_query(args))


@bp.route("/oidc/deconnexion/confirmer", methods=["POST"])
def end_session_confirm():
    form = FlaskForm()
    if not form.validate_on_submit():
        abort(400)
    parsed = _logout_request(MultiDict(parse_qsl(request.form.get("query", ""), keep_blank_values=True)))
    if parsed is None:
        return _refused_page(LOGOUT_INVALID, 400, title="Déconnexion impossible")
    client, target, _ = parsed
    if request.form.get("decision") == "logout":
        sessions.end(via=client.name if client else None)
        return _logged_out(target, after_form=True)
    if target is None:
        return redirect(url_for("dashboard.index"))
    return render_template("oidc/continue.html", target=target)


# ---------------------------------------------------------------------------
# Liste des comptes, pour une appli autorisée à la lire
# ---------------------------------------------------------------------------

_directory_failures = throttle.Limiter("directory", 10, 15 * 60)


def _directory_refused(status, error):
    response = jsonify(error=error)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Basic realm="oidc"'
    return response


@bp.route("/oidc/comptes")
def directory():
    """Comptes qui ont accès à l'appli, pour qu'elle les rattache à ses fiches. Identifiant et secret en HTTP Basic.

    Fermé par défaut : il faut cocher « Peut récupérer la liste des comptes » sur l'appli. Une appli tierce n'y voit
    que les personnes qui lui ont donné leur accord (voir oidc.directory).
    """
    keys = [f"ip:{request.remote_addr}"]
    if _directory_failures.blocked(keys):
        return _directory_refused(429, "slow_down")
    auth = request.authorization
    client = oidc.find_client(auth.username) if auth is not None and auth.type == "basic" else None
    if client is None or not client.check_client_secret(auth.password or ""):
        _directory_failures.hit(keys)
        db.session.commit()
        return _directory_refused(401, "invalid_client")
    if not client.directory_access:
        return _directory_refused(403, "access_denied")
    data = oidc.directory(client)
    log("oidc.directory", f"{client.name} a récupéré la liste de ses comptes ({len(data['accounts'])})")
    db.session.commit()
    response = jsonify(data)
    response.headers["Cache-Control"] = "no-store"
    return response

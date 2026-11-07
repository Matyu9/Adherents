"""Protocole OIDC, avec Authlib : uniquement Authorization Code + PKCE S256, pour des clients qui ont un secret.

Authlib est plus permissif par défaut. Sont donc resserrés ici : PKCE (sinon facultatif pour un client confidentiel, et
`plain` accepté), le scope `openid` (sinon un simple code OAuth), `redirect_uri` (sinon facultatif).
"""
from datetime import datetime, timedelta

from authlib.integrations.flask_oauth2 import AuthorizationServer
from authlib.oauth2.rfc6749 import InvalidGrantError, InvalidRequestError, InvalidScopeError, grants
from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.oidc.core.errors import (RegistrationNotSupportedError, RequestNotSupportedError,
                                      RequestURINotSupportedError)
from authlib.oidc.core.grants import OpenIDCode
from flask import g, request as flask_request
from joserfc.jwk import RSAKey

from ..extensions import db
from ..models import OidcClient, OidcCode, OidcToken
from . import logout, oidc
from .activity import log


class StrictPKCE(CodeChallenge):
    SUPPORTED_CODE_CHALLENGE_METHOD = ["S256"]
    DEFAULT_CODE_CHALLENGE_METHOD = "S256"

    def validate_code_challenge(self, grant, redirect_uri):
        data = grant.request.payload.data
        if not data.get("code_challenge"):
            raise InvalidRequestError("Missing 'code_challenge'")
        if data.get("code_challenge_method") != "S256":  # absente, la norme vaudrait « plain »
            raise InvalidRequestError("'code_challenge_method' must be S256")
        super().validate_code_challenge(grant, redirect_uri)


class OpenID(OpenIDCode):
    def exists_nonce(self, nonce, request):
        client = oidc.find_client(request.payload.client_id)
        return client is not None and db.session.scalar(
            db.select(OidcCode.id).filter_by(client_id=client.id, nonce=nonce).limit(1)) is not None

    def resolve_client_private_key(self, client):
        return RSAKey.import_key(oidc.active_key()[1])

    def get_client_algorithm(self, client):
        return "RS256"

    def get_encode_header(self, client):
        return {"alg": "RS256", "typ": "JWT", "kid": oidc.active_key()[0]}

    def get_client_claims(self, client):
        return {"iss": oidc.issuer(), "aud": client.client_id}

    def get_authorization_code_claims(self, authorization_code):
        claims = super().get_authorization_code_claims(authorization_code)
        if authorization_code.sid:  # l'appli le retrouvera dans l'avis de déconnexion de cette session
            claims["sid"] = authorization_code.sid
        return claims

    def encode_id_token(self, token, request):
        g.oidc_client = request.client  # generate_user_info ne reçoit pas l'appli, dont dépendent les groupes transmis
        return super().encode_id_token(token, request)

    def generate_user_info(self, user, scope):
        return oidc.claims_for(user, g.oidc_client, scope)


class CodeGrant(grants.AuthorizationCodeGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = list(OidcClient.AUTH_METHODS)

    def validate_authorization_request(self):
        if not self.request.payload.redirect_uri:
            raise InvalidRequestError("Missing 'redirect_uri' in request.")
        redirect_uri = super().validate_authorization_request()
        data = self.request.payload.data
        if "openid" not in self.request.scope.split():
            raise InvalidScopeError(redirect_uri=redirect_uri)
        if data.get("request"):
            raise RequestNotSupportedError(redirect_uri=redirect_uri)
        if data.get("request_uri"):
            raise RequestURINotSupportedError(redirect_uri=redirect_uri)
        if data.get("registration"):
            raise RegistrationNotSupportedError(redirect_uri=redirect_uri)
        if data.get("response_mode", "query") != "query":
            raise InvalidRequestError("Only the 'query' response mode is supported.", redirect_uri=redirect_uri)
        return redirect_uri

    def save_authorization_code(self, code, request):
        data = request.payload.data
        db.session.add(OidcCode(
            id=oidc.digest(code), client_id=request.client.id, account_id=request.user.id,
            redirect_uri=request.payload.redirect_uri, scope=request.scope, nonce=data.get("nonce"),
            code_challenge=data["code_challenge"], code_challenge_method=data["code_challenge_method"],
            auth_time=oidc.auth_time(request.user), sid=oidc.sid_of(request.user), expires_at=oidc.utcnow() + oidc.CODE_LIFETIME))

    def query_authorization_code(self, code, client):
        row = db.session.get(OidcCode, oidc.digest(code))
        if row is None or row.client_id != client.id:
            return None
        if row.used_at is not None:
            _on_replay(row)
            return None
        return row if row.expires_at >= oidc.utcnow() else None

    def delete_authorization_code(self, authorization_code):
        # Conditionnel : de deux échanges simultanés du même code, un seul aboutit. La route annule l'autre.
        done = db.session.execute(db.update(OidcCode).where(OidcCode.id == authorization_code.id,
                                                            OidcCode.used_at.is_(None))
                                  .values(used_at=datetime.now())).rowcount
        if not done:
            raise InvalidGrantError("Invalid 'code' in request.")

    def authenticate_user(self, authorization_code):
        account = authorization_code.account
        if not oidc.may_access(authorization_code.client, account):
            return None
        # Session fermée entre le code et son échange (déconnexion, appareil retiré) : l'appli n'en ouvre pas d'autre.
        if authorization_code.sid and not oidc.session_alive(authorization_code.sid):
            return None
        return account


def _on_replay(code):
    """Code rejoué : il a pu fuiter, donc les jetons qu'il a donnés ne sont plus sûrs. Validé tout de suite : la réponse
    sera une erreur, et la route annule tout en cas d'erreur."""
    db.session.execute(db.delete(OidcToken).where(OidcToken.code_id == code.id))
    log("oidc.replay", f"Code d'autorisation rejoué pour {code.client.name} ({flask_request.remote_addr}) : "
                       "jetons annulés", user=code.account, target=code.account)
    db.session.commit()


def _save_token(token, request):
    logout.remember(request.authorization_code, request.user, request.client)
    db.session.add(OidcToken(id=oidc.digest(token["access_token"]), client_id=request.client.id,
                             account_id=request.user.id, code_id=request.authorization_code.id, scope=token["scope"],
                             expires_at=oidc.utcnow() + timedelta(seconds=token["expires_in"])))


def init_app(app):
    server = AuthorizationServer()
    server.init_app(app, oidc.find_client, _save_token)
    server.register_grant(CodeGrant, [StrictPKCE(), OpenID()])
    app.extensions["oidc_server"] = server

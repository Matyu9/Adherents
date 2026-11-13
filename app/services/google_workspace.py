"""Client minimal des API Google utilisées pour les agendas des salles, sans dépendance de plus.

Un compte de service s'authentifie avec une assertion JWT signée (RS256) par sa clé privée, et obtient un jeton
d'accès d'une heure. Pas de délégation sur tout le domaine : le compte de service agit en son nom, sur les agendas
des salles qu'on lui a partagés, et lit la liste des salles grâce à un rôle d'administration limité.

Toutes les requêtes passent par `_http`, que les tests remplacent : aucun test ne contacte Google.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app
from joserfc import jwt
from joserfc.jwk import RSAKey

TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR = "https://www.googleapis.com/calendar/v3"
DIRECTORY = "https://admin.googleapis.com/admin/directory/v1"
SCOPES = (
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/admin.directory.resource.calendar.readonly",
)
TIMEOUT = 8  # secondes : une page ne doit pas attendre Google plus longtemps

_token = {"value": None, "expires": 0.0, "key": None}


class GoogleError(Exception):
    def __init__(self, status, message):
        super().__init__(f"{status} : {message}")
        self.status = status


def configured():
    return bool(current_app.config.get("GOOGLE_SERVICE_ACCOUNT_FILE"))


def _credentials():
    with open(current_app.config["GOOGLE_SERVICE_ACCOUNT_FILE"], encoding="utf-8") as handle:
        return json.load(handle)


def _http(method, url, headers, body):
    """(statut, corps décodé) ; remplacé dans les tests."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (adresses fixes, https)
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else {}
        except ValueError:
            return exc.code, {"error": {"message": raw[:200].decode(errors="replace")}}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GoogleError(0, f"Google injoignable ({exc})") from exc


def assertion(credentials, now=None):
    """JWT signé qui demande un jeton d'accès pour le compte de service."""
    now = int(now or time.time())
    claims = {
        "iss": credentials["client_email"],
        "scope": " ".join(SCOPES),
        "aud": credentials.get("token_uri", TOKEN_URL),
        "iat": now,
        "exp": now + 3600,
    }
    header = {"alg": "RS256", "typ": "JWT", "kid": credentials.get("private_key_id")}
    return jwt.encode(header, claims, RSAKey.import_key(credentials["private_key"]))


def access_token():
    credentials = _credentials()
    if _token["value"] and _token["key"] == credentials["client_email"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    body = urllib.parse.urlencode(
        {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion(credentials)}
    ).encode()
    status, data = _http(
        "POST", credentials.get("token_uri", TOKEN_URL), {"Content-Type": "application/x-www-form-urlencoded"}, body
    )
    if status != 200 or "access_token" not in data:
        raise GoogleError(status, data.get("error_description") or data.get("error") or "jeton refusé")
    _token.update(
        value=data["access_token"],
        expires=time.time() + int(data.get("expires_in", 3600)),
        key=credentials["client_email"],
    )
    return _token["value"]


def call(method, url, params=None, payload=None, ok=(200, 204)):
    """Appel authentifié. Renvoie le corps décodé ; lève GoogleError sinon."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"Authorization": f"Bearer {access_token()}", "Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    status, data = _http(method, url, headers, body)
    if status not in ok:
        message = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        raise GoogleError(status, message or "réponse inattendue")
    return data


def quote(text):
    return urllib.parse.quote(text, safe="")


# ---------------------------------------------------------------------------
# API utilisées
# ---------------------------------------------------------------------------


def _pages(url, key):
    items, token = [], None
    while True:
        data = call("GET", url, params={"maxResults": 500, **({"pageToken": token} if token else {})})
        items += data.get(key, [])
        token = data.get("nextPageToken")
        if not token:
            return items


def buildings():
    customer = current_app.config["GOOGLE_WORKSPACE_CUSTOMER"]
    return _pages(f"{DIRECTORY}/customer/{quote(customer)}/resources/buildings", "buildings")


def resources():
    customer = current_app.config["GOOGLE_WORKSPACE_CUSTOMER"]
    return _pages(f"{DIRECTORY}/customer/{quote(customer)}/resources/calendars", "items")


def events(calendar_id, start, end):
    """Événements de l'agenda entre deux instants (ISO 8601 avec fuseau), occurrences dépliées."""
    data = call(
        "GET",
        f"{CALENDAR}/calendars/{quote(calendar_id)}/events",
        params={"timeMin": start, "timeMax": end, "singleEvents": "true", "maxResults": 250},
    )
    return data.get("items", [])


def put_event(calendar_id, event_id, body):
    """Crée ou remplace l'événement `event_id`."""
    url = f"{CALENDAR}/calendars/{quote(calendar_id)}/events/{quote(event_id)}"
    try:
        return call("PUT", url, payload={**body, "id": event_id})
    except GoogleError as exc:
        if exc.status != 404:
            raise
    return call("POST", f"{CALENDAR}/calendars/{quote(calendar_id)}/events", payload={**body, "id": event_id})


def delete_event(calendar_id, event_id):
    url = f"{CALENDAR}/calendars/{quote(calendar_id)}/events/{quote(event_id)}"
    call("DELETE", url, ok=(200, 204, 404, 410))  # déjà parti : c'est ce qu'on voulait

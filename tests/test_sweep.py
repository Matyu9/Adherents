"""Balayage systématique : toutes les routes × tous les rôles, puis tous les liens de toutes les pages."""
import re
from collections import deque
from urllib.parse import urljoin, urlsplit

import pytest

from app.extensions import db
from app.models import Notification, Role
from app.services.activity import log

from .conftest import login, make_account

A, G, H = Role.ADMIN, Role.GESTIONNAIRE, Role.ADHERENT
ANONYMOUS = "anonyme"

# Qui a accès à quoi. Une route absente de ce tableau fait échouer les tests : tout nouvel écran doit se déclarer ici.
PUBLIC, LOGGED_IN, ADMIN_ONLY = "public", "connecté", "admin"
SETTINGS, JOURNAL, HIDDEN = "settings.manage", "activity.view", "caché"  # caché : 404 pour qui n'a pas settings.manage
ACCESS = {
    "static": PUBLIC, "auth.login": PUBLIC, "csp.report": PUBLIC, "settings.logo_file": PUBLIC,
    "auth.logout": LOGGED_IN, "auth.password": LOGGED_IN, "dashboard.index": LOGGED_IN,
    "account.index": LOGGED_IN, "account.display": LOGGED_IN, "account.revoke_session": LOGGED_IN,
    "account.revoke_others": LOGGED_IN, "notifications.index": LOGGED_IN, "notifications.mark_all_read": LOGGED_IN,
    "journal.index": JOURNAL,
    "roles.index": ADMIN_ONLY, "roles.create": ADMIN_ONLY, "roles.edit": ADMIN_ONLY, "roles.delete": ADMIN_ONLY,
    "settings.index": SETTINGS, "settings.save_branding": SETTINGS, "settings.set_logo": SETTINGS,
    "settings.remove_logo": SETTINGS, "settings.test_email": SETTINGS, "settings.run_tasks": SETTINGS,
    "settings.revoke_sessions": SETTINGS,
    "health.as_json": HIDDEN, "health.page": HIDDEN,
}
HOLDERS = {SETTINGS: {A}, JOURNAL: {A, G}, ADMIN_ONLY: {A}, HIDDEN: {A}}
ARGUMENTS = {"filename": "css/app.css", "key": "adherent", "session_id": "inconnue", "name": "absent"}


def url_of(app, rule):
    with app.test_request_context():
        return rule.build({arg: ARGUMENTS[arg] for arg in rule.arguments})[1]


def is_login_redirect(response):
    return response.status_code == 302 and urlsplit(response.location).path == "/connexion"


def test_every_route_is_declared(app):
    assert {rule.endpoint for rule in app.url_map.iter_rules()} == set(ACCESS)


def test_every_declared_permission_exists():
    from app.permissions import PERMISSIONS
    assert {SETTINGS, JOURNAL} <= set(PERMISSIONS)


@pytest.mark.parametrize("who", [ANONYMOUS, H, G, A])
def test_access_matrix(app, accounts, who):
    problems = []
    for rule in app.url_map.iter_rules():
        policy = ACCESS[rule.endpoint]
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            client = app.test_client()  # nouvelle session à chaque fois : une route peut déconnecter
            if who != ANONYMOUS:
                login(client, accounts[who])
            response = client.open(url_of(app, rule), method=method)
            code = response.status_code
            label = f"{who} {method} {rule.rule} → {code}"
            allowed = who in HOLDERS.get(policy, ()) or policy == PUBLIC or (policy == LOGGED_IN and who != ANONYMOUS)
            if code >= 500:
                problems.append(f"{label} : erreur serveur")
            elif allowed and (code == 403 or is_login_redirect(response) and rule.endpoint != "auth.logout"):
                problems.append(f"{label} : devrait être autorisé")
            elif not allowed and policy == HIDDEN and code != 404:
                problems.append(f"{label} : devrait répondre 404")
            elif not allowed and policy != HIDDEN and who == ANONYMOUS and not is_login_redirect(response):
                problems.append(f"{label} : devrait demander la connexion")
            elif not allowed and policy != HIDDEN and who != ANONYMOUS and code != 403:
                problems.append(f"{label} : devrait répondre 403")
    assert not problems, "\n".join(problems)


def test_forbidden_post_changes_nothing(client, accounts):
    """Refusé, c'est refusé : pas seulement un code d'erreur par-dessus une action déjà faite."""
    login(client, accounts[H])
    assert client.post("/parametres/apparence", data={"name": "Piraté", "palette": "vert"}).status_code == 403
    assert client.post("/roles/nouveau", data={"name": "Piraté"}).status_code == 403
    assert db.session.get(Role, "pirate") is None
    from app.services import branding
    assert branding.current().name != "Piraté"


@pytest.fixture
def rich(app, accounts):
    """Une base représentative : du HTML dans les noms, un rôle sur mesure, un journal et des notifications."""
    db.session.add(Role(key="regie", name="Régie <b>", description="Rôle sur <i>mesure</i>",
                        permissions=["activity.view"]))
    make_account("regie", email="regie@asso.test", first_name="Léa <script>")
    log("settings.branding", "Apparence <em>modifiée</em>", user=accounts[A], target=accounts[H])
    for account in accounts.values():
        db.session.add(Notification(account_id=account.id, message="Dossier <b>à compléter</b>", link="/mon-compte"))
        db.session.add(Notification(account_id=account.id, message="Sans lien", read=True))
    db.session.commit()
    return accounts


INLINE = re.compile(r"""<[a-z][^>]*\son[a-z]+\s*=|<[a-z][^>]*\sstyle\s*=|<style\b|<script\b(?![^>]*\ssrc=)|href\s*=\s*["']javascript:""", re.I)
LINK = re.compile(r"""(?:href|src)="([^"#]+)""")


@pytest.mark.parametrize("who", [H, G, A])
def test_crawl_every_link(app, rich, who):
    """Suit tous les liens depuis l'accueil : aucun lien mort, aucune erreur, et aucune page ne casserait sous la CSP."""
    client = app.test_client()
    login(client, rich[who])
    seen, queue, problems = set(), deque(["/"]), []
    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        response = client.get(path)
        if response.status_code != 200:
            problems.append(f"{path} → {response.status_code}")
            continue
        if response.mimetype != "text/html":
            continue
        html = response.get_data(as_text=True)
        if match := INLINE.search(html):
            problems.append(f"{path} : contenu en ligne interdit par la CSP : {match.group(0)!r}")
        if "<script>" in html or "<b>à compléter" in html or "<em>modifiée" in html:
            problems.append(f"{path} : une donnée n'a pas été échappée")
        for link in LINK.findall(html):
            target = urlsplit(urljoin(path, link.replace("&amp;", "&")))
            if not target.netloc:
                queue.append(target.path + (f"?{target.query}" if target.query else ""))
    assert not problems, "\n".join(problems)
    assert "/mon-compte" in seen and any(p.startswith("/static/css/app.css") for p in seen)
    assert ("/roles" in seen) == (who == A)
    assert ("/parametres" in seen) == (who == A)
    assert ("/journal" in seen) == (who in (A, G))


def test_custom_role_gets_exactly_its_rights(app, rich):
    client = app.test_client()
    login(client, db.session.scalar(db.select(type(rich[A])).filter_by(email="regie@asso.test")))
    assert client.get("/journal").status_code == 200
    assert client.get("/parametres").status_code == 403
    assert client.get("/roles").status_code == 403
    assert client.get("/sante").status_code == 404


def test_right_removed_from_a_role_applies_at_once(client, accounts):
    login(client, accounts[G])
    assert client.get("/journal").status_code == 200
    db.session.add(Role(key=G, name="Gestionnaire", permissions=[]))
    db.session.commit()
    assert client.get("/journal").status_code == 403

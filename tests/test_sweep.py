"""Balayage systématique : toutes les routes × tous les rôles, puis tous les liens de toutes les pages."""
import re
from collections import deque
from urllib.parse import urljoin, urlsplit

import pytest

from app.extensions import db
from app.models import AccessGroup, Notification, OidcClient, OidcConsent, Role
from app.services.activity import log

from .conftest import login, make_account

A, G, H = Role.ADMIN, Role.GESTIONNAIRE, Role.ADHERENT
ANONYMOUS = "anonyme"

# Qui a accès à quoi. Une route absente de ce tableau fait échouer les tests : tout nouvel écran doit se déclarer ici.
PUBLIC, LOGGED_IN, ADMIN_ONLY = "public", "connecté", "admin"
SETTINGS, JOURNAL, HIDDEN = "settings.manage", "activity.view", "caché"  # caché : 404 pour qui n'a pas settings.manage
ACCOUNTS, GROUPS, CLIENTS = "account.manage", "group.manage", "client.manage"
DIR_VIEW, DIR_EDIT, DIR_MERGE, BANK = "directory.view", "directory.edit", "directory.merge", "bank.view"
LINKING = "account.manage+directory.edit"
TEACHERS = "teacher.manage"
ACCESS = {
    "static": PUBLIC, "auth.login": PUBLIC, "csp.report": PUBLIC, "settings.logo_file": PUBLIC,
    "auth.two_factor": PUBLIC, "auth.forgot": PUBLIC, "auth.token_link": PUBLIC,
    "oidc.discovery": PUBLIC, "oidc.jwks": PUBLIC, "oidc.authorize": PUBLIC, "oidc.resume": PUBLIC, "oidc.token": PUBLIC,
    "oidc.userinfo": PUBLIC, "oidc.consent": LOGGED_IN, "oidc.end_session": PUBLIC, "oidc.end_session_confirm": PUBLIC,
    "oidc.directory": PUBLIC,
    **{f"applications.{name}": CLIENTS for name in ("index", "create", "detail", "edit", "new_secret", "toggle", "delete",
                                                    "rotate_key", "export_accounts")},
    "account.revoke_consent": LOGGED_IN,
    "auth.logout": LOGGED_IN, "auth.password": LOGGED_IN, "dashboard.index": LOGGED_IN,
    "account.index": LOGGED_IN, "account.display": LOGGED_IN, "account.revoke_session": LOGGED_IN,
    "account.revoke_others": LOGGED_IN, "account.change_email": LOGGED_IN, "account.verify_email": LOGGED_IN,
    "account.totp": LOGGED_IN, "account.totp_codes": LOGGED_IN, "account.totp_disable": LOGGED_IN,
    "notifications.index": LOGGED_IN, "notifications.mark_all_read": LOGGED_IN,
    "journal.index": JOURNAL,
    **{f"accounts.{name}": ACCOUNTS for name in ("index", "create", "detail", "edit", "invite", "cut", "restore",
                                                  "set_groups", "reset_totp", "revoke_session", "revoke_sessions")},
    **{f"groups.{name}": GROUPS for name in ("index", "create", "edit", "delete")},
    "roles.index": ADMIN_ONLY, "roles.create": ADMIN_ONLY, "roles.edit": ADMIN_ONLY, "roles.delete": ADMIN_ONLY,
    "settings.index": SETTINGS, "settings.save_branding": SETTINGS, "settings.set_logo": SETTINGS,
    "settings.remove_logo": SETTINGS, "settings.test_email": SETTINGS, "settings.run_tasks": SETTINGS,
    "settings.revoke_sessions": SETTINGS,
    "health.as_json": HIDDEN, "health.page": HIDDEN,
    **{f"directory.{name}": DIR_VIEW for name in ("index", "person", "households", "household")},
    **{f"directory.{name}": DIR_EDIT for name in (
        "create_person", "edit_person", "archive_person", "restore_person", "add_person_phone", "add_household_phone",
        "delete_phone", "create_membership", "edit_membership", "create_household",
        "edit_household", "archive_household", "restore_household", "add_member", "update_member", "remove_member",
        "swap_guardians", "seasons", "create_season", "edit_season", "set_current_season", "delete_season")},
    **{f"teachers.{name}": TEACHERS for name in ("index", "create", "detail", "edit", "remove")},
    "directory.merge_person": DIR_MERGE, "directory.merge_household": DIR_MERGE,
    **{f"directory.{name}": BANK for name in ("create_bank_account", "reveal_iban", "delete_bank_account",
                                              "create_mandate", "edit_mandate", "elevate")},
    "directory.leave_secure": LOGGED_IN,
    **{f"directory.{name}": LINKING for name in ("create_account", "unlink_account", "link_accounts", "link_account",
                                                 "person_from_account")},
}
HOLDERS = {SETTINGS: {A}, JOURNAL: {A, G}, ADMIN_ONLY: {A}, HIDDEN: {A}, ACCOUNTS: {A}, GROUPS: {A}, CLIENTS: {A},
           DIR_VIEW: {A, G}, DIR_EDIT: {A, G}, DIR_MERGE: {A, G}, BANK: {A}, LINKING: {A}, TEACHERS: {A}}
ARGUMENTS = {"filename": "css/app.css", "key": "adherent", "sid": "inconnue", "name": "absent",
             "account_id": 1, "group_id": 999, "token": "inconnu", "client_pk": 999, "person_id": 999,
             "household_id": 999, "phone_id": 999, "membership_id": 999, "season_id": 999, "mandate_id": 999}
# Publiques, mais renvoient légitimement vers la connexion : rien en cours, ou lien inconnu.
SENDS_TO_LOGIN = {"auth.logout", "auth.two_factor", "auth.token_link", "oidc.end_session"}


def url_of(app, rule):
    with app.test_request_context():
        return rule.build({arg: ARGUMENTS[arg] for arg in rule.arguments})[1]


def is_login_redirect(response):
    return response.status_code == 302 and urlsplit(response.location).path == "/connexion"


def test_every_route_is_declared(app):
    assert {rule.endpoint for rule in app.url_map.iter_rules()} == set(ACCESS)


def test_every_declared_permission_exists():
    from app.permissions import PERMISSIONS
    assert {SETTINGS, JOURNAL, ACCOUNTS, GROUPS, CLIENTS, DIR_VIEW, DIR_EDIT, DIR_MERGE, BANK} <= set(PERMISSIONS)


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
            elif allowed and (code == 403 or is_login_redirect(response) and rule.endpoint not in SENDS_TO_LOGIN):
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
    """Une base représentative : du HTML dans les noms, un rôle sur mesure, un groupe, un journal et des notifications."""
    db.session.add(AccessGroup(key="regie", name="Groupe <u>régie</u>", description="<i>accès</i>",
                               members=[accounts[H]]))
    db.session.add(Role(key="regie", name="Régie <b>", description="Rôle sur <i>mesure</i>",
                        permissions=["activity.view"]))
    make_account("regie", email="regie@asso.test", first_name="Léa <script>")
    client = OidcClient(client_id="sweep", secret_hash="x", name="Appli <b>", redirect_uris=["https://a.test/cb"])
    db.session.add(client)
    db.session.flush()
    for account in accounts.values():
        db.session.add(OidcConsent(account_id=account.id, client_id=client.id, scope="email"))
    log("settings.branding", "Apparence <em>modifiée</em>", user=accounts[A], target=accounts[H])
    _directory(accounts)
    for account in accounts.values():
        db.session.add(Notification(account_id=account.id, message="Dossier <b>à compléter</b>", link="/mon-compte"))
        db.session.add(Notification(account_id=account.id, message="Sans lien", read=True))
    db.session.commit()
    return accounts


def _directory(accounts):
    """Un foyer complet, avec du HTML dans chaque champ affiché."""
    from datetime import date

    from app.models import (BankAccount, ExternalRef, Household, Membership, Person, Phone, Season, SepaMandate,
                            Teacher)
    from app.services import bank, directory

    season = Season(label="2026-2027", starts_on=date(2026, 9, 1), ends_on=date(2027, 8, 31))
    old = Season(label="2025-2026", starts_on=date(2025, 9, 1), ends_on=date(2026, 8, 31))
    household = Household(name="Famille <b>Dupont</b>", address_street="1 rue <i>Haute</i>", postal_code="76000",
                          city="Rouen", notes="<script>")
    parent = Person(first_name="Anne <script>", last_name="Dupont", email="anne@asso.test", birth_place="<i>Caen</i>",
                    birth_date=date(1980, 1, 2), notes="<em>modifiée</em>", account=accounts[H])
    child = Person(first_name="Zoé", last_name="Dupont <script>", birth_date=date(2015, 5, 6))
    teacher = Person(first_name="Paul", last_name="Prof", teacher=Teacher(status="CDI", notes="<b>x</b>"))
    db.session.add_all([season, old, household, parent, child, teacher])
    db.session.flush()
    directory.set_current_season(season)
    directory.add_member(household, parent, "guardian", "<b>mère</b>")
    directory.add_member(household, child, "dependent")
    household.phones.append(Phone(number="+33612345678", label="<b>maison</b>"))
    child.phones.append(Phone(number="+41791234567"))
    db.session.add_all([Membership(person=child, season=season, category="<b>scolaire</b>"),
                        Membership(person=child, season=old, status="withdrawn", withdrawn_on=date(2026, 1, 5)),
                        ExternalRef(kind="student", value="<b>42</b>", person=child)])
    account = BankAccount(household_id=household.id, holder_name="Anne <b>Dupont</b>", holder=parent)
    bank.set_iban(account, "FR7630006000011234567890189")
    db.session.add(account)
    db.session.add(SepaMandate(bank_account=account, rum="ADH-2026-ABC", signed_on=date(2026, 9, 1)))
    log("person.update", "Fiche <em>modifiée</em>", user=accounts[A], person=parent)


INLINE = re.compile(r"""<[a-z][^>]*\son[a-z]+\s*=|<[a-z][^>]*\sstyle\s*=|<style\b|<script\b(?![^>]*\ssrc=)|href\s*=\s*["']javascript:""", re.I)
LINK = re.compile(r"""(?:href|src)="([^"#]+)""")


@pytest.mark.parametrize("who", [H, G, A])
def test_crawl_every_link(app, rich, who):
    """Suit tous les liens depuis l'accueil : aucun lien mort, aucune erreur, et aucune page ne casserait sous la CSP."""
    client = app.test_client()
    login(client, rich[who])
    if who == A:  # pages bancaires : mode sécurisé ouvert, comme le ferait le trésorier
        from .conftest import totp_code
        rich[A].totp_last_counter = None
        db.session.commit()
        assert client.post("/annuaire/mode-securise", data={"code": totp_code()}).status_code == 302
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
            if not target.netloc and target.scheme not in ("data", "mailto", "tel"):  # data : QR code intégré
                queue.append(target.path + (f"?{target.query}" if target.query else ""))
    assert not problems, "\n".join(problems)
    assert "/mon-compte" in seen and any(p.startswith("/static/css/app.css") for p in seen)
    assert ("/roles" in seen) == (who == A)
    assert ("/parametres" in seen) == (who == A)
    assert ("/journal" in seen) == (who in (A, G))
    assert ("/comptes" in seen) == (who == A)
    assert ("/groupes" in seen) == (who == A)
    assert ("/applications" in seen) == (who == A)
    assert ("/annuaire" in seen) == (who in (A, G)) and ("/annuaire/foyers" in seen) == (who in (A, G))
    assert ("/annuaire/saisons" in seen) == (who in (A, G))
    assert ("/annuaire/rattachement" in seen) == (who == A)
    assert ("/professeurs" in seen) == (who == A) and any(p.startswith("/professeurs/") for p in seen) == (who == A)
    assert any(p.startswith("/annuaire/personnes/") for p in seen) == (who in (A, G))
    assert any(p.endswith("/afficher") for p in seen) == (who == A)
    assert any(p.endswith("/fusionner") for p in seen) == (who in (A, G))


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

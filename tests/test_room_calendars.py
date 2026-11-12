"""Gestion des cours, étape 5 : agendas des salles (Google Workspace). Aucun test ne contacte Google : un faux
serveur répond à la place de `google_workspace._http`."""

import json
import re
import urllib.parse
from datetime import date, datetime, timedelta

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from app.extensions import db
from app.models import Activity, Room, RoomCalendarJob, Site
from app.services import google_workspace as google
from app.services import lessons, room_calendars, tasks

from .conftest import enroll, login
from .test_lessons import BEFORE, OCT14, OCT28, at, generated, lesson_on, school  # noqa: F401 (fixtures)

ROOM_A = "salle-a@resource.calendar.google.com"
ROOM_C = "salle-c@resource.calendar.google.com"
ROOM_X = "auditorium@resource.calendar.google.com"


class FakeGoogle:
    """Jeton, annuaire des ressources, agendas : le strict nécessaire, avec les codes d'erreur de Google."""

    def __init__(self):
        self.tokens = 0
        self.assertions = []
        self.calendars = {ROOM_A: {}, ROOM_C: {}, ROOM_X: {}}  # agenda → {id: événement}
        self.unshared = set()
        self.down = False
        self.buildings = [{"buildingId": "b1", "buildingName": "Conservatoire"}]
        self.resources = [
            {"resourceEmail": ROOM_A, "resourceName": "Salle A", "buildingId": "b1", "capacity": 12},
            {"resourceEmail": ROOM_C, "resourceName": "Salle C rénovée", "buildingId": "b1", "capacity": 30},
            {"resourceEmail": ROOM_X, "resourceName": "Auditorium", "buildingId": "b1", "capacity": 200},
            {
                "resourceEmail": "photocopieuse@resource.calendar.google.com",
                "resourceName": "Photocopieuse",
                "resourceCategory": "OTHER",
            },
        ]
        self.calls = []

    def __call__(self, method, url, headers, body):
        if self.down:
            raise google.GoogleError(0, "Google injoignable (test)")
        parts = urllib.parse.urlsplit(url)
        path = parts.path
        self.calls.append((method, path))
        if url.startswith(google.TOKEN_URL):
            self.tokens += 1
            self.assertions.append(urllib.parse.parse_qs(body.decode())["assertion"][0])
            return 200, {"access_token": "jeton", "expires_in": 3600}
        assert headers["Authorization"] == "Bearer jeton"
        if path.endswith("/resources/buildings"):
            return 200, {"buildings": self.buildings}
        if path.endswith("/resources/calendars"):
            return 200, {"items": self.resources}
        match = re.fullmatch(r"/calendar/v3/calendars/([^/]+)/events(?:/([^/]+))?", path)
        calendar, event = urllib.parse.unquote(match.group(1)), match.group(2)
        if calendar not in self.calendars or calendar in self.unshared:
            return 404, {"error": {"message": "Not Found"}}
        events = self.calendars[calendar]
        if method == "GET":
            return 200, {"items": list(events.values())}
        if method == "PUT":
            if event not in events:
                return 404, {"error": {"message": "Not Found"}}
            events[event] = json.loads(body)
            return 200, events[event]
        if method == "POST":
            data = json.loads(body)
            if data["id"] in events:
                return 409, {"error": {"message": "duplicate"}}
            events[data["id"]] = data
            return 200, data
        if method == "DELETE":
            return (204, {}) if events.pop(event, None) is not None else (410, {})
        raise AssertionError(method)


@pytest.fixture
def fake(app, tmp_path, monkeypatch):
    key = RSAKey.generate_key(2048)
    credentials = {
        "type": "service_account",
        "client_email": "adherents@projet.iam.gserviceaccount.com",
        "private_key_id": "k1",
        "private_key": key.as_pem(private=True).decode(),
        "token_uri": google.TOKEN_URL,
    }
    path = tmp_path / "cle.json"
    path.write_text(json.dumps(credentials))
    app.config["GOOGLE_SERVICE_ACCOUNT_FILE"] = str(path)
    google._token.update(value=None, expires=0.0, key=None)
    server = FakeGoogle()
    server.key = key
    monkeypatch.setattr(google, "_http", server)
    return server


@pytest.fixture
def linked(fake, generated):
    generated["a"].calendar_id, generated["c"].calendar_id = ROOM_A, ROOM_C
    db.session.commit()
    return generated


def jobs():
    return db.session.scalars(db.select(RoomCalendarJob).order_by(RoomCalendarJob.id)).all()


# ---------------------------------------------------------------------------
# Jeton du compte de service
# ---------------------------------------------------------------------------


def test_sans_configuration_rien_ne_se_passe(generated):
    assert not room_calendars.enabled()
    assert room_calendars.busy(generated["a"], BEFORE, BEFORE) is None
    lessons.move(lesson_on(generated["flute"], OCT14), OCT14, at("16:00"), at("17:00"), generated["a"], now=BEFORE)
    assert jobs() == [] and room_calendars.deliver_pending() == (0, 0, 0)


def test_assertion_signee_et_jeton_garde(fake, app):
    google.call("GET", google.DIRECTORY + "/customer/my_customer/resources/buildings")
    google.call("GET", google.DIRECTORY + "/customer/my_customer/resources/buildings")
    assert fake.tokens == 1
    token = jwt.decode(fake.assertions[0], fake.key)
    assert token.header["alg"] == "RS256" and token.header["kid"] == "k1"
    claims = token.claims
    assert claims["iss"] == "adherents@projet.iam.gserviceaccount.com" and claims["aud"] == google.TOKEN_URL
    assert claims["scope"] == " ".join(google.SCOPES) and claims["exp"] - claims["iat"] == 3600
    assert "sub" not in claims  # pas de délégation sur tout le domaine


def test_jeton_refuse(fake, monkeypatch):
    monkeypatch.setattr(
        google, "_http", lambda *a: (400, {"error": "invalid_grant", "error_description": "clé révoquée"})
    )
    with pytest.raises(google.GoogleError, match="clé révoquée"):
        google.access_token()


# ---------------------------------------------------------------------------
# Lire l'occupation
# ---------------------------------------------------------------------------


def event(start, end, **extra):
    return {"start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


def test_occupation_de_la_salle(linked, fake):
    fake.calendars[ROOM_A] = {
        "r1": event("2026-10-14T18:00:00+02:00", "2026-10-14T19:30:00+02:00", id="r1"),
        "adherents7": event("2026-10-14T17:00:00+02:00", "2026-10-14T18:00:00+02:00", id="adherents7"),  # une séance
        "r2": event("2026-10-14T08:00:00Z", "2026-10-14T09:00:00Z", id="r2", transparency="transparent"),
        "r3": event("2026-10-14T12:00:00Z", "2026-10-14T13:00:00Z", id="r3", status="cancelled"),
    }
    start, end = datetime(2026, 10, 14, 17), datetime(2026, 10, 14, 20)
    assert room_calendars.busy(linked["a"], start, end) == "réservée 18:00-19:30"
    fake.calendars[ROOM_A] = {"j": {"id": "j", "start": {"date": "2026-10-14"}, "end": {"date": "2026-10-15"}}}
    assert room_calendars.busy(linked["a"], start, end) == "réservée toute la journée"
    assert room_calendars.busy(linked["b"], start, end) is None  # salle sans agenda
    fake.unshared.add(ROOM_A)
    assert room_calendars.busy(linked["a"], start, end) == "agenda illisible"
    fake.down = True
    assert room_calendars.busy(linked["c"], start, end) == "agenda illisible"


def test_reunion_signalee_au_deplacement(linked, fake):
    fake.calendars[ROOM_C] = {"r": event("2026-10-14T17:30:00+02:00", "2026-10-14T18:30:00+02:00", id="r")}
    lesson = lesson_on(linked["flute"], OCT14)
    warnings = lessons.conflicts(lesson, OCT14, at("17:00"), at("18:00"), linked["c"])
    assert "Salle C (École) est réservée dans son agenda (réservée 17:30-18:30)." in warnings
    rooms = dict(lessons.free_rooms(lesson, OCT14, at("17:00"), at("18:00")))
    assert rooms[linked["c"]] == "réservée 17:30-18:30" and rooms[linked["a"]] is None


# ---------------------------------------------------------------------------
# Réserver
# ---------------------------------------------------------------------------


def test_deplacer_libere_l_ancienne_salle_et_reserve_la_nouvelle(linked, fake):
    lesson = lesson_on(linked["flute"], OCT14)
    fake.calendars[ROOM_A][f"adherents{lesson.id}"] = {"id": f"adherents{lesson.id}"}
    lessons.move(lesson, OCT14, at("16:00"), at("17:00"), linked["c"], user=linked["paul_account"], now=BEFORE)
    assert [(j.calendar_id, j.action) for j in jobs()] == [(ROOM_A, "delete"), (ROOM_C, "put")]
    assert room_calendars.deliver_pending() == (2, 0, 0)
    assert f"adherents{lesson.id}" not in fake.calendars[ROOM_A]
    sent = fake.calendars[ROOM_C][f"adherents{lesson.id}"]
    assert sent["summary"] == "Flûte (Paul Prof)" and sent["location"] == "Salle C (École)"
    assert sent["start"] == {"dateTime": "2026-10-14T16:00:00", "timeZone": "Europe/Paris"}
    assert "Alice" not in json.dumps(sent) and "Martin" not in json.dumps(sent)  # aucun nom d'élève
    assert jobs() == []


def test_renvoyer_ne_double_pas(linked, fake):
    lesson = lesson_on(linked["flute"], OCT14)
    for start in ("16:00", "15:00"):
        lessons.move(lesson, OCT14, at(start), at("17:00"), linked["a"], now=BEFORE)
        room_calendars.deliver_pending()
    assert list(fake.calendars[ROOM_A]) == [f"adherents{lesson.id}"]
    assert fake.calendars[ROOM_A][f"adherents{lesson.id}"]["start"]["dateTime"].endswith("15:00:00")


def test_un_seul_envoi_en_attente_par_evenement(linked):
    lesson = lesson_on(linked["flute"], OCT14)
    lessons.move(lesson, OCT14, at("16:00"), at("17:00"), linked["a"], now=BEFORE)
    lessons.move(lesson, OCT14, at("15:00"), at("16:00"), linked["a"], now=BEFORE)
    (job,) = jobs()
    assert job.payload["start"]["dateTime"].endswith("15:00:00")


def test_annuler_retire_l_evenement(linked, fake):
    lesson = lesson_on(linked["flute"], OCT14)
    fake.calendars[ROOM_A][f"adherents{lesson.id}"] = {"id": f"adherents{lesson.id}"}
    lessons.cancel(lesson, "Grève", user=linked["manager"], now=BEFORE)
    room_calendars.deliver_pending()
    assert fake.calendars[ROOM_A] == {}


def test_seance_ajoutee_puis_retiree(linked, fake):
    lesson, _ = lessons.add_extra(
        linked["flute"], date(2026, 10, 31), at("10:00"), at("11:00"), linked["c"], now=BEFORE
    )
    room_calendars.deliver_pending()
    assert f"adherents{lesson.id}" in fake.calendars[ROOM_C]
    lessons.revert(lesson, linked["manager"])
    room_calendars.deliver_pending()
    assert fake.calendars[ROOM_C] == {}


def test_remplacant_change_le_titre(linked, fake):
    lesson = lesson_on(linked["flute"], OCT14)
    lessons.set_substitute(lesson, linked["lea"], linked["manager"])
    room_calendars.deliver_pending()
    assert fake.calendars[ROOM_A][f"adherents{lesson.id}"]["summary"] == "Flûte (Léa Remplaçante)"


def test_premiere_mise_en_route(linked, fake):
    assert room_calendars.push_upcoming(today=OCT14) == 3  # flûte les 14, 21 et 28 en salle A
    room_calendars.deliver_pending()
    assert len(fake.calendars[ROOM_A]) == 3
    assert "3 séance(s) à envoyer" in db.session.scalar(
        db.select(Activity.message).filter_by(action="room_calendar.push")
    )


def test_generation_envoie_les_seances_recalees(linked, fake):
    linked["flute"].room = linked["c"]
    lessons.generate(linked["season"], today=OCT14, apply=True)
    assert {(j.calendar_id, j.action) for j in jobs()} == {(ROOM_A, "delete"), (ROOM_C, "put")}
    assert len(jobs()) == 6


def test_google_en_panne_puis_retabli(linked, fake):
    lessons.move(lesson_on(linked["flute"], OCT14), OCT14, at("16:00"), at("17:00"), linked["a"], now=BEFORE)
    fake.down = True
    now = datetime.now()
    assert room_calendars.deliver_pending(now=now) == (0, 1, 0)
    (job,) = jobs()
    assert job.attempts == 1 and "injoignable" in job.last_error and job.next_try_at > now
    assert room_calendars.deliver_pending(now=now) == (0, 0, 0)  # pas encore l'heure de réessayer
    fake.down = False
    assert room_calendars.deliver_pending(now=now + timedelta(hours=1)) == (1, 0, 0)


def test_envoi_abandonne_apres_trop_d_essais(linked, fake):
    lessons.move(lesson_on(linked["flute"], OCT14), OCT14, at("16:00"), at("17:00"), linked["a"], now=BEFORE)
    fake.unshared.add(ROOM_A)
    for attempt in range(room_calendars.MAX_ATTEMPTS):  # chaque essai attend le précédent
        room_calendars.deliver_pending(now=datetime.now() + timedelta(days=attempt + 1))
    assert jobs() == []
    assert db.session.scalar(db.select(Activity).filter_by(action="room_calendar.drop")) is not None


def test_taches_periodiques(linked, fake):
    lessons.move(lesson_on(linked["flute"], OCT14), OCT14, at("16:00"), at("17:00"), linked["a"], now=BEFORE)
    db.session.commit()
    assert "agendas des salles : 1 envoi(s), 0 à réessayer, 0 abandonné(s)" in tasks.run()


# ---------------------------------------------------------------------------
# Salles de l'organisation
# ---------------------------------------------------------------------------


def test_apercu_des_salles(linked, fake):
    linked["b"].calendar_id = "disparue@resource.calendar.google.com"
    fake.unshared.add(ROOM_C)
    db.session.flush()
    plan = room_calendars.sync_plan()
    assert [r["resourceName"] for r, _ in plan.new] == ["Auditorium"]  # pas la photocopieuse
    assert [(room.name, r["resourceName"]) for room, r in plan.renamed] == [("Salle C", "Salle C rénovée")]
    assert plan.gone == [linked["b"]] and plan.unshared == [linked["c"]]


def test_salle_du_catalogue_reliee_par_son_nom(fake, generated):
    generated["a"].name = "salle a"  # casse différente
    db.session.flush()
    plan = room_calendars.sync_plan(check_access=False)
    assert [(room, r["resourceEmail"]) for room, r in plan.linkable] == [(generated["a"], ROOM_A)]
    assert generated["b"] in plan.unlinked


def test_appliquer_les_choix(linked, fake):
    linked["b"].calendar_id = "disparue@resource.calendar.google.com"
    db.session.flush()
    done = room_calendars.apply_sync(create={ROOM_X}, rename={linked["c"].id}, archive={linked["b"].id})
    assert done == {"créées": 1, "renommées": 1, "reliées": 0, "archivées": 1}
    auditorium = db.session.scalar(db.select(Room).filter_by(calendar_id=ROOM_X))
    assert (auditorium.name, auditorium.capacity, auditorium.site.name, auditorium.site.external_id) == (
        "Auditorium",
        200,
        "Conservatoire",
        "b1",
    )
    assert linked["c"].name == "Salle C rénovée" and linked["c"].capacity == 30 and linked["b"].archived
    # Une seconde fois : le site du bâtiment est retrouvé par son identifiant, pas recréé.
    fake.resources.append(
        {"resourceEmail": "studio@resource.calendar.google.com", "resourceName": "Studio", "buildingId": "b1"}
    )
    room_calendars.apply_sync(create={"studio@resource.calendar.google.com"})
    assert db.session.scalar(db.select(db.func.count(Site.id)).filter_by(name="Conservatoire")) == 1


def test_relier_une_salle(fake, generated):
    room_calendars.apply_sync(link={generated["b"].id: ROOM_X, generated["c"].id: "inconnue@x"})
    assert (
        generated["b"].calendar_id == ROOM_X and generated["b"].capacity == 200 and generated["c"].calendar_id is None
    )
    room_calendars.apply_sync(link={generated["a"].id: ROOM_X})  # déjà reliée à une autre salle
    assert generated["a"].calendar_id is None


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------


@pytest.fixture
def manager_client(client, generated):
    login(client, enroll(generated["manager"]))
    return client


def page(response):
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


def test_ecran_absent_sans_configuration(manager_client):
    assert manager_client.get("/catalogue/salles-workspace").status_code == 404
    assert manager_client.post("/catalogue/salles-workspace/envoyer").status_code == 404
    assert "Salles de l&#39;organisation" not in page(manager_client.get("/catalogue/referentiels"))


def test_ecran_des_salles_de_l_organisation(manager_client, fake, linked):
    fake.unshared.add(ROOM_C)
    assert "Salles de l'organisation" in page(manager_client.get("/catalogue/referentiels"))
    html = page(manager_client.get("/catalogue/salles-workspace"))
    assert "Auditorium" in html and "Salle C rénovée" in html and "Agenda non partagé" in html
    response = manager_client.post("/catalogue/salles-workspace", data={"create": ROOM_X})
    assert response.status_code == 302
    assert db.session.scalar(db.select(Room).filter_by(calendar_id=ROOM_X)) is not None
    html = page(manager_client.post("/catalogue/salles-workspace/envoyer", follow_redirects=True))
    assert "séance(s) à envoyer" in html


def test_ecran_google_injoignable(manager_client, fake):
    fake.down = True
    html = page(manager_client.get("/catalogue/salles-workspace"))
    assert "Impossible de lire les salles" in html
    html = page(manager_client.post("/catalogue/salles-workspace", data={"create": ROOM_X}, follow_redirects=True))
    assert "Rien n&#39;a été changé" in html


def test_agenda_d_une_salle_saisi_a_la_main(manager_client, generated):
    generated["a"].calendar_id = ROOM_A
    db.session.commit()
    room = generated["c"]
    data = {"site_id": room.site_id, "name": room.name, "capacity": "25", "calendar_id": ROOM_A}
    html = page(manager_client.post(f"/catalogue/salles/{room.id}/modifier", data=data))
    assert "Cet agenda est déjà celui de Salle A (École)" in html
    data["calendar_id"] = ROOM_C
    assert manager_client.post(f"/catalogue/salles/{room.id}/modifier", data=data).status_code == 302
    room = db.session.get(Room, room.id)
    assert (room.calendar_id, room.capacity) == (ROOM_C, 25)


def test_effectif_au_dessus_des_places(manager_client, generated):
    generated["a"].capacity = 4
    db.session.commit()
    flute = generated["flute"]
    data = {
        "discipline_id": flute.discipline_id,
        "room_id": generated["a"].id,
        "capacity": "10",
        "weekday": "2",
        "starts_at": "17:00",
        "ends_at": "18:00",
        "teacher_id": flute.teacher_id,
    }
    html = page(manager_client.post(f"/catalogue/cours/{flute.id}/modifier", data=data, follow_redirects=True))
    assert "dépasse les places de Salle A (École) (4)" in html


def test_ecran_reserve_a_catalog_edit(client, generated, fake):
    from app.models import Role as RoleModel

    db.session.add(RoleModel(key="catalogue", name="Catalogue", permissions=["catalog.view"]))
    db.session.commit()
    from .conftest import make_account

    login(client, make_account("catalogue", email="cat@asso.test"))
    assert client.get("/catalogue/salles-workspace").status_code == 403

import pytest

from app.extensions import db


def test_404_page_is_in_french(client):
    response = client.get("/nexiste-pas")
    assert response.status_code == 404 and "Page introuvable" in response.get_data(as_text=True)


def test_403_page_when_logged_in(client, member):
    html = client.get("/parametres").get_data(as_text=True)
    assert "Accès refusé" in html and "Tableau de bord" in html


def test_405_page(client):
    assert "Action impossible" in client.get("/deconnexion").get_data(as_text=True)


def test_guest_error_page_offers_login(client):
    assert "Se connecter" in client.get("/nexiste-pas").get_data(as_text=True)


def test_back_link_only_for_this_site(client):
    local = client.get("/nexiste-pas", headers={"Referer": "http://localhost/connexion?x=1"}).get_data(as_text=True)
    assert 'href="/connexion?x=1"' in local
    other = client.get("/nexiste-pas", headers={"Referer": "https://evil.test/piege"}).get_data(as_text=True)
    assert "evil.test" not in other


@pytest.fixture
def crashing(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/_panne")
    def panne():
        raise RuntimeError("secret interne")

    @app.route("/api/_panne")
    def panne_api():
        raise RuntimeError("secret interne")

    return app


def test_500_page_gives_a_reference_and_hides_details(crashing, caplog):
    response = crashing.test_client().get("/_panne")
    html = response.get_data(as_text=True)
    assert response.status_code == 500 and "Référence" in html and "secret interne" not in html
    assert "<aside" not in html  # page autonome, sans menu


def test_500_page_survives_a_broken_database(crashing, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def broken(*_args, **_kwargs):
        raise OperationalError("SELECT", {}, Exception("base illisible"))

    with monkeypatch.context() as patch:  # la base « casse » le temps de la requête, pas pendant le nettoyage
        patch.setattr(db.session, "execute", broken)
        response = crashing.test_client().get("/_panne")
    assert response.status_code == 500 and "Référence" in response.get_data(as_text=True)


def test_api_errors_are_json(crashing):
    response = crashing.test_client().get("/api/_panne")
    assert response.status_code == 500 and response.get_json()["error"]["code"] == 500
    missing = crashing.test_client().get("/api/absent")
    assert missing.status_code == 404 and missing.get_json()["error"]["message"]

"""Paramètres : personnalisation, logo, email de test, tâches, déconnexion générale."""

import io
import re
from pathlib import Path

import pytest
from PIL import Image

from app.extensions import db
from app.models import Activity, AppSetting, LoginSession, Role
from app.services import branding

from .conftest import login


def png(size=(600, 300), mode="RGBA", fmt="PNG"):
    out = io.BytesIO()
    Image.new(mode, size, (200, 30, 60, 128) if mode == "RGBA" else (200, 30, 60)).save(out, fmt)
    out.seek(0)
    return out


def upload(client, stream, name="logo.png"):
    return client.post(
        "/parametres/logo", data={"logo": (stream, name)}, content_type="multipart/form-data", follow_redirects=True
    )


def logo_setting():
    setting = db.session.get(AppSetting, "logo")
    return setting.value if setting else None


def test_defaults(client, admin, app):
    html = client.get("/parametres").get_data(as_text=True)
    assert app.config["ASSO_NAME"] in html and 'data-palette="bordeaux"' in html


def test_save_branding_everywhere(client, admin):
    response = client.post(
        "/parametres/apparence", data={"name": " Harmonie ", "subtitle": "Les adhérents", "palette": "vert"}
    )
    assert response.status_code == 302
    html = client.get("/").get_data(as_text=True)
    assert "Harmonie" in html and "Les adhérents" in html and 'data-palette="vert"' in html
    assert db.session.scalar(db.select(Activity).filter_by(action="settings.branding"))


def test_guest_pages_use_the_branding(app, client, admin):
    client.post("/parametres/apparence", data={"name": "Harmonie", "palette": "vert"})
    html = app.test_client().get("/connexion").get_data(as_text=True)
    assert "Harmonie" in html and 'data-palette="vert"' in html


def test_empty_values_go_back_to_defaults(client, admin):
    client.post("/parametres/apparence", data={"name": "Harmonie", "subtitle": "Sous", "palette": "vert"})
    client.post("/parametres/apparence", data={"name": "", "subtitle": "", "palette": "bordeaux"})
    assert db.session.scalar(db.select(db.func.count()).select_from(AppSetting)) == 0
    assert branding.DEFAULT_SUBTITLE in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize(
    "data",
    [
        {"name": "x", "palette": "fluo"},
        {"name": "x" * 81, "palette": "vert"},
        {"name": "x", "subtitle": "y" * 61, "palette": "vert"},
    ],
)
def test_invalid_branding_is_refused(client, admin, data):
    assert client.post("/parametres/apparence", data=data).status_code == 200
    assert db.session.get(AppSetting, "name") is None


def test_unknown_palette_in_database_falls_back(client, admin):
    db.session.add(AppSetting(key="palette", value="disparue"))
    db.session.commit()
    assert 'data-palette="bordeaux"' in client.get("/").get_data(as_text=True)


def test_save_rejects_unknown_palette(app):
    with pytest.raises(ValueError):
        branding.save("a", "b", "fluo")


def test_every_palette_has_its_light_and_dark_tones():
    css = Path("app/static/css")
    light, dark = (css / "app.css").read_text(), (css / "dark.css").read_text()
    for key in branding.PALETTES:
        assert f'html:root[data-palette="{key}"]' in light and f'html:root[data-palette="{key}"]' in dark
        assert branding.PALETTES[key][1] in light


def test_logo_is_resized_and_served_publicly(app, client, admin):
    assert "Logo enregistré" in upload(client, png()).get_data(as_text=True)
    relative = logo_setting()
    assert re.fullmatch(r"logo/[0-9a-f]{32}\.png", relative)
    image = Image.open(Path(app.config["UPLOAD_DIR"]) / relative)
    assert image.size == (256, 128) and image.mode == "RGBA"
    served = app.test_client().get("/" + relative)
    assert served.status_code == 200 and served.mimetype == "image/png" and "public" in served.headers["Cache-Control"]
    assert f'src="/{relative}"' in app.test_client().get("/connexion").get_data(as_text=True)


def test_logo_metadata_is_dropped(app, client, admin):
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "Position secrète"
    Image.new("RGB", (32, 32)).save(out, "JPEG", exif=exif)
    out.seek(0)
    upload(client, out, "photo.jpg")
    assert b"secr" not in (Path(app.config["UPLOAD_DIR"]) / logo_setting()).read_bytes()


def test_replacing_or_removing_the_logo_deletes_the_file(app, client, admin):
    upload(client, png())
    first = Path(app.config["UPLOAD_DIR"]) / logo_setting()
    upload(client, png())
    second = Path(app.config["UPLOAD_DIR"]) / logo_setting()
    assert not first.exists() and second.exists() and first != second
    client.post("/parametres/logo/supprimer")
    assert not second.exists() and logo_setting() is None


@pytest.mark.parametrize("content", [b"pas une image", b"", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"])
def test_non_image_is_refused(client, admin, content):
    html = upload(client, io.BytesIO(content), "logo.png").get_data(as_text=True)
    assert logo_setting() is None
    assert "image" in html.lower()


def test_no_file_is_a_warning(client, admin):
    html = client.post("/parametres/logo", data={}, follow_redirects=True).get_data(as_text=True)
    assert "Choisissez une image" in html


@pytest.mark.parametrize(
    "name", ["..%2F..%2Fadherents.db", "absent.png", "a" * 32 + ".png", "A" * 32 + ".PNG", "x.svg"]
)
def test_logo_route_serves_nothing_else(client, name):
    assert client.get(f"/logo/{name}").status_code == 404


def test_upload_too_large_is_a_clean_error(app, client, admin):
    app.config["MAX_CONTENT_LENGTH"] = 100
    response = client.post(
        "/parametres/logo", data={"logo": (io.BytesIO(b"x" * 1000), "l.png")}, content_type="multipart/form-data"
    )
    assert response.status_code == 413 and "volumineux" in response.get_data(as_text=True)


def test_email_test_without_smtp(client, admin):
    html = client.post("/parametres/email-test", follow_redirects=True).get_data(as_text=True)
    assert "pas configuré" in html


def test_run_tasks_shows_the_summary(client, admin):
    html = client.post("/parametres/taches", follow_redirects=True).get_data(as_text=True)
    assert "email(s) envoyé(s)" in html


def test_disconnect_everyone_but_me(app, client, admin, accounts):
    other = app.test_client()
    login(other, accounts[Role.ADHERENT])
    assert db.session.scalar(db.select(db.func.count(LoginSession.id))) == 2
    html = client.post("/parametres/deconnecter-tout", follow_redirects=True).get_data(as_text=True)
    assert "1 session(s) fermée(s)" in html
    assert client.get("/").status_code == 200 and other.get("/").status_code == 302
    assert db.session.scalar(db.select(Activity).filter_by(action="sessions.revoke_all"))

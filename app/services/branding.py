"""Personnalisation de l'interface par l'association : nom, sous-titre, logo et couleur principale.

Stockés dans AppSetting ; sans réglage, le nom vient de ASSO_NAME (.env) et la couleur est le bordeaux d'origine.
"""
from dataclasses import dataclass
from pathlib import Path

from flask import current_app, g, url_for
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import AppSetting
from . import photos

# clé : (libellé, couleur principale et fond pâle en thème clair), repris par les emails et la barre du navigateur.
# Les nuances sont dans css/app.css, celles du thème sombre dans css/dark.css.
PALETTES = {
    "bordeaux": ("Bordeaux", "#8b1e3f", "#f6e8ed"),
    "bleu": ("Bleu nuit", "#1f4e8c", "#e7eef8"),
    "sarcelle": ("Bleu canard", "#0f6b72", "#e2f2f3"),
    "vert": ("Vert sapin", "#1f6b4a", "#e5f2eb"),
    "violet": ("Violet", "#5b3a9e", "#efe9f8"),
    "terracotta": ("Terracotta", "#a3441f", "#f9ebe4"),
    "ardoise": ("Ardoise", "#3d4a5c", "#eaedf1"),
}
DEFAULT_PALETTE = "bordeaux"
DEFAULT_SUBTITLE = "Espace adhérents"
NAME_MAX = 80
SUBTITLE_MAX = 60


@dataclass(frozen=True)
class Branding:
    name: str
    subtitle: str
    palette: str
    logo: str | None  # chemin relatif dans UPLOAD_DIR

    @property
    def theme_color(self):
        return PALETTES[self.palette][1]

    @property
    def soft_color(self):
        return PALETTES[self.palette][2]

    @property
    def logo_url(self):
        return url_for("settings.logo_file", name=self.logo.rsplit("/", 1)[-1]) if self.logo else None


def current():
    """Réglages en cours, lus une fois par requête. Base illisible (page d'erreur, migration en attente) : défauts."""
    if "branding" not in g:
        try:
            values = dict(db.session.execute(db.select(AppSetting.key, AppSetting.value)).all())
        except SQLAlchemyError:
            db.session.rollback()
            values = {}
        palette = values.get("palette")
        g.branding = Branding(name=values.get("name") or current_app.config["ASSO_NAME"],
                              subtitle=values.get("subtitle") or DEFAULT_SUBTITLE,
                              palette=palette if palette in PALETTES else DEFAULT_PALETTE,
                              logo=values.get("logo"))
    return g.branding


def name():
    return current().name


def _set(key, value):
    """Enregistre (ou efface si vide) un réglage. Sans commit."""
    setting = db.session.get(AppSetting, key)
    if not value:
        if setting:
            db.session.delete(setting)
    elif setting:
        setting.value = value
    else:
        db.session.add(AppSetting(key=key, value=value))
    g.pop("branding", None)


def save(name, subtitle, palette):
    """Nom, sous-titre et couleur. Un nom ou un sous-titre vide revient à la valeur par défaut. Sans commit."""
    if palette not in PALETTES:
        raise ValueError(f"Palette inconnue : {palette}")
    _set("name", (name or "").strip()[:NAME_MAX])
    _set("subtitle", (subtitle or "").strip()[:SUBTITLE_MAX])
    _set("palette", "" if palette == DEFAULT_PALETTE else palette)


def _delete_logo_file(relative):
    if relative:
        (Path(current_app.config["UPLOAD_DIR"]) / relative).unlink(missing_ok=True)


def set_logo(file_storage):
    """Remplace le logo (lève photos.InvalidImage si le fichier n'est pas une image). Sans commit."""
    new = photos.save_logo(file_storage)
    _delete_logo_file(current().logo)
    _set("logo", new)


def remove_logo():
    _delete_logo_file(current().logo)
    _set("logo", "")

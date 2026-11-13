import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 50_000_000  # au-delà : refus (au lieu d'un simple avertissement)


class InvalidImage(ValueError):
    pass


def _open(file_storage, mode="RGB"):
    """Image envoyée, orientée d'après l'EXIF et en RGB (les métadonnées, dont la position GPS, sont perdues)."""
    try:
        image = Image.open(file_storage.stream)
        image.load()
    except Image.DecompressionBombError as exc:
        raise InvalidImage("Image trop grande.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("Le fichier n'est pas une image valide.") from exc
    return ImageOps.exif_transpose(image).convert(mode)


def _folder(name):
    folder = Path(current_app.config["UPLOAD_DIR"]) / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


LOGO_SIZE = 256
PORTRAIT_SIZE = 400


def save_portrait(file_storage):
    """Photo d'un professeur : recadrée au carré, 400 px, en JPEG sans métadonnées. Renvoie le chemin relatif."""
    image = ImageOps.fit(_open(file_storage), (PORTRAIT_SIZE, PORTRAIT_SIZE), Image.LANCZOS)
    name = f"{uuid.uuid4().hex}.jpg"
    image.save(_folder("profs") / name, "JPEG", quality=85, optimize=True)
    return f"profs/{name}"


def remove(relative):
    """Efface un fichier envoyé (ancienne photo), sans erreur s'il a déjà disparu."""
    if relative:
        (Path(current_app.config["UPLOAD_DIR"]) / relative).unlink(missing_ok=True)


def save_logo(file_storage):
    """Logo de l'association : réduit à 256 px au plus, en PNG pour garder la transparence. Renvoie le chemin relatif."""
    image = _open(file_storage, "RGBA")
    image.thumbnail((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    name = f"{uuid.uuid4().hex}.png"  # nom aléatoire : un nouveau logo n'est jamais servi périmé
    image.save(_folder("logo") / name, "PNG", optimize=True)
    return f"logo/{name}"

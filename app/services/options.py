"""Réglages simples de l'association, rangés dans AppSetting. Aucun n'a de valeur par défaut cachée : chaque fonction
dit ce que vaut un réglage absent."""

from ..extensions import db
from ..models import AppSetting

# Délai (en heures) avant une séance en deçà duquel le professeur ne la modifie plus. Absent : pas de limite.
TEACHER_DELAY = "lesson_teacher_delay_hours"
# Les familles voient les présences de leurs enfants. Absent : oui.
FAMILY_ATTENDANCE = "attendance_family_visible"
# Nombre d'absences non excusées à partir duquel un élève est signalé. Absent : pas de signalement.
ABSENCE_THRESHOLD = "absence_threshold"
# Coordonnées que les familles modifient elles-mêmes dans Ma famille, en cours d'année (liste séparée par des virgules).
# Absent : aucune, tout passe par le dossier d'inscription ou le secrétariat.
MEMBER_EDITABLE = "member_editable_fields"
MEMBER_FIELDS = {"telephones": "Téléphones", "email": "Adresses email", "adresse": "Adresse postale"}


def member_editable():
    return {key for key in (get(MEMBER_EDITABLE) or "").split(",") if key in MEMBER_FIELDS}


def set_member_editable(keys):
    kept = [key for key in MEMBER_FIELDS if key in set(keys)]
    put(MEMBER_EDITABLE, ",".join(kept) if kept else None)


def get(key):
    setting = db.session.get(AppSetting, key)
    return setting.value if setting is not None else None


def put(key, value):
    """Enregistre la valeur (texte) ; None efface le réglage."""
    setting = db.session.get(AppSetting, key)
    if value is None:
        if setting is not None:
            db.session.delete(setting)
        return
    if setting is None:
        setting = AppSetting(key=key, value="")
        db.session.add(setting)
    setting.value = str(value)


def positive_int(key):
    value = get(key)
    return int(value) if value and value.isdigit() and int(value) > 0 else None


def flag(key, default):
    value = get(key)
    return default if value is None else value == "1"

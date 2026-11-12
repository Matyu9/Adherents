"""Agendas des salles (Google Workspace, facultatif). Sans configuration, rien ne se passe : les fonctions ci-dessous
répondent « rien à signaler ». Voir docs/cours.md."""


def enabled():
    return False


def busy(room, start, end):
    """Occupation de la salle dans son agenda entre `start` et `end` (texte court), ou None."""
    return None


def lesson_changed(lesson, old_room_id):
    """La séance a changé : son événement doit suivre (et quitter l'agenda de l'ancienne salle)."""


def lesson_deleted(lesson):
    """La séance va disparaître : son événement aussi."""

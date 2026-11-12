"""Espace adhérent : ce que l'association sait du foyer de la personne connectée.

Une seule page, sans identifiant dans l'adresse : on ne montre que les foyers dont le compte est responsable (par sa
fiche de l'annuaire), et sa propre fiche. Rien ne se modifie ici : les changements passent par le dossier
d'inscription ou par le secrétariat.
"""

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Dossier, Enrollment, Membership
from ..services import attendance, directory, inventory, registration

bp = Blueprint("member", __name__, url_prefix="/mon-espace")


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _seasons_of(person):
    """Adhésions de la personne, la plus récente d'abord, avec leurs inscriptions actives."""
    rows = []
    for membership in sorted(person.memberships, key=lambda m: m.season.starts_on, reverse=True):
        enrollments = [e for e in membership.enrollments if e.status == Enrollment.ACTIVE]
        rows.append(
            (
                membership,
                sorted(
                    enrollments,
                    key=lambda e: (
                        e.course.weekday if e.course.weekday is not None else 7,
                        e.slot_starts_at or e.course.starts_at or e.course.created_at.time(),
                    ),
                ),
            )
        )
    return rows


@bp.route("")
@login_required
def index():
    person = getattr(current_user, "person", None)
    households = registration.guarded_households(current_user)
    people = []  # toutes les personnes montrées, pour le matériel prêté
    blocks = []
    for household in households:
        members = [link.person for link in household.guardians] + [link.person for link in household.dependents]
        people += members
        dossiers = db.session.scalars(
            db.select(Dossier).filter_by(household_id=household.id).order_by(Dossier.created_at.desc())
        ).all()
        blocks.append(
            {
                "household": household,
                "members": [(p, _seasons_of(p)) for p in members],
                "dossiers": dossiers,
                "consents": sorted(household.consents, key=lambda c: c.given_at, reverse=True),
            }
        )
    own = None
    if person is not None and person not in people:  # un élève de 16 ans ou plus, avec son propre compte
        own = (person, _seasons_of(person))
        people.append(person)
    loans = inventory.loans([p.uuid for p in people]) if people else None
    return render_template(
        "member/index.html",
        person=person,
        blocks=blocks,
        own=own,
        current=directory.current_season(),
        loans=loans,
        inventory_on=inventory.enabled(),
        active=Membership.ACTIVE,
        attendance_visible=attendance.family_visible(),
    )

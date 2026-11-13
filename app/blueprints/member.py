"""Ma famille : ce que l'association sait du foyer de la personne connectée.

Une seule page, sans identifiant dans l'adresse : on ne montre que les foyers dont le compte est responsable (par sa
fiche de l'annuaire), et sa propre fiche. Les coordonnées que l'association autorise (Paramètres → Adhérents) se
modifient depuis /ma-famille/coordonnees/<foyer> ; le reste passe par le dossier d'inscription ou le secrétariat.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Dossier, Enrollment, Membership
from ..services import attendance, contacts, directory, inventory, options, registration
from ..services.directory import DirectoryError

bp = Blueprint("member", __name__, url_prefix="/ma-famille")


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
        editable=options.member_editable(),
    )


@bp.route("/coordonnees/<int:household_id>", methods=["GET", "POST"])
@login_required
def contacts_form(household_id):
    household = next((h for h in registration.guarded_households(current_user) if h.id == household_id), None)
    if household is None:
        abort(404)
    allowed = options.member_editable()
    if not allowed:
        abort(404)
    error = None
    if request.method == "POST":
        try:
            changes = contacts.save(current_user, household, request.form)
        except DirectoryError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            db.session.commit()
            flash("Coordonnées enregistrées. Le secrétariat est prévenu." if changes else "Rien n'a changé.", "success")
            return redirect(url_for(".index"))
    return render_template(
        "member/contacts.html",
        household=household,
        people=contacts.editable_people(current_user, household),
        allowed=allowed,
        dossier=contacts.open_dossier(household),
        max_phones=contacts.MAX_PHONES,
        form=request.form if request.method == "POST" else None,
        error=error,
    )


@bp.route("/absences")
@login_required
def absences():
    """Séances des quatre semaines à venir, pour prévenir d'une absence (ou la retirer)."""
    return render_template(
        "member/absences.html", rows=attendance.family_lessons(current_user), days=attendance.NOTICE_DAYS
    )


@bp.route("/absences/prevenir", methods=["POST"])
@login_required
def declare_absence():
    from ..models import Enrollment, Lesson

    lesson = db.session.get(Lesson, request.form.get("lesson_id", type=int) or 0) or abort(404)
    enrollment = db.session.get(Enrollment, request.form.get("enrollment_id", type=int) or 0) or abort(404)
    try:
        attendance.declare_absence(current_user, lesson, enrollment)
    except attendance.AttendanceError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash("Absence prévenue : le professeur en est informé.", "success")
    return redirect(url_for(".absences"))


@bp.route("/absences/<int:notice_id>/retirer", methods=["POST"])
@login_required
def cancel_absence(notice_id):
    from ..models import AbsenceNotice

    notice = db.session.get(AbsenceNotice, notice_id) or abort(404)
    try:
        attendance.cancel_notice(current_user, notice)
    except attendance.AttendanceError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash("Absence retirée.", "success")
    return redirect(url_for(".absences"))

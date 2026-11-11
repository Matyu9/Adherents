"""Journal global : toutes les actions récentes, connexions comprises."""

from flask import Blueprint, render_template, request
from sqlalchemy import or_

from ..extensions import db, fold_contains
from ..models import Account, Activity
from ..permissions import require

bp = Blueprint("journal", __name__, url_prefix="/journal")

PER_PAGE = 100

# Familles d'actions proposées dans le filtre : libellé et préfixes des codes `Activity.action`.
FAMILIES = {
    "connexions": ("Connexions", ("auth.",)),
    "comptes": ("Comptes, groupes et rôles", ("account.", "group.", "role.", "sessions.")),
    "applications": ("Applications connectées", ("oidc.", "client.")),
    "parametres": ("Paramètres", ("settings.",)),
}


@bp.route("")
@require("activity.view")
def index():
    query = db.select(Activity)
    family = request.args.get("type")
    if family in FAMILIES:
        query = query.where(or_(*(Activity.action.startswith(p) for p in FAMILIES[family][1])))
    person = db.session.get(Account, request.args.get("personne", type=int) or 0)
    if person:
        query = query.where(or_(Activity.account_id == person.id, Activity.target_account_id == person.id))
    if q := (request.args.get("q") or "").strip():
        query = query.where(fold_contains(Activity.message, q))
    total = db.session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    page = max(request.args.get("page", 1, type=int), 1)
    entries = db.session.scalars(
        query.order_by(Activity.created_at.desc(), Activity.id.desc()).limit(PER_PAGE).offset((page - 1) * PER_PAGE)
    ).all()
    args = {
        k: v
        for k, v in (
            ("type", family if family in FAMILIES else None),
            ("personne", person.id if person else None),
            ("q", q),
        )
        if v
    }
    return render_template(
        "journal/index.html",
        entries=entries,
        total=total,
        page=page,
        pages=max((total + PER_PAGE - 1) // PER_PAGE, 1),
        args=args,
        everyone={k: v for k, v in args.items() if k != "personne"},
        families=FAMILIES,
        family=family,
        person=person,
        q=q,
    )

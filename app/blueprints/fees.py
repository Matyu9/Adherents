"""Tarifs : réglages, grille, règles, simulateur, situation des foyers et cotisations. Voir docs/tarifs.md.

Le formulaire d'une règle est construit à la main (groupes de conditions en nombre variable) : il marche sans
JavaScript, les boutons « Ajouter une condition » renvoyant simplement le formulaire. La protection CSRF est globale.
"""

import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db, fold
from ..models import (
    Assessment,
    AssessmentLine,
    Course,
    FeeItem,
    FeePick,
    FeeRule,
    Household,
    Membership,
    Person,
    Regie,
    Season,
)
from ..permissions import require
from ..services import catalog, directory, fees, pricing
from ..services.fees import FeeError
from ..services.pricing import PricingError

bp = Blueprint("fees", __name__, url_prefix="/tarifs")

SIMULATOR_ROWS = 4
BAND_ROWS = 6


def _selected_season():
    return directory.selected_season()


def _season_or_redirect():
    season, seasons = _selected_season()
    if season is None:
        flash("Créez d'abord une saison.", "error")
    return season, seasons


def _labels(season):
    return {item.id: item.label for item in fees.items(season)}


@bp.app_template_filter("euros")
def euros_filter(cents):
    return fees.euros(cents or 0)


@bp.app_template_filter("euros_input")
def euros_input(cents):
    """Montant tel qu'on le retape dans un champ : 42000 → « 420 », 1250 → « 12,50 »."""
    if cents is None:
        return ""
    whole, rest = divmod(abs(cents), 100)
    return ("-" if cents < 0 else "") + (f"{whole}" if not rest else f"{whole},{rest:02d}")


# ---------------------------------------------------------------------------
# Grille : éléments de la saison
# ---------------------------------------------------------------------------


@bp.route("")
@require("fee.view")
def index():
    season, seasons = _selected_season()
    items = fees.items(season) if season else []
    usage = {}
    if season:
        counts = dict(
            db.session.execute(
                db.select(Course.fee_item_id, db.func.count(Course.id))
                .where(Course.season_id == season.id)
                .group_by(Course.fee_item_id)
            ).all()
        )
        usage = {item.id: counts.get(item.id, 0) for item in items}
        without = counts.get(None, 0)
    else:
        without = 0
    return render_template(
        "fees/index.html",
        season=season,
        seasons=seasons,
        items=items,
        usage=usage,
        without=without,
        rules=fees.season_rules(season) if season else [],
    )


def _item_values(form, season):
    regie = db.session.get(Regie, form.get("regie", type=int) or 0)
    return {
        "label": form.get("label", ""),
        "price": fees.parse_euros(form.get("price")),
        "category": form.get("category", ""),
        "code": form.get("code", ""),
        "regie": regie,
        "notes": form.get("notes", ""),
    }


@bp.route("/elements/nouveau", methods=["GET", "POST"])
@require("fee.edit")
def create_item():
    season, _ = _season_or_redirect()
    if season is None:
        return redirect(url_for(".index"))
    return _item_form(season, None)


@bp.route("/elements/<int:item_id>/modifier", methods=["GET", "POST"])
@require("fee.edit")
def edit_item(item_id):
    item = db.session.get(FeeItem, item_id) or abort(404)
    return _item_form(item.season, item)


def _item_form(season, item):
    error = None
    values = {}
    if request.method == "POST":
        try:
            values = _item_values(request.form, season)
            if values["price"] is None:
                raise FeeError("Indiquez le prix de base.")
            saved = fees.save_item(season, values, item)
            db.session.commit()
            flash(f"Élément « {saved.label} » enregistré.", "success")
            return redirect(url_for(".index", saison=season.id))
        except FeeError as exc:
            db.session.rollback()
            error = str(exc)
            values = dict(request.form)
    elif item is not None:
        values = {
            "label": item.label,
            "price": euros_input(item.price),
            "category": item.category or "",
            "code": item.code or "",
            "regie": item.regie_id,
            "notes": item.notes or "",
        }
    return render_template(
        "fees/item_form.html",
        season=season,
        item=item,
        values=values,
        error=error,
        regies=fees.regies(),
        categories=fees.categories(season),
    )


@bp.route("/elements/<int:item_id>/supprimer", methods=["POST"])
@require("fee.edit")
def delete_item(item_id):
    item = db.session.get(FeeItem, item_id) or abort(404)
    season_id = item.season_id
    try:
        fees.delete_item(item)
        db.session.commit()
        flash("Élément supprimé.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for(".index", saison=season_id))


# ---------------------------------------------------------------------------
# Élément facturé par chaque cours
# ---------------------------------------------------------------------------


@bp.route("/cours")
@require("fee.view")
def courses():
    season, seasons = _selected_season()
    if season is None:
        return redirect(url_for(".index"))
    rows = catalog.sorted_courses(db.session.scalars(catalog.course_query(season)))
    return render_template("fees/courses.html", season=season, seasons=seasons, rows=rows, items=fees.items(season))


@bp.route("/cours", methods=["POST"])
@require("fee.edit")
def save_courses():
    season = db.session.get(Season, request.form.get("saison", type=int) or 0) or abort(404)
    rows = db.session.scalars(catalog.course_query(season))
    known = {item.id: item for item in fees.items(season)}
    changed = 0
    for course in rows:
        if f"course-{course.id}" not in request.form:
            continue
        wanted = request.form.get(f"course-{course.id}", type=int)
        item = known.get(wanted) if wanted else None
        if wanted and item is None:
            continue  # élément d'une autre saison ou supprimé entre-temps : ignoré
        if course.fee_item_id != (item.id if item else None):
            fees.set_course_item(course, item)
            changed += 1
    db.session.commit()
    flash(f"{changed} cours modifié(s).", "success")
    return redirect(url_for(".courses", saison=season.id))


# ---------------------------------------------------------------------------
# Réglages de la saison et régies
# ---------------------------------------------------------------------------


@bp.route("/reglages", methods=["GET", "POST"])
@require("fee.edit")
def settings():
    season, seasons = _season_or_redirect()
    if season is None:
        return redirect(url_for(".index"))
    current = fees.season_settings(season)
    error = None
    if request.method == "POST":
        form = request.form
        bands = []
        # Autant de lignes que le formulaire en envoie : le bouton « + Ajouter une tranche » en ajoute sans limite.
        rows = sorted({int(key.split("-")[1]) for key in form if re.fullmatch(r"band-\d{1,4}-(label|max)", key)})
        for k in rows:
            label = (form.get(f"band-{k}-label") or "").strip()
            ceiling = (form.get(f"band-{k}-max") or "").strip()
            if not label and not ceiling:
                continue
            bands.append({"label": label, "max": int(ceiling) if ceiling.isdigit() else (ceiling or None)})
        engine = {
            "ranking": form.get("ranking", "price_desc"),
            "ranking_categories": form.getlist("ranking_categories"),
            "rounding": form.get("rounding", "none"),
            "rounding_scope": form.get("rounding_scope", "line"),
        }
        cities = (form.get("resident_cities") or "").splitlines()
        try:
            fees.save_settings(season, engine, bands, form.get("default_band"), cities)
            db.session.commit()
            flash("Réglages enregistrés.", "success")
            return redirect(url_for(".settings", saison=season.id))
        except FeeError as exc:
            db.session.rollback()
            error = str(exc)
            current = fees.season_settings(season)
    engine = pricing.validate_settings(current.engine or {})
    return render_template(
        "fees/settings.html",
        season=season,
        seasons=seasons,
        settings=current,
        engine=engine,
        error=error,
        rankings=pricing.RANKINGS,
        roundings=pricing.ROUNDINGS,
        scopes=pricing.ROUNDING_SCOPES,
        categories=fees.categories(season),
        band_rows=max(BAND_ROWS, len(current.qf_bands) + 2),
        regies=fees.regies(include_archived=True),
    )


@bp.route("/regies", methods=["POST"])
@require("fee.edit")
def create_regie():
    try:
        regie = fees.create_regie(request.form.get("name"))
        from ..services.activity import log

        log("fee.regie", f"Régie « {regie.name} » ajoutée")
        db.session.commit()
        flash(f"Régie « {regie.name} » ajoutée.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for(".settings", saison=request.form.get("saison", type=int)) + "#regies")


@bp.route("/regies/<int:regie_id>/archiver", methods=["POST"])
@require("fee.edit")
def toggle_regie(regie_id):
    from datetime import datetime

    from ..services.activity import log

    regie = db.session.get(Regie, regie_id) or abort(404)
    regie.archived_at = None if regie.archived else datetime.now()
    log("fee.regie", f"Régie « {regie.name} » {'archivée' if regie.archived else 'rouverte'}")
    db.session.commit()
    flash(f"Régie « {regie.name} » {'archivée' if regie.archived else 'rouverte'}.", "success")
    return redirect(url_for(".settings", saison=request.form.get("saison", type=int)) + "#regies")


# ---------------------------------------------------------------------------
# Règles
# ---------------------------------------------------------------------------


@bp.route("/regles")
@require("fee.view")
def rules():
    season, seasons = _selected_season()
    records = fees.season_rules(season) if season else []
    labels = _labels(season) if season else {}
    described = [
        (
            r,
            fees.describe_conditions(r.conditions, labels),
            fees.describe_target(r.target, labels),
            fees.describe_effect(r.effect, labels),
        )
        for r in records
    ]
    return render_template(
        "fees/rules.html", season=season, seasons=seasons, described=described, line_effects=pricing.LINE_EFFECTS
    )


@bp.route("/regles/nouvelle", methods=["GET", "POST"])
@require("fee.edit")
def create_rule():
    season, _ = _season_or_redirect()
    if season is None:
        return redirect(url_for(".index"))
    return _rule_form(season, None)


@bp.route("/regles/<int:rule_id>/modifier", methods=["GET", "POST"])
@require("fee.edit")
def edit_rule(rule_id):
    rule = db.session.get(FeeRule, rule_id) or abort(404)
    return _rule_form(rule.season, rule)


CONDITION_KEY = re.compile(r"^c-(\d+)-(\d+)-field$")


def _posted_groups(form):
    """Groupes de conditions tels que saisis : [[{field, op, value, param}, …], …], lignes vides comprises."""
    groups = {}
    for key in form:
        if match := CONDITION_KEY.match(key):
            g, i = int(match[1]), int(match[2])
            groups.setdefault(g, {})[i] = {
                name: form.get(f"c-{g}-{i}-{name}", "") for name in ("field", "op", "value", "param")
            }
    return [[rows[i] for i in sorted(rows)] for _, rows in sorted(groups.items())]


def _empty_row():
    return {"field": "", "op": "", "value": "", "param": ""}


def _rule_rows(rule, labels):
    """Conditions d'une règle enregistrée, remises sous la forme du formulaire."""
    groups = []
    for group in rule.conditions:
        rows = []
        for c in group:
            value = c["value"]
            if c["field"] == "ligne.element":
                text = ", ".join(labels.get(v, "?") for v in value)
            elif isinstance(value, bool):
                text = "oui" if value else "non"
            elif c["op"] == "between":
                text = f"{value[0]} et {value[1]}"
            elif isinstance(value, list):
                text = ", ".join(value)
            else:
                text = str(value)
            param = c.get("param") or {}
            rows.append(
                {
                    "field": c["field"],
                    "op": c["op"],
                    "value": text,
                    "param": f"item:{param['item']}"
                    if "item" in param
                    else f"cat:{param['category']}"
                    if "category" in param
                    else "",
                }
            )
        groups.append(rows)
    return groups


def _parse_condition(row, items_by_label):
    name = row["field"]
    if name not in pricing.FIELDS:
        raise FeeError("Choisissez un critère pour chaque condition (ou retirez la ligne).")
    label, _, kind, param_kind = pricing.FIELDS[name]
    op, text = row["op"], (row["value"] or "").strip()
    if op not in pricing.OPERATORS[kind]:
        raise FeeError(f"« {label} » : choisissez une comparaison parmi {', '.join(pricing.OPERATORS[kind].values())}.")
    if kind == pricing.NUMBER:
        numbers = [int(n) for n in re.findall(r"-?\d+", text)]
        expected = 2 if op == "between" else 1
        if len(numbers) != expected:
            raise FeeError(f"« {label} » : indiquez {'deux nombres (« 10 et 14 »)' if expected == 2 else 'un nombre'}.")
        value = numbers if op == "between" else numbers[0]
    elif kind == pricing.BOOL:
        answer = fold(text)
        if answer not in ("oui", "non", "o", "n"):
            raise FeeError(f"« {label} » : répondez oui ou non.")
        value = answer.startswith("o")
    else:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if name == "ligne.element":
            unknown = [p for p in parts if fold(p) not in items_by_label]
            if unknown:
                raise FeeError(f"« {label} » : élément inconnu : {', '.join(unknown)}.")
            value = [items_by_label[fold(p)] for p in parts]
        else:
            value = parts
    condition = {"field": name, "op": op, "value": value}
    if param_kind == "selection" and row["param"]:
        kind_, _, rest = row["param"].partition(":")
        if kind_ == "item" and rest.isdigit():
            condition["param"] = {"item": int(rest)}
        elif kind_ == "cat" and rest:
            condition["param"] = {"category": rest}
    return condition


def _parse_rule(form, season):
    items = fees.items(season)
    items_by_label = {fold(item.label): item.id for item in items}
    kind = form.get("effect_kind", "")
    effect = {"kind": kind}
    if kind in ("percent", "total_percent"):
        value = (form.get("effect_value") or "").strip().replace(",", ".").replace("%", "").replace("−", "-").strip()
        if not value:
            raise FeeError("Indiquez le pourcentage (négatif pour une réduction).")
        effect["value"] = value
        if kind == "percent":
            effect["base"] = form.get("effect_base", "current")
    elif kind in ("amount", "total_amount", "fixed"):
        cents = fees.parse_euros(form.get("effect_value"))
        if cents is None:
            raise FeeError("Indiquez le montant.")
        effect["value"] = cents
    elif kind in ("replace", "add"):
        effect["item"] = form.get("effect_item", type=int)
        if effect["item"] is None:
            raise FeeError("Choisissez l'élément.")
        if kind == "add":
            effect["per"] = form.get("effect_per", "student")
    target = {}
    if kind in pricing.LINE_EFFECTS:
        for key in ("items", "exclude_items"):
            if values := [int(v) for v in form.getlist(f"target_{key}") if v.isdigit()]:
                target[key] = values
        for key in ("categories", "exclude_categories"):
            if values := [v for v in form.getlist(f"target_{key}") if v.strip()]:
                target[key] = values
    conditions = [
        [_parse_condition(row, items_by_label) for row in group if row["field"]] for group in _posted_groups(form)
    ]
    return {
        "name": form.get("name", ""),
        "group": form.get("group", ""),
        "notes": form.get("notes", ""),
        "effect": effect,
        "target": target,
        "conditions": [g for g in conditions if g],
    }


def _rule_form(season, rule):
    labels = _labels(season)
    error = None
    form = request.form
    if request.method == "POST":
        action = form.get("action", "save")
        groups = _posted_groups(form) or [[_empty_row()]]
        values = dict(form)
        values["target_items"] = form.getlist("target_items")
        values["target_categories"] = form.getlist("target_categories")
        values["target_exclude_items"] = form.getlist("target_exclude_items")
        values["target_exclude_categories"] = form.getlist("target_exclude_categories")
        if action == "add-group":
            groups.append([_empty_row()])
        elif action.startswith("add-condition-"):
            g = int(action.rsplit("-", 1)[1])
            if 0 <= g < len(groups):
                groups[g].append(_empty_row())
        elif action.startswith("remove-"):
            _, g, i = action.split("-")
            g, i = int(g), int(i)
            if 0 <= g < len(groups) and 0 <= i < len(groups[g]):
                groups[g].pop(i)
                groups = [group for group in groups if group] or [[_empty_row()]]
        else:
            try:
                raw = _parse_rule(form, season)
                saved = fees.save_rule(season, raw, rule)
                position = form.get("position", type=int)
                if position:
                    fees.move_rule_to(saved, position)
                if rule is None and form.get("activate"):
                    fees.set_rule_active(saved, True)
                db.session.commit()
                flash(
                    f"Règle « {saved.name} » enregistrée"
                    + ("" if saved.active else " en brouillon : voyez son effet avant de l'activer")
                    + ".",
                    "success",
                )
                return redirect(url_for(".rules", saison=season.id) + f"#regle-{saved.id}")
            except (FeeError, PricingError, ValueError) as exc:
                db.session.rollback()
                error = str(exc)
    elif rule is not None:
        groups = _rule_rows(rule, labels) or [[_empty_row()]]
        effect = rule.effect
        value = effect.get("value")
        values = {
            "name": rule.name,
            "group": rule.exclusive_group or "",
            "notes": rule.notes or "",
            "effect_kind": effect["kind"],
            "effect_value": (
                value.replace(".", ",") if isinstance(value, str) else euros_input(value) if value is not None else ""
            ),
            "effect_base": effect.get("base", "current"),
            "effect_item": effect.get("item"),
            "effect_per": effect.get("per", "student"),
            "target_items": [str(i) for i in rule.target.get("items", [])],
            "target_categories": rule.target.get("categories", []),
            "target_exclude_items": [str(i) for i in rule.target.get("exclude_items", [])],
            "target_exclude_categories": rule.target.get("exclude_categories", []),
            "position": rule.position + 1,
        }
    else:
        groups = [[_empty_row()]]
        values = {
            "effect_kind": "percent",
            "effect_base": "current",
            "effect_per": "student",
            "target_items": [],
            "target_categories": [],
            "target_exclude_items": [],
            "target_exclude_categories": [],
        }
    fields_by_level = {}
    for name, (label, level, _, _) in pricing.FIELDS.items():
        fields_by_level.setdefault(level, []).append((name, label))
    operators = {}
    for ops in pricing.OPERATORS.values():
        operators.update(ops)
    return render_template(
        "fees/rule_form.html",
        season=season,
        rule=rule,
        groups=groups,
        values=values,
        error=error,
        items=fees.items(season),
        categories=fees.categories(season),
        fields_by_level=fields_by_level,
        operators=operators,
        effects=pricing.EFFECTS,
        bases=pricing.BASES,
        count=len(fees.season_rules(season)),
        level_names={"household": "Foyer", "student": "Élève", "line": "Élément"},
    )


@bp.route("/regles/<int:rule_id>/deplacer", methods=["POST"])
@require("fee.edit")
def move_rule(rule_id):
    rule = db.session.get(FeeRule, rule_id) or abort(404)
    fees.move_rule(rule, -1 if request.form.get("sens") == "haut" else 1)
    db.session.commit()
    return redirect(url_for(".rules", saison=rule.season_id) + f"#regle-{rule.id}")


@bp.route("/regles/<int:rule_id>/activer", methods=["POST"])
@require("fee.edit")
def toggle_rule(rule_id):
    rule = db.session.get(FeeRule, rule_id) or abort(404)
    fees.set_rule_active(rule, not rule.active)
    db.session.commit()
    flash(f"Règle « {rule.name} » {'activée' if rule.active else 'remise en brouillon'}.", "success")
    return redirect(url_for(".rules", saison=rule.season_id) + f"#regle-{rule.id}")


@bp.route("/regles/<int:rule_id>/supprimer", methods=["POST"])
@require("fee.edit")
def delete_rule(rule_id):
    rule = db.session.get(FeeRule, rule_id) or abort(404)
    season_id = rule.season_id
    fees.delete_rule(rule)
    db.session.commit()
    flash("Règle supprimée.", "success")
    return redirect(url_for(".rules", saison=season_id))


@bp.route("/regles/<int:rule_id>/effet")
@require("fee.view")
def rule_impact(rule_id):
    rule = db.session.get(FeeRule, rule_id) or abort(404)
    impacts = sorted(fees.rule_impact(rule), key=lambda i: (i.delta, fold(i.household.name)))
    labels = _labels(rule.season)
    return render_template(
        "fees/impact.html",
        rule=rule,
        impacts=impacts,
        season=rule.season,
        total=sum(i.delta for i in impacts),
        households=len(fees.season_households(rule.season)),
        conditions=fees.describe_conditions(rule.conditions, labels),
        target=fees.describe_target(rule.target, labels),
        effect=fees.describe_effect(rule.effect, labels),
    )


# ---------------------------------------------------------------------------
# Simulateur
# ---------------------------------------------------------------------------


@bp.route("/simulateur")
@require("fee.view")
def simulator():
    season, seasons = _selected_season()
    if season is None:
        return redirect(url_for(".index"))
    args = request.args
    rows = max(SIMULATOR_ROWS, min(12, args.get("n", type=int) or SIMULATOR_ROWS))
    items = {item.id: item for item in fees.items(season)}
    students, picks, form_rows = [], [], []
    error = None
    for k in range(rows):
        row = {
            "name": (args.get(f"s{k}-name") or "").strip(),
            "age": args.get(f"s{k}-age", type=int),
            "adult": bool(args.get(f"s{k}-adult")),
            "tags": args.get(f"s{k}-tags", ""),
            "items": [i for i in args.getlist(f"s{k}-items", type=int) if i in items],
        }
        form_rows.append(row)
        if row["items"]:
            tags = frozenset(t.strip() for t in row["tags"].split(",") if t.strip())
            students.append(
                pricing.Student(
                    k + 1,
                    row["name"] or f"Élève {k + 1}",
                    age=row["age"],
                    adult=row["adult"] or (row["age"] or 0) >= 18,
                    tags=tags,
                    seniority=args.get(f"s{k}-seniority", type=int) or 0,
                )
            )
            picks += [(k + 1, item_id) for item_id in row["items"]]
    resident = {"1": True, "0": False}.get(args.get("resident", ""))
    household = pricing.Household(
        qf_band=args.get("band") or None,
        resident=resident,
        tags=frozenset(t.strip() for t in args.get("htags", "").split(",") if t.strip()),
    )
    drafts = [r for r in fees.season_rules(season) if not r.active and str(r.id) in args.getlist("drafts")]
    result = None
    if picks:
        try:
            result = fees.simulate(season, pricing.Order(household, tuple(students), tuple(picks)), drafts)
        except PricingError as exc:
            error = str(exc)
    names = {s.id: s.name for s in students}
    return render_template(
        "fees/simulator.html",
        season=season,
        seasons=seasons,
        rows=form_rows,
        items=items.values(),
        result=result,
        names=names,
        error=error,
        args=args,
        bands=[b["label"] for b in fees.season_settings(season).qf_bands],
        drafts=[r for r in fees.season_rules(season) if not r.active],
        chosen={r.id for r in drafts},
        more=rows + 2 if rows < 12 else None,
    )


# ---------------------------------------------------------------------------
# Copie d'une saison
# ---------------------------------------------------------------------------


@bp.route("/copier", methods=["GET", "POST"])
@require("fee.edit")
def copy():
    seasons = directory.seasons()
    source = db.session.get(Season, request.values.get("depuis", type=int) or 0)
    target = db.session.get(Season, request.values.get("vers", type=int) or 0)
    if request.method == "GET" and source is None and target is None and len(seasons) >= 2:
        target, source = seasons[0], seasons[1]
    if request.method == "POST":
        if source is None or target is None:
            flash("Choisissez les deux saisons.", "error")
            return redirect(url_for(".copy"))
        try:
            summary = fees.copy_season(source, target)
            db.session.commit()
            flash(f"Tarifs de {source.label} recopiés sur {target.label} : {summary}. Vérifiez les prix.", "success")
            return redirect(url_for(".index", saison=target.id))
        except (FeeError, PricingError) as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for(".copy", depuis=source.id, vers=target.id))
    preview = None
    if source is not None:
        preview = {
            "items": len(fees.items(source)),
            "rules": len(fees.season_rules(source)),
            "target_items": len(fees.items(target)) if target else 0,
            "target_rules": len(fees.season_rules(target)) if target else 0,
        }
    return render_template("fees/copy.html", seasons=seasons, source=source, target=target, preview=preview)


# ---------------------------------------------------------------------------
# Cotisations
# ---------------------------------------------------------------------------


@bp.route("/cotisations")
@require("fee.view")
def assessments():
    season, seasons = _selected_season()
    rows = []
    if season is not None:
        existing = {
            a.household_id: a
            for a in db.session.scalars(db.select(Assessment).where(Assessment.season_id == season.id))
        }
        households = fees.season_households(season)
        households += [a.household for a in existing.values() if a.household not in households]
        status = request.args.get("statut")
        for household in sorted(households, key=lambda h: fold(h.name)):
            assessment = existing.get(household.id)
            state = assessment.status if assessment else "none"
            if status == "review" and not (assessment and assessment.review_reasons):
                continue
            if status and status != "review" and status != state:
                continue
            rows.append((household, assessment))
    totals = {
        "count": sum(1 for _, a in rows if a),
        "validated": sum(1 for _, a in rows if a and a.status == "validated"),
        "amount": sum(a.total for _, a in rows if a),
    }
    return render_template(
        "fees/assessments.html",
        season=season,
        seasons=seasons,
        rows=rows,
        totals=totals,
        status=request.args.get("statut", ""),
    )


@bp.route("/cotisations/calculer", methods=["POST"])
@require("fee.assess")
def assess_all():
    season = db.session.get(Season, request.form.get("saison", type=int) or 0) or abort(404)
    done, skipped, reasons = fees.assess_season(season)
    db.session.commit()
    flash(
        f"{done} cotisation(s) calculée(s)" + (f", {skipped} laissée(s) telle(s) quelle(s)" if skipped else "") + ".",
        "success",
    )
    for reason in reasons[:5]:
        flash(reason, "info")
    return redirect(url_for(".assessments", saison=season.id))


def _household_and_season(household_id):
    household = db.session.get(Household, household_id) or abort(404)
    season, seasons = _selected_season()
    if season is None:
        abort(404)
    return household, season, seasons


@bp.route("/foyers/<int:household_id>")
@require("fee.view")
def household(household_id):
    household, season, seasons = _household_and_season(household_id)
    assessment = fees.get_assessment(household, season)
    prepared, preview = fees.compute(household, season)
    drift = fees.drift(assessment) if assessment else None
    record = fees.situation(household, season)
    settings = fees.season_settings(season)
    students = list(prepared.persons.values())
    picks = db.session.scalars(
        db.select(FeePick)
        .join(FeeItem)
        .where(FeePick.household_id == household.id, FeeItem.season_id == season.id)
        .order_by(FeePick.id)
    ).all()
    return render_template(
        "fees/household.html",
        household=household,
        season=season,
        seasons=seasons,
        assessment=assessment,
        preview=preview,
        prepared=prepared,
        drift=drift,
        names={p.id: p.full_name for p in prepared.persons.values()},
        band=fees.band_of(record, settings),
        resident=fees.resident_of(record, household, settings),
        situation=record,
        students=students,
        picks=picks,
        items=fees.items(season),
    )


def _back(household_id, season_id, anchor=""):
    return redirect(url_for(".household", household_id=household_id, saison=season_id) + anchor)


@bp.route("/foyers/<int:household_id>/calculer", methods=["POST"])
@require("fee.assess")
def assess_household(household_id):
    household, season, _ = _household_and_season(household_id)
    try:
        fees.assess(household, season, discard_corrections=bool(request.form.get("effacer")))
        db.session.commit()
        flash("Cotisation calculée.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(household.id, season.id)


@bp.route("/foyers/<int:household_id>/elements", methods=["POST"])
@require("fee.assess")
def add_pick(household_id):
    household, season, _ = _household_and_season(household_id)
    person = db.session.get(Person, request.form.get("person", type=int) or 0)
    item = db.session.get(FeeItem, request.form.get("item", type=int) or 0)
    if person is None or item is None or item.season_id != season.id:
        flash("Choisissez l'élève et l'élément.", "error")
    else:
        try:
            fees.add_pick(household, person, item, request.form.get("note"))
            db.session.commit()
            flash(f"« {item.label} » ajouté. Recalculez la cotisation pour en tenir compte.", "success")
        except FeeError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return _back(household.id, season.id, "#a-part")


@bp.route("/elements-pris/<int:pick_id>/retirer", methods=["POST"])
@require("fee.assess")
def remove_pick(pick_id):
    pick = db.session.get(FeePick, pick_id) or abort(404)
    household_id, season_id = pick.household_id, pick.item.season_id
    fees.remove_pick(pick)
    db.session.commit()
    flash("Élément retiré. Recalculez la cotisation pour en tenir compte.", "success")
    return _back(household_id, season_id, "#a-part")


def _line(line_id):
    return db.session.get(AssessmentLine, line_id) or abort(404)


@bp.route("/lignes/<int:line_id>/corriger", methods=["POST"])
@require("fee.assess")
def correct_line(line_id):
    line = _line(line_id)
    assessment = line.assessment
    try:
        amount = fees.parse_euros(request.form.get("amount"))
        if amount is None:
            raise FeeError("Indiquez le nouveau montant.")
        fees.correct_line(line, amount, request.form.get("reason"), current_user)
        db.session.commit()
        flash("Correction enregistrée.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(assessment.household_id, assessment.season_id)


@bp.route("/lignes/<int:line_id>/annuler", methods=["POST"])
@require("fee.assess")
def undo_line(line_id):
    line = _line(line_id)
    assessment = line.assessment
    try:
        fees.undo_correction(line, current_user)
        db.session.commit()
        flash("Correction annulée.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(assessment.household_id, assessment.season_id)


def _assessment(assessment_id):
    return db.session.get(Assessment, assessment_id) or abort(404)


@bp.route("/cotisations/<int:assessment_id>/valider", methods=["POST"])
@require("fee.assess")
def validate_assessment(assessment_id):
    assessment = _assessment(assessment_id)
    try:
        fees.validate(assessment, current_user)
        db.session.commit()
        flash("Cotisation validée.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(assessment.household_id, assessment.season_id)


@bp.route("/cotisations/<int:assessment_id>/rouvrir", methods=["POST"])
@require("fee.assess")
def reopen_assessment(assessment_id):
    assessment = _assessment(assessment_id)
    try:
        fees.reopen(assessment, request.form.get("reason"), current_user)
        db.session.commit()
        flash("Cotisation rouverte.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(assessment.household_id, assessment.season_id)


@bp.route("/cotisations/<int:assessment_id>/revue", methods=["POST"])
@require("fee.assess")
def acknowledge_review(assessment_id):
    assessment = _assessment(assessment_id)
    try:
        fees.acknowledge_review(assessment, current_user)
        db.session.commit()
        flash("Cotisation revue : rien à changer.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(assessment.household_id, assessment.season_id)


@bp.route("/cotisations/<int:assessment_id>/supprimer", methods=["POST"])
@require("fee.assess")
def delete_assessment(assessment_id):
    assessment = _assessment(assessment_id)
    household_id, season_id = assessment.household_id, assessment.season_id
    try:
        fees.delete_assessment(assessment, current_user)
        db.session.commit()
        flash("Cotisation supprimée.", "success")
    except FeeError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(household_id, season_id)


# ---------------------------------------------------------------------------
# Situation du foyer (quotient familial) : trésorier et direction
# ---------------------------------------------------------------------------


@bp.route("/foyers/<int:household_id>/situation", methods=["GET", "POST"])
@require("fee.situation")
def situation(household_id):
    household, season, seasons = _household_and_season(household_id)
    settings = fees.season_settings(season)
    memberships = [
        m
        for member in household.members
        for m in member.person.memberships
        if m.season_id == season.id and m.status == Membership.ACTIVE
    ]
    record = fees.situation(household, season)
    error = None
    if request.method == "POST":
        form = request.form
        quotient = (form.get("quotient") or "").strip().replace(" ", "")
        values = {
            "qf_band": form.get("qf_band"),
            "qf_proof": form.get("qf_proof"),
            "resident": {"1": True, "0": False}.get(form.get("resident", "")),
            "tags": (form.get("tags") or "").split(","),
        }
        try:
            if quotient and not quotient.isdigit():
                raise FeeError("Le quotient familial doit être un nombre entier positif.")
            values["quotient"] = int(quotient) if quotient else None
            fees.save_situation(household, season, values)
            for membership in memberships:
                fees.set_student_tags(membership, (form.get(f"tags-{membership.id}") or "").split(","))
            db.session.commit()
            flash("Situation enregistrée. Recalculez la cotisation pour en tenir compte.", "success")
            return _back(household.id, season.id)
        except FeeError as exc:
            db.session.rollback()
            error = str(exc)
            record = fees.situation(household, season)
    return render_template(
        "fees/situation.html",
        household=household,
        season=season,
        seasons=seasons,
        record=record,
        settings=settings,
        memberships=memberships,
        error=error,
        band=fees.band_of(record, settings),
        resident=fees.resident_of(record, household, settings),
    )

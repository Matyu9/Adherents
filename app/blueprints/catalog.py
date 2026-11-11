"""Catalogue : disciplines, niveaux, sites et salles communs à toutes les saisons, puis les cours de chaque saison et
les disciplines de chaque professeur. Voir docs/catalogue.md."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, SelectMultipleField, StringField, TextAreaField, TimeField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError
from wtforms.widgets import CheckboxInput, ListWidget

from ..extensions import db, fold
from ..filters import date_fr
from ..models import Course, Discipline, Level, Room, Season, Site, Teacher
from ..permissions import require
from ..services import catalog, directory, registration
from ..services.activity import diff, log
from ..services.catalog import CatalogError
from .directory import _blank, _strip, _text

bp = Blueprint("catalog", __name__, url_prefix="/catalogue")

KINDS = "any(disciplines, niveaux, sites, salles)"
COURSE_FIELDS = ("discipline_id", "level_id", "group_label", "teacher_id", "weekday", "starts_at", "ends_at",
                 "room_id", "capacity", "marked_full", "notes")


class Choice(SelectField):
    """Liste déroulante dont la valeur doit être l'une des options proposées (une valeur archivée ne l'est plus)."""

    def pre_validate(self, form):
        if not any(match for _, _, match, *_ in self.iter_choices()):
            raise ValidationError("Ce choix n'est pas proposé.")


def _optional_int(value):
    """Liste déroulante facultative : « — » (chaîne vide) → None."""
    if value in (None, ""):
        return None
    return int(value)


def _selected_season():
    """Saison demandée (?saison=), sinon la saison en cours, sinon la plus récente."""
    seasons = directory.seasons()
    wanted = request.args.get("saison", type=int)
    season = next((s for s in seasons if s.id == wanted), None) if wanted else None
    return season or directory.current_season() or (seasons[0] if seasons else None), seasons


# ---------------------------------------------------------------------------
# Formulaires
# ---------------------------------------------------------------------------

class CourseForm(FlaskForm):
    discipline_id = Choice("Discipline", coerce=_optional_int, validators=[DataRequired("Choisissez la discipline.")])
    level_id = Choice("Niveau", coerce=_optional_int)
    group_label = StringField("Groupe", validators=[Optional(), Length(max=80)], filters=[_strip],
                              description="Pour distinguer deux cours de même discipline et niveau : « groupe A », « ados »…")
    teacher_id = Choice("Professeur", coerce=_optional_int)
    weekday = Choice("Jour", coerce=_optional_int,
                          choices=[("", "—"), *((str(i), name) for i, name in enumerate(Course.WEEKDAYS))])
    starts_at = TimeField("Début", validators=[Optional()])
    ends_at = TimeField("Fin", validators=[Optional()])
    room_id = Choice("Salle", coerce=_optional_int)
    capacity = IntegerField("Effectif maximum", validators=[Optional(), NumberRange(min=1, max=500, message="Entre 1 et 500.")],
                            description="Vide : pas de limite, par exemple pour un cours individuel.")
    marked_full = BooleanField("Complet : ne plus proposer ce cours aux inscriptions")
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=5000)])

    def __init__(self, *args, season, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._season = season
        keep_discipline = course.discipline if course else None
        self.discipline_id.choices = [("", "—")] + [
            (str(d.id), d.name + (" (archivée)" if d.archived else ""))
            for d in _with(catalog.disciplines(), keep_discipline)]
        self.level_id.choices = [("", "—")] + [
            (str(lv.id), lv.name + (" (archivé)" if lv.archived else ""))
            for lv in _with(catalog.levels(), course.level if course else None)]
        self.room_id.choices = [("", "—")] + [
            (str(r.id), r.label + ("" if catalog.room_open(r) else " (fermée)"))
            for r in _with(catalog.rooms(), course.room if course else None)]
        self.teacher_id.choices = [("", "À trouver")] + [
            (str(t.person_id), t.person.sort_name + (f" (parti le {date_fr(t.left_on)})" if t.leaves_during(season) else ""))
            for t in catalog.teachers_for(season, keep=course.teacher if course else None)]

    def validate(self, extra_validators=None):
        """L'horaire se vérifie ici : un champ vide arrête ses propres validateurs (Optional)."""
        ok = super().validate(extra_validators)
        start, end = self.starts_at.data, self.ends_at.data
        if not self.starts_at.errors and not self.ends_at.errors:
            if (start is None) != (end is None):
                self.ends_at.errors.append("Indiquez le début et la fin, ou aucun des deux.")
                ok = False
            elif start is not None and end <= start:
                self.ends_at.errors.append("La fin doit suivre le début.")
                ok = False
        if start is not None and self.weekday.data is None and not self.weekday.errors:
            self.weekday.errors.append("Un horaire demande un jour.")
            ok = False
        return ok

    def values(self):
        return {"discipline_id": self.discipline_id.data, "level_id": self.level_id.data,
                "group_label": _blank(self.group_label.data), "teacher_id": self.teacher_id.data,
                "weekday": self.weekday.data, "starts_at": self.starts_at.data, "ends_at": self.ends_at.data,
                "room_id": self.room_id.data, "capacity": self.capacity.data, "marked_full": self.marked_full.data,
                "notes": _blank(self.notes.data)}


def _with(items, keep):
    """La liste des valeurs ouvertes, plus celle déjà choisie si elle a été archivée depuis."""
    return items + [keep] if keep is not None and keep not in items else items


class DisciplineForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)], filters=[_strip])
    category = SelectField("Type", default="", coerce=_text, validate_choice=False,
                           choices=[("", "—"), *((c, c) for c in Discipline.CATEGORIES)])

    def values(self):
        return {"name": self.name.data, "category": self.category.data if self.category.data in Discipline.CATEGORIES else None}


class LevelForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=60)], filters=[_strip])
    position = IntegerField("Ordre", default=0, validators=[Optional(), NumberRange(min=0, max=9999, message="Entre 0 et 9999.")],
                            description="Les niveaux sont classés par ordre croissant, puis par nom.")

    def values(self):
        return {"name": self.name.data, "position": self.position.data or 0}


class SiteForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)], filters=[_strip])
    address = StringField("Adresse", validators=[Optional(), Length(max=255)], filters=[_strip])

    def values(self):
        return {"name": self.name.data, "address": _blank(self.address.data)}


class RoomForm(FlaskForm):
    site_id = Choice("Site", coerce=_optional_int, validators=[DataRequired("Choisissez le site.")])
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)], filters=[_strip])

    def __init__(self, *args, room=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.site_id.choices = [("", "—")] + [(str(s.id), s.name) for s in _with(catalog.sites(), room.site if room else None)]

    def values(self):
        return {"site_id": self.site_id.data, "name": self.name.data}


# Chaque liste de référence : modèle, formulaire, libellés, et champ qui doit être unique (avec sa portée).
REFERENCES = {
    "disciplines": {"model": Discipline, "form": DisciplineForm, "one": "discipline", "title": "Disciplines", "fields": ("name", "category")},
    "niveaux": {"model": Level, "form": LevelForm, "one": "niveau", "title": "Niveaux", "fields": ("name", "position")},
    "sites": {"model": Site, "form": SiteForm, "one": "site", "title": "Sites", "fields": ("name", "address")},
    "salles": {"model": Room, "form": RoomForm, "one": "salle", "title": "Salles", "fields": ("site_id", "name")},
}


def _name_taken(kind, values, item):
    """Même nom, sans tenir compte des accents ni de la casse : « Flute » et « Flûte » sont la même discipline."""
    model = REFERENCES[kind]["model"]
    query = db.select(model)
    if model is Room:
        query = query.where(Room.site_id == values["site_id"])
    return any(other is not item and fold(other.name) == fold(values["name"]) for other in db.session.scalars(query))


def _item_label(item):
    return item.label if isinstance(item, Room) else item.name


# ---------------------------------------------------------------------------
# Cours d'une saison
# ---------------------------------------------------------------------------

@bp.route("")
@require("catalog.view")
def index():
    season, seasons = _selected_season()
    if season is None:
        return render_template("catalog/index.html", season=None, seasons=[], items=[], conflicts={}, filters={},
                               enrolled={})
    filters = {"discipline": request.args.get("discipline", type=int),
               "prof": request.args.get("prof") if request.args.get("prof") == "aucun" else request.args.get("prof", type=int),
               "jour": request.args.get("jour", type=int), "site": request.args.get("site", type=int)}
    if filters["jour"] is not None and not 0 <= filters["jour"] <= 6:
        filters["jour"] = None
    query = catalog.course_query(season, filters["discipline"], filters["prof"], filters["jour"], filters["site"])
    items = catalog.sorted_courses(db.session.scalars(query))
    every = db.session.scalars(catalog.course_query(season)).all()
    return render_template("catalog/index.html", season=season, seasons=seasons, items=items, filters=filters,
                           conflicts=catalog.season_conflicts(every), total=len(every),
                           enrolled=registration.enrolled_counts(season),
                           disciplines=catalog.disciplines(include_archived=True),
                           teachers=catalog.teachers_for(season), sites=catalog.sites(include_archived=True),
                           weekdays=Course.WEEKDAYS)


def _save_course(course, form, season):
    """Enregistre le formulaire, note la discipline du professeur et prévient des chevauchements."""
    changes = diff(course, COURSE_FIELDS, form.values(), mask=("notes",))
    db.session.flush()
    db.session.refresh(course)
    if catalog.note_discipline(course):
        flash(f"{course.discipline.name} ajoutée aux disciplines de {course.teacher.person.full_name} "
              f"pour {season.label}.", "info")
    others = db.session.scalars(catalog.course_query(season, weekday=course.weekday)).all()
    for other, reason in catalog.conflicts(course, others):
        flash(f"Chevauchement ({reason}) avec {catalog.describe(other)}.", "warning")
    return changes


@bp.route("/cours/nouveau", methods=["GET", "POST"])
@require("catalog.edit")
def create_course():
    season, _ = _selected_season()
    if season is None:
        flash("Créez d'abord une saison.", "error")
        return redirect(url_for(".index"))
    form = CourseForm(season=season)
    copy = db.session.get(Course, request.args.get("depuis", type=int) or 0) if request.method == "GET" else None
    if copy and copy.season_id == season.id:  # « dupliquer » : un autre groupe du même cours
        form = CourseForm(season=season, obj=copy, formdata=None)
        form.marked_full.data = False
    if form.validate_on_submit():
        course = Course(season=season, discipline_id=form.discipline_id.data)
        db.session.add(course)
        _save_course(course, form, season)
        log("course.create", f"Cours ajouté : {catalog.describe(course)}")
        db.session.commit()
        flash("Cours ajouté.", "success")
        return redirect(url_for(".index", saison=season.id))
    return render_template("catalog/course_form.html", form=form, season=season, course=None)


def _course(course_id):
    return db.session.get(Course, course_id) or abort(404)


@bp.route("/cours/<int:course_id>/modifier", methods=["GET", "POST"])
@require("catalog.edit")
def edit_course(course_id):
    course = _course(course_id)
    season = course.season
    form = CourseForm(obj=course, season=season, course=course)
    if form.validate_on_submit():
        if changes := _save_course(course, form, season):
            log("course.update", f"Cours modifié : {catalog.describe(course)}", changes=changes)
        db.session.commit()
        flash("Cours enregistré.", "success")
        return redirect(url_for(".index", saison=season.id))
    return render_template("catalog/course_form.html", form=form, season=season, course=course)


@bp.route("/cours/<int:course_id>/supprimer", methods=["POST"])
@require("catalog.edit")
def delete_course(course_id):
    course = _course(course_id)
    season_id = course.season_id
    if course.enrollments:
        flash(f"{len(course.enrollments)} élève(s) sont inscrits à ce cours : il ne peut pas être supprimé. "
              "Fermez-le aux inscriptions s'il ne doit plus être proposé.", "error")
        return redirect(url_for(".index", saison=season_id))
    log("course.delete", f"Cours supprimé : {catalog.describe(course)}")
    db.session.delete(course)
    db.session.commit()
    flash("Cours supprimé.", "success")
    return redirect(url_for(".index", saison=season_id))


# ---------------------------------------------------------------------------
# Copie d'une saison sur la suivante
# ---------------------------------------------------------------------------

@bp.route("/copier", methods=["GET", "POST"])
@require("catalog.edit")
def copy():
    """GET : choix des saisons et aperçu de la copie. POST : la copie, une fois l'aperçu vu."""
    seasons = directory.seasons()
    source = db.session.get(Season, request.values.get("depuis", type=int) or 0)
    target = db.session.get(Season, request.values.get("vers", type=int) or 0)
    if request.method == "GET" and source is None and target is None and len(seasons) >= 2:
        target, source = seasons[0], seasons[1]  # par défaut : de l'avant-dernière vers la plus récente
    plan = catalog.copy_plan(source, target) if source or target else None
    if request.method == "POST":
        try:
            plan = catalog.copy_season(source, target)
        except CatalogError as exc:
            flash(str(exc), "error")
            return redirect(url_for(".copy", depuis=source.id if source else None, vers=target.id if target else None))
        log("catalog.copy", f"Catalogue {source.label} recopié sur {target.label} : {len(plan.copied)} cours, "
                            f"{len(plan.skipped)} laissé(s) de côté, {len(plan.disciplines)} discipline(s) de professeurs")
        db.session.commit()
        flash(f"{len(plan.copied)} cours recopiés sur {target.label}. Vérifiez les cours sans professeur ou sans salle.",
              "success")
        return redirect(url_for(".index", saison=target.id))
    return render_template("catalog/copy.html", seasons=seasons, source=source, target=target, plan=plan)


# ---------------------------------------------------------------------------
# Disciplines des professeurs, par saison
# ---------------------------------------------------------------------------

@bp.route("/professeurs")
@require("catalog.view")
def teachers():
    season, seasons = _selected_season()
    rows = []
    if season is not None:
        counts = dict(db.session.execute(db.select(Course.teacher_id, db.func.count(Course.id))
                                         .where(Course.season_id == season.id).group_by(Course.teacher_id)).all())
        rows = [(t, catalog.disciplines_of(t, season), counts.get(t.person_id, 0))
                for t in catalog.teachers_for(season)]
        # Partis entre-temps, mais avec des cours cette saison : à remplacer, donc à montrer.
        rows += [(t, catalog.disciplines_of(t, season), counts[t.person_id])
                 for t in db.session.scalars(db.select(Teacher).where(Teacher.person_id.in_(counts.keys())))
                 if t not in {r[0] for r in rows}]
    return render_template("catalog/teachers.html", season=season, seasons=seasons, rows=rows)


class TeacherDisciplinesForm(FlaskForm):
    disciplines = SelectMultipleField("Disciplines", coerce=int, widget=ListWidget(prefix_label=False),
                                      option_widget=CheckboxInput())


@bp.route("/professeurs/<int:person_id>", methods=["GET", "POST"])
@require("catalog.edit")
def teacher_disciplines(person_id):
    teacher = db.session.get(Teacher, person_id) or abort(404)
    season, _ = _selected_season()
    if season is None:
        abort(404)
    current = catalog.disciplines_of(teacher, season)
    form = TeacherDisciplinesForm()
    form.disciplines.choices = [(d.id, d.name + (" (archivée)" if d.archived else ""))
                                for d in catalog.disciplines() + [d for d in current if d.archived]]
    if request.method == "GET":
        form.disciplines.data = [d.id for d in current]
    if form.validate_on_submit():
        wanted = [db.session.get(Discipline, i) for i in form.disciplines.data]
        try:
            added, removed = catalog.set_disciplines(teacher, season, wanted)
        except CatalogError as exc:
            flash(str(exc), "error")
            return redirect(url_for(".teacher_disciplines", person_id=person_id, saison=season.id))
        if added or removed:
            changes = {"disciplines": [", ".join(d.name for d in current) or None,
                                       ", ".join(d.name for d in catalog.disciplines_of(teacher, season)) or None]}
            log("teacher.disciplines", f"Disciplines de {teacher.person.full_name} en {season.label} modifiées",
                changes=changes, person=teacher.person)
        db.session.commit()
        flash("Disciplines enregistrées.", "success")
        return redirect(url_for(".teachers", saison=season.id))
    courses = catalog.sorted_courses(c for c in teacher.courses if c.season_id == season.id)
    return render_template("catalog/teacher_form.html", form=form, teacher=teacher, season=season, courses=courses)


# ---------------------------------------------------------------------------
# Listes de référence : disciplines, niveaux, sites, salles
# ---------------------------------------------------------------------------

@bp.route("/referentiels")
@require("catalog.view")
def references():
    lists = {"disciplines": catalog.disciplines(include_archived=True),
             "niveaux": catalog.levels(include_archived=True),
             "sites": catalog.sites(include_archived=True)}
    return render_template("catalog/references.html", lists=lists, usage=catalog.usage)


def _reference(kind, item_id):
    return db.session.get(REFERENCES[kind]["model"], item_id) or abort(404)


@bp.route(f"/<{KINDS}:kind>/nouveau", methods=["GET", "POST"])
@require("catalog.edit")
def create_item(kind):
    spec = REFERENCES[kind]
    form = spec["form"]()
    if kind == "salles" and request.method == "GET" and (site_id := request.args.get("site", type=int)):
        form.site_id.data = site_id
    if form.validate_on_submit():
        values = form.values()
        if _name_taken(kind, values, None):
            form.name.errors.append("Ce nom existe déjà" + (" sur ce site." if kind == "salles" else "."))
        else:
            item = spec["model"](**values)
            db.session.add(item)
            db.session.flush()
            log(f"{spec['model'].__tablename__}.create", f"{spec['one'].capitalize()} ajouté(e) : {_item_label(item)}")
            db.session.commit()
            flash(f"{spec['one'].capitalize()} ajouté(e).", "success")
            return redirect(url_for(".references") + f"#{kind}")
    return render_template("catalog/item_form.html", form=form, spec=spec, kind=kind, item=None)


@bp.route(f"/<{KINDS}:kind>/<int:item_id>/modifier", methods=["GET", "POST"])
@require("catalog.edit")
def edit_item(kind, item_id):
    spec = REFERENCES[kind]
    item = _reference(kind, item_id)
    form = spec["form"](obj=item, **({"room": item} if kind == "salles" else {}))
    if form.validate_on_submit():
        values = form.values()
        if _name_taken(kind, values, item):
            form.name.errors.append("Ce nom existe déjà" + (" sur ce site." if kind == "salles" else "."))
        else:
            if changes := diff(item, spec["fields"], values):
                log(f"{spec['model'].__tablename__}.update", f"{spec['one'].capitalize()} modifié(e) : {_item_label(item)}",
                    changes=changes)
            db.session.commit()
            flash("Enregistré.", "success")
            return redirect(url_for(".references") + f"#{kind}")
    return render_template("catalog/item_form.html", form=form, spec=spec, kind=kind, item=item)


@bp.route(f"/<{KINDS}:kind>/<int:item_id>/archiver", methods=["POST"])
@require("catalog.edit")
def archive_item(kind, item_id):
    spec = REFERENCES[kind]
    item = _reference(kind, item_id)
    catalog.archive(item)
    log(f"{spec['model'].__tablename__}.archive", f"{spec['one'].capitalize()} archivé(e) : {_item_label(item)}")
    db.session.commit()
    flash(f"{_item_label(item)} n'est plus proposé(e) pour les nouveaux cours. Les cours existants ne changent pas.",
          "success")
    return redirect(url_for(".references") + f"#{kind}")


@bp.route(f"/<{KINDS}:kind>/<int:item_id>/restaurer", methods=["POST"])
@require("catalog.edit")
def restore_item(kind, item_id):
    spec = REFERENCES[kind]
    item = _reference(kind, item_id)
    catalog.restore(item)
    log(f"{spec['model'].__tablename__}.restore", f"{spec['one'].capitalize()} rouvert(e) : {_item_label(item)}")
    db.session.commit()
    flash(f"{_item_label(item)} est de nouveau proposé(e).", "success")
    return redirect(url_for(".references") + f"#{kind}")


@bp.route(f"/<{KINDS}:kind>/<int:item_id>/supprimer", methods=["POST"])
@require("catalog.edit")
def delete_item(kind, item_id):
    spec = REFERENCES[kind]
    item = _reference(kind, item_id)
    if catalog.usage(item):
        flash(f"{_item_label(item)} a déjà servi : archivez-le (la) plutôt, l'historique des saisons passées en dépend.",
              "error")
    else:
        log(f"{spec['model'].__tablename__}.delete", f"{spec['one'].capitalize()} supprimé(e) : {_item_label(item)}")
        db.session.delete(item)
        db.session.commit()
        flash("Supprimé.", "success")
    return redirect(url_for(".references") + f"#{kind}")

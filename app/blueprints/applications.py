"""Applications connectées : les applis qui délèguent leur connexion à ce serveur, et les clés qui signent leurs jetons."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, ValidationError

from ..extensions import db, fold
from ..models import AccessGroup, OidcClient
from ..permissions import require
from ..services import groups, oidc
from ..services.activity import log

bp = Blueprint("applications", __name__, url_prefix="/applications")


class ApplicationForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    redirect_uris = TextAreaField("Adresses de retour (une par ligne)", validators=[DataRequired()])
    first_party = BooleanField("Appli de l'association : ne pas demander de consentement")

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def validate_name(self, field):
        name = fold(" ".join(field.data.split()))
        for client in oidc.all_clients():
            if fold(client.name) == name and client is not self._current:
                raise ValidationError("Une application porte déjà ce nom.")

    def validate_redirect_uris(self, field):
        for uri in field.data.split():
            if problem := oidc.redirect_uri_problem(uri):
                raise ValidationError(f"{uri} : {problem}.")


def _checked_groups(name):
    ids = request.form.getlist(name, type=int)
    return db.session.scalars(db.select(AccessGroup).where(AccessGroup.id.in_(ids))).all() if ids else []


def _apply(form, client):
    client.name = " ".join(form.name.data.split())
    client.redirect_uris = list(dict.fromkeys(form.redirect_uris.data.split()))
    client.first_party = form.first_party.data
    client.allowed_groups = _checked_groups("allowed_groups")
    client.shared_groups = _checked_groups("shared_groups")


def _get(client_pk):
    return db.session.get(OidcClient, client_pk) or abort(404)


def _discovery():
    return oidc.issuer() + url_for("oidc.discovery")


def _snapshot(client):
    """Ce que le journal compare quand une application est modifiée."""
    return {"nom": client.name, "adresses": client.redirect_uris, "association": client.first_party,
            "autorisés": sorted(g.key for g in client.allowed_groups),
            "transmis": sorted(g.key for g in client.shared_groups)}


def _render_form(form, client):
    return render_template("applications/form.html", form=form, client=client, all_groups=groups.all_groups(),
                           allowed=_form_groups("allowed_groups", client), shared=_form_groups("shared_groups", client))


def _form_groups(name, client):
    if request.method == "POST":
        return set(_checked_groups(name))
    return set(getattr(client, name)) if client else set()


@bp.route("")
@require("client.manage")
def index():
    return render_template("applications/index.html", clients=oidc.all_clients(), keys=oidc.published_keys(),
                           issuer=oidc.issuer(), discovery=_discovery())


@bp.route("/nouvelle", methods=["GET", "POST"])
@require("client.manage")
def create():
    form = ApplicationForm()
    if form.validate_on_submit():
        client = OidcClient(client_id=oidc.new_client_id())
        _apply(form, client)
        secret = oidc.set_secret(client)
        db.session.add(client)
        log("client.create", f"Application créée : {client.name}")
        db.session.commit()
        return render_template("applications/secret.html", client=client, secret=secret, created=True,
                               discovery=_discovery())
    return _render_form(form, None)


@bp.route("/<int:client_pk>")
@require("client.manage")
def detail(client_pk):
    return render_template("applications/detail.html", client=_get(client_pk), form=FlaskForm(),
                           discovery=_discovery())


@bp.route("/<int:client_pk>/modifier", methods=["GET", "POST"])
@require("client.manage")
def edit(client_pk):
    client = _get(client_pk)
    form = ApplicationForm(current=client, data={"name": client.name, "redirect_uris": "\n".join(client.redirect_uris),
                                                 "first_party": client.first_party})
    if form.validate_on_submit():
        before = _snapshot(client)
        _apply(form, client)
        after = _snapshot(client)
        log("client.update", f"Application modifiée : {client.name}",
            changes={key: [before[key], after[key]] for key in before if before[key] != after[key]})
        db.session.commit()
        flash("Application enregistrée.", "success")
        return redirect(url_for(".detail", client_pk=client.id))
    return _render_form(form, client)


@bp.route("/<int:client_pk>/secret", methods=["POST"])
@require("client.manage")
def new_secret(client_pk):
    client = _get(client_pk)
    secret = oidc.set_secret(client)
    log("client.secret", f"Nouveau secret pour {client.name}")
    db.session.commit()
    return render_template("applications/secret.html", client=client, secret=secret, created=False,
                           discovery=_discovery())


@bp.route("/<int:client_pk>/activer", methods=["POST"])
@require("client.manage")
def toggle(client_pk):
    client = _get(client_pk)
    client.active = not client.active
    if not client.active:
        oidc.revoke_tokens(client=client)
    log("client.toggle", f"Application {'rouverte' if client.active else 'fermée'} : {client.name}")
    db.session.commit()
    flash(f"« {client.name} » est {'ouverte' if client.active else 'fermée'}.", "success")
    return redirect(url_for(".detail", client_pk=client.id))


@bp.route("/<int:client_pk>/supprimer", methods=["POST"])
@require("client.manage")
def delete(client_pk):
    client = _get(client_pk)
    name = client.name
    db.session.delete(client)
    log("client.delete", f"Application supprimée : {name}")
    db.session.commit()
    flash(f"Application « {name} » supprimée.", "success")
    return redirect(url_for(".index"))


@bp.route("/cles/renouveler", methods=["POST"])
@require("client.manage")
def rotate_key():
    oidc.rotate_key()
    log("oidc.key_rotate", "Clé de signature renouvelée")
    db.session.commit()
    flash("Nouvelle clé de signature en service. L'ancienne reste publiée 30 jours.", "success")
    return redirect(url_for(".index"))

"""Applications connectées : les applis qui délèguent leur connexion à ce serveur, et les clés qui signent leurs jetons."""
import json
import re

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from ..extensions import db, fold
from ..models import AccessGroup, OidcClient
from ..permissions import require
from ..services import groups, logout, oidc
from ..services.activity import log

bp = Blueprint("applications", __name__, url_prefix="/applications")


class ApplicationForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    redirect_uris = TextAreaField("Adresses de retour (une par ligne)", validators=[DataRequired()])
    post_logout_redirect_uris = TextAreaField("Adresses après déconnexion (une par ligne, facultatif)",
                                              validators=[Optional()])
    backchannel_logout_uri = StringField("Adresse des avis de déconnexion (Back-Channel Logout, facultatif)",
                                         validators=[Optional(), Length(max=oidc.MAX_REDIRECT_URI)])
    first_party = BooleanField("Appli de l'association : ne pas demander de consentement")
    directory_access = BooleanField("Peut récupérer la liste des comptes qui y ont accès (avec son identifiant et son "
                                    "secret) et, pour une appli de l'association, les personnes de l'annuaire")

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def validate_name(self, field):
        name = fold(" ".join(field.data.split()))
        for client in oidc.all_clients():
            if fold(client.name) == name and client is not self._current:
                raise ValidationError("Une application porte déjà ce nom.")

    def validate_redirect_uris(self, field):
        _check_uris(field.data.split())

    def validate_post_logout_redirect_uris(self, field):
        _check_uris((field.data or "").split())

    def validate_backchannel_logout_uri(self, field):
        uri = (field.data or "").strip()
        if len(uri.split()) > 1:
            raise ValidationError("Une seule adresse.")
        _check_uris([uri] if uri else [])


def _check_uris(uris):
    for uri in uris:
        if problem := oidc.redirect_uri_problem(uri):
            raise ValidationError(f"{uri} : {problem}.")


def _checked_groups(name):
    ids = request.form.getlist(name, type=int)
    return db.session.scalars(db.select(AccessGroup).where(AccessGroup.id.in_(ids))).all() if ids else []


def _apply(form, client):
    client.name = " ".join(form.name.data.split())
    client.redirect_uris = list(dict.fromkeys(form.redirect_uris.data.split()))
    client.post_logout_redirect_uris = list(dict.fromkeys((form.post_logout_redirect_uris.data or "").split()))
    client.backchannel_logout_uri = (form.backchannel_logout_uri.data or "").strip() or None
    client.first_party = form.first_party.data
    client.directory_access = form.directory_access.data
    client.allowed_groups = _checked_groups("allowed_groups")
    client.shared_groups = _checked_groups("shared_groups")


def _get(client_pk):
    return db.session.get(OidcClient, client_pk) or abort(404)


def _discovery():
    return oidc.issuer() + url_for("oidc.discovery")


def _snapshot(client):
    """Ce que le journal compare quand une application est modifiée."""
    return {"nom": client.name, "adresses": client.redirect_uris, "après déconnexion": client.post_logout_redirect_uris,
            "avis de déconnexion": client.backchannel_logout_uri, "association": client.first_party,
            "liste des comptes": client.directory_access,
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
    client = _get(client_pk)
    return render_template("applications/detail.html", client=client, form=FlaskForm(), discovery=_discovery(),
                           pending_logouts=logout.pending_for(client), issuer=oidc.issuer())


@bp.route("/<int:client_pk>/modifier", methods=["GET", "POST"])
@require("client.manage")
def edit(client_pk):
    client = _get(client_pk)
    form = ApplicationForm(current=client, data={
        "name": client.name, "redirect_uris": "\n".join(client.redirect_uris),
        "post_logout_redirect_uris": "\n".join(client.post_logout_redirect_uris),
        "backchannel_logout_uri": client.backchannel_logout_uri or "", "first_party": client.first_party,
        "directory_access": client.directory_access})
    if form.validate_on_submit():
        before = _snapshot(client)
        _apply(form, client)
        after = _snapshot(client)
        logout.review_access(clients=[client])  # groupes autorisés ou transmis changés : les applis sont prévenues
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


@bp.route("/<int:client_pk>/comptes", methods=["POST"])
@require("client.manage")
def export_accounts(client_pk):
    """Le fichier que l'appli utilise pour rattacher chaque compte à sa fiche (même contenu que /oidc/comptes)."""
    client = _get(client_pk)
    data = oidc.directory(client)
    log("oidc.export", f"Comptes exportés pour {client.name} ({len(data['accounts'])})")
    db.session.commit()
    name = re.sub(r"[^a-z0-9]+", "-", fold(client.name)).strip("-") or "application"
    response = current_app.response_class(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                                          mimetype="application/json")
    response.headers["Content-Disposition"] = f'attachment; filename="comptes-{name}.json"'
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<int:client_pk>/activer", methods=["POST"])
@require("client.manage")
def toggle(client_pk):
    client = _get(client_pk)
    client.active = not client.active
    if not client.active:
        oidc.revoke_tokens(client=client)
        logout.review_access(clients=[client])  # chacun y est déconnecté
    log("client.toggle", f"Application {'rouverte' if client.active else 'fermée'} : {client.name}")
    db.session.commit()
    flash(f"« {client.name} » est {'ouverte' if client.active else 'fermée'}.", "success")
    return redirect(url_for(".detail", client_pk=client.id))


@bp.route("/<int:client_pk>/supprimer", methods=["POST"])
@require("client.manage")
def delete(client_pk):
    """Seulement une appli fermée : la fermeture a prévenu chacun qu'il en était déconnecté."""
    client = _get(client_pk)
    if client.active:
        flash("Fermez d'abord l'application : ses utilisateurs en seront déconnectés.", "error")
        return redirect(url_for(".detail", client_pk=client.id))
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

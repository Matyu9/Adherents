"""Gestion des comptes : qui peut se connecter, avec quel rôle et dans quels groupes."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import or_
from wtforms import EmailField, SelectField, StringField
from wtforms.validators import DataRequired, Length, Regexp, ValidationError

from ..extensions import db, fold_contains
from ..models import AccessGroup, Account, AccountToken, Activity, Role
from ..permissions import can, require
from ..services import accounts, groups, logout, mailer, oidc, roles, sessions, totp
from ..services.activity import diff, log

bp = Blueprint("accounts", __name__, url_prefix="/comptes")

PER_PAGE = 100


class AccountForm(FlaskForm):
    first_name = StringField("Prénom", validators=[DataRequired(), Length(max=80)])
    last_name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    email = EmailField("Email", validators=[
        DataRequired(), Length(max=255), Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Adresse email invalide.")],
        filters=[lambda value: value.strip() if value else value])
    role = SelectField("Rôle", default=Role.ADHERENT)

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current
        self.role.choices = accounts.assignable_roles(current_user)

    def validate_email(self, field):
        existing = db.session.scalar(db.select(Account).filter_by(email=field.data.strip().lower()))
        if existing is not None and existing is not self._current:
            raise ValidationError("Cette adresse est déjà utilisée par un autre compte.")


def _managed(account_id):
    """Compte que la personne connectée a le droit de gérer. Voir accounts.can_manage."""
    account = db.session.get(Account, account_id) or abort(404)
    if not accounts.can_manage(current_user, account):
        abort(403)
    return account


def _grant_access(account):
    """Envoie le lien pour choisir un mot de passe. S'il ne part pas, il s'affiche pour être transmis à la main."""
    purpose = AccountToken.RESET if account.password_hash else AccountToken.INVITE
    invited = purpose == AccountToken.INVITE
    raw = accounts.issue(account, purpose)
    log("account.invite" if invited else "account.reset_link",
        f"{'Invitation' if invited else 'Lien de réinitialisation'} pour {account.full_name}", target=account)
    db.session.commit()
    sent = (accounts.send_invitation if invited else accounts.send_reset)(account, raw)
    if sent:
        accounts.mark_delivered(raw)
        db.session.commit()
    return render_template("accounts/invited.html", account=account, sent=sent, invited=invited,
                           link=None if sent else accounts.link(raw), ttl=accounts.TTL_LABELS[purpose],
                           smtp=mailer.smtp_enabled())


@bp.route("")
@require("account.manage")
def index():
    query = db.select(Account)
    labels = dict(roles.choices())
    role = request.args.get("role")
    if role in labels:
        query = query.where(Account.role == role)
    status = request.args.get("statut")
    if status in accounts.STATUSES:
        query = query.where(accounts.status_condition(status))
    group = db.session.get(AccessGroup, request.args.get("groupe", type=int) or 0)
    if group:
        query = query.where(groups.is_member(group))  # cochés sur le compte ou donnés par le rôle
    if q := (request.args.get("q") or "").strip():
        query = query.where(or_(*(fold_contains(c, q) for c in (Account.first_name, Account.last_name, Account.email))))
    total = db.session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    page = max(request.args.get("page", 1, type=int), 1)
    items = db.session.scalars(query.order_by(Account.last_name, Account.first_name, Account.id)
                               .limit(PER_PAGE).offset((page - 1) * PER_PAGE)).all()
    args = {k: v for k, v in (("role", role if role in labels else None),
                              ("statut", status if status in accounts.STATUSES else None),
                              ("groupe", group.id if group else None), ("q", q)) if v}
    return render_template("accounts/index.html", items=items, total=total, page=page,
                           pages=max((total + PER_PAGE - 1) // PER_PAGE, 1), args=args, labels=labels, role=role,
                           status=status, statuses=accounts.STATUSES, status_of=accounts.status_of, q=q, group=group,
                           all_groups=groups.all_groups())


@bp.route("/nouveau", methods=["GET", "POST"])
@require("account.manage")
def create():
    form = AccountForm()
    if form.validate_on_submit():
        account = Account(first_name=form.first_name.data.strip(), last_name=form.last_name.data.strip(),
                          email=form.email.data.strip().lower(), role=form.role.data, active=True)
        db.session.add(account)
        db.session.flush()
        log("account.create", f"Compte créé : {account.full_name} ({account.role_label})", target=account)
        return _grant_access(account)
    return render_template("accounts/form.html", form=form, account=None)


@bp.route("/<int:account_id>")
@require("account.manage")
def detail(account_id):
    account = db.session.get(Account, account_id) or abort(404)
    activities = db.session.scalars(
        db.select(Activity).where(or_(Activity.account_id == account.id, Activity.target_account_id == account.id))
        .order_by(Activity.created_at.desc(), Activity.id.desc()).limit(30)).all()
    return render_template("accounts/detail.html", account=account, manage=accounts.can_manage(current_user, account),
                           status=accounts.status_of(account), statuses=accounts.STATUSES,
                           sessions=sessions.of(account), device_label=sessions.device_label, activities=activities,
                           all_groups=groups.all_groups() if can(current_user, "group.manage") else [],
                           recovery_left=totp.remaining_recovery_codes(account))


@bp.route("/<int:account_id>/modifier", methods=["GET", "POST"])
@require("account.manage")
def edit(account_id):
    account = _managed(account_id)
    form = AccountForm(obj=account, current=account)
    is_self = account.id == current_user.id
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if is_self and (email != account.email or form.role.data != account.role):
            flash("Votre adresse se change dans Mon compte, et votre rôle par un autre responsable.", "error")
            return render_template("accounts/form.html", form=form, account=account)
        old_email = account.email
        changes = diff(account, ("first_name", "last_name", "email", "role"),
                       {"first_name": form.first_name.data.strip(), "last_name": form.last_name.data.strip(),
                        "email": email, "role": form.role.data})
        if "email" in changes:
            # Rien ne prouve que la nouvelle adresse est la bonne, et un lien parti vers l'ancienne ne vaut plus.
            account.email_verified = False
            accounts.revoke(account)
        if changes:
            log("account.update", f"Compte modifié : {account.full_name}", changes=changes, target=account)
        if "role" in changes:
            logout.review_access(accounts=[account])  # les groupes du rôle viennent ou partent avec lui
        db.session.commit()
        if "email" in changes:
            accounts.send_email_changed_notice(account, old_email)
        flash("Modifications enregistrées.", "success")
        return redirect(url_for(".detail", account_id=account.id))
    return render_template("accounts/form.html", form=form, account=account)


@bp.route("/<int:account_id>/inviter", methods=["POST"])
@require("account.manage")
def invite(account_id):
    account = _managed(account_id)
    if not account.active:
        flash("Ce compte est coupé : rétablissez-le d'abord.", "error")
        return redirect(url_for(".detail", account_id=account.id))
    return _grant_access(account)


@bp.route("/<int:account_id>/couper", methods=["POST"])
@require("account.manage")
def cut(account_id):
    """Effet immédiat : plus de connexion, plus de session ouverte, plus aucun lien valable."""
    account = _managed(account_id)
    if account.id == current_user.id:
        flash("Vous ne pouvez pas couper votre propre compte.", "error")
    else:
        account.active = False
        sessions.revoke(account)
        accounts.revoke(account)
        oidc.revoke_tokens(account=account)
        log("account.cut", f"Compte coupé : {account.full_name}", target=account)
        db.session.commit()
        flash(f"Le compte de {account.full_name} est coupé.", "success")
    return redirect(url_for(".detail", account_id=account.id))


@bp.route("/<int:account_id>/retablir", methods=["POST"])
@require("account.manage")
def restore(account_id):
    account = _managed(account_id)
    account.active = True
    log("account.restore", f"Compte rétabli : {account.full_name}", target=account)
    db.session.commit()
    flash(f"Le compte de {account.full_name} est rétabli.", "success")
    return redirect(url_for(".detail", account_id=account.id))


@bp.route("/<int:account_id>/groupes", methods=["POST"])
@require("account.manage")
@require("group.manage")
def set_groups(account_id):
    account = _managed(account_id)
    if account.id == current_user.id and not current_user.is_admin:
        flash("Seul un administrateur peut changer vos groupes.", "error")
        return redirect(url_for(".detail", account_id=account.id))
    ids = request.form.getlist("groups", type=int)
    wanted = db.session.scalars(db.select(AccessGroup).where(AccessGroup.id.in_(ids))).all() if ids else []
    # Les groupes du rôle ne sont pas des cases à cocher : une appartenance cochée avant reste telle quelle.
    by_role = set(account.role_groups)
    wanted = [g for g in wanted if g not in by_role] + [g for g in account.groups if g in by_role]
    added, removed = groups.set_members_of(account, wanted)
    if added or removed:
        logout.review_access(accounts=[account])  # une appli qu'il quitte, ou dont les groupes transmis changent
        log("account.groups", f"Groupes de {account.full_name} modifiés",
            changes={"groupes": [sorted(g.key for g in removed), sorted(g.key for g in added)]}, target=account)
    db.session.commit()
    flash("Groupes enregistrés.", "success")
    return redirect(url_for(".detail", account_id=account.id))


@bp.route("/<int:account_id>/double-authentification/reinitialiser", methods=["POST"])
@require("account.manage")
def reset_totp(account_id):
    """Téléphone perdu et plus de code de secours. La personne reconfigurera la double authentification."""
    account = _managed(account_id)
    if account.id == current_user.id:
        flash("Vous ne pouvez pas réinitialiser votre propre double authentification.", "error")
        return redirect(url_for(".detail", account_id=account.id))
    totp.disable(account)
    sessions.revoke(account)
    log("account.totp_reset", f"Double authentification de {account.full_name} réinitialisée", target=account)
    db.session.commit()
    accounts.send_totp_reset_notice(account)
    flash("Double authentification réinitialisée, et appareils déconnectés.", "success")
    return redirect(url_for(".detail", account_id=account.id))


@bp.route("/<int:account_id>/sessions/<sid>/fermer", methods=["POST"])
@require("account.manage")
def revoke_session(account_id, sid):
    account = _managed(account_id)
    if sessions.revoke_one(account, sid):  # jamais celle de la requête en cours
        log("account.session", f"Session de {account.full_name} fermée", target=account)
        db.session.commit()
        flash("Appareil déconnecté.", "success")
    return redirect(url_for(".detail", account_id=account.id) + "#sessions")


@bp.route("/<int:account_id>/sessions/fermer", methods=["POST"])
@require("account.manage")
def revoke_sessions(account_id):
    account = _managed(account_id)
    sessions.revoke(account, keep_current=True)
    log("account.session", f"Toutes les sessions de {account.full_name} fermées", target=account)
    db.session.commit()
    flash("Appareils déconnectés.", "success")
    return redirect(url_for(".detail", account_id=account.id) + "#sessions")

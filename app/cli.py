import json

import click

from .extensions import db, fold
from .models import Account, OidcClient, Role
from .services import accounts, logout, oidc, sessions, totp
from .services.activity import log


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt="Email")
    @click.option("--first-name", prompt="Prénom")
    @click.option("--last-name", prompt="Nom")
    @click.password_option(prompt="Mot de passe")
    def create_admin(email, first_name, last_name, password):
        """Crée (ou promeut) un compte administrateur : c'est aussi l'accès de secours."""
        email = email.strip().lower()
        account = db.session.scalar(db.select(Account).filter_by(email=email))
        if account is None:
            account = Account(email=email, first_name=first_name, last_name=last_name)
            db.session.add(account)
        account.role = Role.ADMIN
        account.active = True
        account.set_password(password)
        if account.id:
            sessions.revoke(account)
            accounts.revoke(account)
            totp.disable(account)  # téléphone perdu : le secours doit rouvrir l'accès, le TOTP se reconfigure ensuite
        db.session.commit()
        logout.deliver_pending()  # les applis où ses anciennes sessions avaient servi
        click.echo(f"Administrateur prêt : {account.full_name} <{email}>")

    @app.cli.command("oidc-rotate-key")
    def oidc_rotate_key():
        """Met en service une nouvelle clé de signature OIDC (l'ancienne reste publiée 30 jours)."""
        key = oidc.rotate_key()
        log("oidc.key_rotate", "Clé de signature renouvelée en ligne de commande")
        db.session.commit()
        click.echo(f"Nouvelle clé en service : {key.kid}")

    @app.cli.command("oidc-export-accounts")
    @click.option("--application", "name", required=True, help="Nom de l'application, comme dans « Applications ».")
    @click.option("--output", type=click.File("w", encoding="utf-8"), default="-",
                  help="Fichier où écrire l'export (par défaut, la sortie standard).")
    def oidc_export_accounts(name, output):
        """Exporte en JSON les comptes qui ont accès à une application, pour les rattacher à ses propres fiches.

        Le fichier contient des noms et des adresses : à supprimer une fois le rattachement fait.
        """
        client = db.session.scalar(db.select(OidcClient).where(db.func.fold(OidcClient.name) == fold(name.strip())))
        if client is None:
            raise click.ClickException(f"Aucune application ne s'appelle « {name} ».")
        found = [account for account in db.session.scalars(db.select(Account).where(Account.active)
                                                           .order_by(Account.last_name, Account.first_name))
                 if oidc.may_access(client, account)]
        json.dump({"issuer": oidc.issuer(), "application": client.name, "accounts": [
            {"sub": a.sub, "email": a.email, "email_verified": a.email_verified, "first_name": a.first_name,
             "last_name": a.last_name} for a in found]}, output, ensure_ascii=False, indent=1)
        output.write("\n")
        log("oidc.export", f"Comptes exportés pour {client.name} ({len(found)}) en ligne de commande")
        db.session.commit()
        click.echo(f"{len(found)} compte(s) exporté(s) pour {client.name}.", err=True)

    @app.cli.command("run-tasks")
    def run_tasks():
        """Tâches périodiques (lancées par le service scheduler) : purges et envoi des emails en attente."""
        from .services.health import record_scheduler_run
        from .services.tasks import run

        click.echo(run())
        record_scheduler_run()

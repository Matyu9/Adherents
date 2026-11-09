import json

import click

from .extensions import db, fold
from .models import Account, OidcClient, Role
from .services import accounts, bank, logout, oidc, sessions, totp
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
        data = oidc.directory(client)
        json.dump(data, output, ensure_ascii=False, indent=1)
        output.write("\n")
        log("oidc.export", f"Comptes exportés pour {client.name} ({len(data['accounts'])}) en ligne de commande")
        db.session.commit()
        click.echo(f"{len(data['accounts'])} compte(s) exporté(s) pour {client.name}.", err=True)

    @app.cli.command("bank-generate-key")
    def bank_generate_key():
        """Affiche une nouvelle clé pour BANK_DATA_KEYS, avec le numéro suivant. Rien n'est enregistré."""
        try:
            version = max(bank.keys(), default=0) + 1
        except bank.BankKeyError:
            version = 1
        click.echo(f"{version}:{bank.new_key()}")
        click.echo("Ajoutez-la en tête de BANK_DATA_KEYS (séparée par une virgule), redémarrez, puis lancez "
                   "flask bank-rotate-key. Sauvegardez-la à part de la base.", err=True)

    @app.cli.command("bank-rotate-key")
    def bank_rotate_key():
        """Rechiffre tous les IBAN avec la clé la plus haute de BANK_DATA_KEYS. Les anciennes peuvent ensuite partir."""
        try:
            count = bank.rotate()
            version = bank.current_version()
        except bank.BankKeyError as exc:
            raise click.ClickException(str(exc)) from None
        log("bank.rotate", f"IBAN rechiffrés avec la clé n° {version} ({count}) en ligne de commande")
        db.session.commit()
        click.echo(f"{count} IBAN rechiffré(s) avec la clé n° {version}. Les autres clés peuvent être retirées "
                   "de BANK_DATA_KEYS.")

    @app.cli.command("run-tasks")
    def run_tasks():
        """Tâches périodiques (lancées par le service scheduler) : purges et envoi des emails en attente."""
        from .services.health import record_scheduler_run
        from .services.tasks import run

        click.echo(run())
        record_scheduler_run()

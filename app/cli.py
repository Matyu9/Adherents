import click

from .extensions import db
from .models import Account, Role
from .services import accounts, sessions, totp


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
        click.echo(f"Administrateur prêt : {account.full_name} <{email}>")

    @app.cli.command("run-tasks")
    def run_tasks():
        """Tâches périodiques (lancées par le service scheduler) : purges et envoi des emails en attente."""
        from .services.health import record_scheduler_run
        from .services.tasks import run

        click.echo(run())
        record_scheduler_run()

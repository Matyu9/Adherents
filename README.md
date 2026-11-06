# Adhérents

Plateforme de l'association pour les adhérents. Elle aura deux rôles :

- **servir d'interface entre le logiciel de gestion et les adhérents**, surtout au moment des inscriptions et réinscriptions ;
- **gérer les comptes et servir de fournisseur de connexion (OIDC)** pour toutes les applis de l'association, à commencer par l'Inventaire.

Elle ne gère pas les paiements.

## État du projet

**La phase 0, le socle, est terminée.** L'appli démarre et offre la connexion, Mon compte, les notifications, le journal, les rôles, les paramètres et la page d'état de l'installation.

**Il n'y a encore aucune fonction « métier »** : ni annuaire, ni inscriptions, ni serveur OIDC. Les comptes ne se créent pour l'instant qu'en ligne de commande (`flask create-admin`). La suite est décrite dans le [plan du projet](docs/plan.md).

## Démarrage rapide (développement)

Il faut Python 3.12 ou plus récent.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export FLASK_APP=wsgi.py FLASK_DEBUG=1
.venv/bin/flask db upgrade
.venv/bin/flask create-admin
.venv/bin/flask run --port 8001
```

L'appli répond sur <http://127.0.0.1:8001>. Pour lancer les tests : `.venv/bin/python -m pytest`.

Pour la mise en service avec Docker, voir [Installation et exploitation](docs/installation.md).

## Documentation

Tout est dans [`docs/`](docs/README.md) : plan du projet, installation, configuration, architecture et développement.

## Technique

Flask, SQLite, Jinja et HTMX, sans étape de build. Déploiement avec Docker Compose. Le socle est copié de l'Inventaire, pas partagé sous forme de paquet : les deux applis évoluent séparément.

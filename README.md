# Adhérents

Plateforme de l'association pour les adhérents. Elle a deux rôles :

- **servir d'interface entre le logiciel de gestion et les adhérents**, surtout au moment des inscriptions et réinscriptions, **puis le remplacer** : catalogue, cotisations et règlements ;
- **gérer les comptes et servir de fournisseur de connexion (OIDC)** pour toutes les applis de l'association, à commencer par l'Inventaire.

## État du projet

Déjà en place :

- **le socle** : connexion, Mon compte, notifications, journal, rôles, paramètres, état de l'installation ;
- **les comptes** : invitations, mot de passe oublié, groupes, sessions révocables, double authentification obligatoire pour qui administre ou lit des données personnelles ;
- **le serveur OIDC** : les applis de l'association s'y connectent (Authorization Code avec PKCE), et sont prévenues quand une session se ferme (Back-Channel Logout) ;
- **l'annuaire** : personnes, foyers, téléphones, saisons et adhésions, professeurs, fusion des doublons, IBAN chiffrés et mandats SEPA, rattachement des comptes à leur fiche ;
- **le catalogue** : disciplines, niveaux, sites et salles, cours de chaque saison, disciplines des professeurs, copie d'une saison sur la suivante ;
- **les inscriptions** : campagnes, dossier pré-rempli des familles, formulaire public des nouvelles familles, revue et validation par le secrétariat, report vers le logiciel de gestion, relances, « Mes élèves » pour les professeurs, conservation et anonymisation ;
- **l'espace adhérent** : chaque famille voit son foyer, ses cours, ses dossiers et les instruments prêtés (lus dans l'Inventaire) ;
- **les tarifs et cotisations** : grille d'éléments et de règles par saison, situation des foyers, cotisations proposées, corrigées puis validées, simulateur.

Pas encore : l'import depuis le logiciel de gestion, qui attend ses exports, puis les règlements et la bascule. La suite est décrite dans le [plan du projet](docs/plan.md).

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

Tout est dans [`docs/`](docs/README.md) : le [guide d'utilisation](docs/guide-utilisateur.md), le plan du projet, l'installation, la configuration, l'architecture et le développement.

## Technique

Flask, PostgreSQL, Jinja et HTMX, sans étape de build. Déploiement avec Docker Compose. Le socle est copié de l'Inventaire, pas partagé sous forme de paquet : les deux applis évoluent séparément.

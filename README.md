# Adhérents

[![CI](https://github.com/Matyu9/Adherents/actions/workflows/ci.yml/badge.svg)](https://github.com/Matyu9/Adherents/actions/workflows/ci.yml)

L'appli d'une association pour ses adhérents : annuaire, inscriptions aux cours, cotisations, et un compte unique pour toutes les applis de l'association.

Elle est née dans une association de musique, mais rien n'y est propre à la musique : les types de discipline, les disciplines, les niveaux, les cours et les tarifs sont ceux que l'association crée.

Elle a deux missions :

1. **Gérer les adhérents.** Annuaire, catalogue des cours, inscriptions, cotisations, puis règlements. Une association qui a déjà un logiciel de gestion peut commencer par s'en servir comme d'une interface avec les familles, puis le remplacer.
2. **Connecter les personnes.** Elle est le fournisseur d'identité (OpenID Connect) de l'association. Chaque appli s'y branche sans code spécifique, à commencer par l'Inventaire, qui suit le matériel prêté.

```
            Adhérents
  ┌──────────────────────────────┐
  │ Annuaire, foyers, dossiers   │
  │ Cours, cotisations, SEPA     │
  │ Comptes et groupes           │
  │ Serveur OIDC  ───────────────┼──▶ Inventaire
  └──────────────────────────────┘──▶ future appli
```

## Sommaire

- [Ce que fait l'appli](#ce-que-fait-lappli)
- [État du projet](#état-du-projet)
- [Démarrer en développement](#démarrer-en-développement)
- [Tests et qualité](#tests-et-qualité)
- [Mise en service](#mise-en-service)
- [Sécurité et données personnelles](#sécurité-et-données-personnelles)
- [Organisation du code](#organisation-du-code)
- [Documentation](#documentation)

## Ce que fait l'appli

### Pour les familles

- **Mon espace** : le foyer, les adhésions et les cours de chaque membre (avec l'heure de passage, la liste d'attente et les présences), les dossiers d'inscription, les consentements donnés et le matériel prêté (lu dans l'Inventaire).
- **Agenda** : une adresse à ajouter à Google Agenda, Apple ou Outlook, avec les séances des enfants à jour.
- **Inscription et réinscription** : un dossier pré-rempli depuis l'annuaire, à corriger et à envoyer. Les nouvelles familles passent par un formulaire public, avec vérification de leur adresse email.
- **Un seul compte** pour toutes les applis de l'association, avec invitation, mot de passe oublié et double authentification.

### Pour les professeurs

- **Mes élèves** : les élèves de ses cours et leurs responsables, et l'heure de passage dans un cours individuel.
- **Mes séances** : déplacer, annuler ou ajouter une séance, sans attendre le secrétariat ; faire l'appel ; ses séances dans son agenda.

### Pour le secrétariat

- **Annuaire** : une seule fiche par personne, les foyers et leurs responsables, les saisons et les adhésions, la fusion des doublons, l'historique de chaque fiche.
- **Catalogue** : disciplines et leurs types, niveaux, sites et salles, cours de chaque saison, copie d'une saison sur la suivante. Les chevauchements de salle ou de professeur sont signalés.
- **Gestion des cours** : élèves de chaque cours, inscriptions, transferts et abandons en cours de saison, liste d'attente, cours individuels, calendrier et séances, remplaçants, plannings, absences répétées, réservation des salles dans Google Workspace (facultatif).
- **Campagnes d'inscription** : revue de chaque dossier, champ par champ, puis validation ou renvoi à la famille ; relances hebdomadaires ; report des changements vers le logiciel de gestion.
- **Tarifs et cotisations** : une grille d'éléments et de règles par saison, la situation de chaque foyer, des cotisations proposées, corrigées avec un motif puis validées, et un simulateur.
- **Coordonnées bancaires** : IBAN chiffrés et suivi des mandats SEPA.

### Pour les administrateurs

- **Comptes, rôles et groupes** : des droits réglables par rôle, des groupes qui ouvrent les applis, des sessions révocables.
- **Applications connectées** : chaque appli déclarée reçoit l'identité et les groupes de la personne, et est prévenue quand une session se ferme.
- **Suivi** : journal des actions, notifications, état de l'installation (`/sante`), apparence aux couleurs de l'association.

Chaque écran est décrit dans le [guide d'utilisation](docs/guide-utilisateur.md).

## État du projet

Le projet est en développement : aucune installation ne sert encore de vrais adhérents.

### Ce qui est fait

| Partie | Contenu |
|---|---|
| Socle | Interface, droits par rôle, journal, notifications, emails, état de l'installation |
| Comptes | Invitations, mot de passe oublié, groupes, sessions révocables, double authentification |
| Serveur OIDC | Authorization Code avec PKCE, consentement, rotation des clés |
| Déconnexion | Les applis sont prévenues quand une session se ferme (Back-Channel Logout) |
| Branchement de l'Inventaire | Rattachement des comptes, parcours testé avec le vrai Inventaire |
| Annuaire | Personnes, foyers, saisons et adhésions, IBAN chiffrés et mandats SEPA |
| Catalogue | Types de discipline, disciplines, niveaux, lieux, cours de chaque saison |
| Campagnes d'inscription | Dossiers des familles, revue et validation, relances, conservation des données |
| Espace adhérent | Foyer, cours, dossiers, matériel prêté |
| Tarifs et cotisations | Grille par saison, règles, calcul proposé puis validé, simulateur |
| Gestion des cours | Effectifs, liste d'attente, cours individuels, séances, plannings, agendas, salles Google Workspace, présences |

### Ce qui reste

1. **Import depuis un logiciel de gestion** : personnes, foyers, adhésions et cours, avec essai à blanc et rapport de contrôle ; comptes créés déjà rattachés à leur fiche et invitation en masse.
2. **Règlements** : saisie manuelle par le secrétariat, prélèvements SEPA (échéancier, fichier pour la banque, rejets), paiement en ligne, soldes par foyer et relances des impayés.
3. **Bascule** : pour une association qui remplace son logiciel de gestion, un dernier import, la comparaison des soldes, puis l'appli devient la référence.

Les choix de conception, les pièges repérés et la liste à suivre avant une mise en production sont dans la [feuille de route](docs/plan.md). Les changements sont consignés dans le [journal des versions](CHANGELOG.md).

## Démarrer en développement

Il faut **Python 3.12 ou plus récent** et **PostgreSQL 16 ou plus récent** sur la machine. Docker n'est pas nécessaire.

```sh
createdb adherents && createdb adherents_test
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export FLASK_APP=wsgi.py FLASK_DEBUG=1
.venv/bin/flask db upgrade
.venv/bin/flask create-admin
.venv/bin/flask run --port 8001
```

L'appli répond sur <http://127.0.0.1:8001>. Aucun fichier `.env` n'est nécessaire en développement.

N'ajoutez pas `--host 0.0.0.0` : le mode debug permet d'exécuter du code à distance, et l'appli serait visible de tout le réseau.

La suite est dans [Développement](docs/developpement.md) : ajouter un écran, modifier le modèle de données, essayer l'appli avec l'Inventaire.

## Tests et qualité

```sh
.venv/bin/python -m pytest                        # tout, environ 6 minutes
.venv/bin/python -m pytest -m "not e2e" -n auto   # sans navigateur, en parallèle
.venv/bin/playwright install chromium             # une fois, pour les tests dans un navigateur
.venv/bin/ruff check . && .venv/bin/ruff format . # erreurs probables et mise en forme
.venv/bin/pre-commit install                      # une fois : Ruff passe avant chaque commit
```

Les tests tournent sur une vraie base PostgreSQL (`adherents_test`). Ils couvrent entre autres toutes les routes avec tous les rôles, les migrations sur une base remplie, et les parcours complets dans un vrai navigateur, avec la politique de sécurité du contenu active.

À chaque push sur `main` et à chaque pull request, l'intégration continue lance Ruff, les tests sous Python 3.12 et 3.14, les tests dans un navigateur et la recherche de failles connues dans les dépendances.

## Mise en service

L'appli se déploie avec Docker Compose, derrière un reverse proxy HTTPS :

```sh
cp .env.example .env    # puis renseigner SECRET_KEY, POSTGRES_PASSWORD, BASE_URL et BANK_DATA_KEYS
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
docker compose exec app flask create-admin
```

Trois services démarrent : la base PostgreSQL, l'appli (qui applique les migrations au démarrage) et le planificateur des tâches périodiques. L'appli n'écoute que sur `127.0.0.1:8001` : c'est au reverse proxy de l'exposer.

Deux points à ne pas manquer :

- **`BANK_DATA_KEYS`** chiffre les IBAN. Sauvegardez ces clés à part de la base : sans elles, les IBAN enregistrés sont perdus.
- **`OIDC_ISSUER`** est l'identité du serveur de connexion. Choisissez-la avant de brancher une appli : elle ne doit plus changer ensuite.

Le reste est dans [Installation et exploitation](docs/installation.md) (sauvegarde, mise à jour, accès de secours) et dans [Configuration](docs/configuration.md) (toutes les variables d'environnement).

## Sécurité et données personnelles

L'appli contient des données de mineurs et des coordonnées bancaires. Ses principaux garde-fous :

- **Double authentification obligatoire** pour qui administre ou lit des données personnelles.
- **IBAN chiffrés** (AES-GCM) avec une clé dédiée. Les lire en clair demande un droit à part et un code de double authentification récent, et chaque lecture est inscrite au journal.
- **Politique de sécurité du contenu stricte** : aucun script ni style en ligne, rien n'est chargé depuis un service externe.
- **Sessions révocables**, limitation des tentatives de connexion, déconnexion après inactivité.
- **Conservation limitée** : les anciens adhérents sont anonymisés et les anciens dossiers supprimés, après une durée fixée par l'association.

Aucune vraie donnée d'adhérent ne doit entrer dans le dépôt : `data/` et `.env` sont ignorés par Git.

Pour signaler une faille, en privé : voir [SECURITY.md](SECURITY.md).

## Organisation du code

Flask, PostgreSQL, Jinja et HTMX, sans étape de build : Pico et HTMX sont copiés dans le dépôt.

```
app/
├── blueprints/      Routes : valident les formulaires et la transaction
├── services/        Règles métier : ne font pas de commit
├── models.py        Modèle de données
├── permissions.py   Catalogue des droits
├── templates/       Jinja
└── static/          CSS, JavaScript, Pico et HTMX
migrations/          Migrations Alembic
tests/               Tests pytest, dont ceux dans un navigateur (Playwright)
docs/                Documentation
```

Le socle vient de l'Inventaire, copié et non partagé sous forme de paquet : les deux applis évoluent séparément. Les conventions, le modèle de données et le serveur OIDC sont décrits dans [Architecture](docs/architecture.md).

## Documentation

| Document | Pour qui | Contenu |
|---|---|---|
| [Guide d'utilisation](docs/guide-utilisateur.md) | Familles, professeurs, secrétariat, administrateurs | Chaque écran de l'appli |
| [Feuille de route](docs/plan.md) | Tout le monde | Objectifs, choix de conception, ce qui est fait et ce qui reste |
| [Installation et exploitation](docs/installation.md) | Personne qui héberge l'appli | Docker, reverse proxy, sauvegarde, mise à jour, secours |
| [Configuration](docs/configuration.md) | Personne qui héberge l'appli | Toutes les variables d'environnement |
| [Architecture](docs/architecture.md) | Développeurs | Organisation du code, données, droits, sécurité, serveur OIDC |
| [Développement](docs/developpement.md) | Développeurs | Environnement, tests, migrations, ajout d'un écran |
| [Annuaire](docs/annuaire.md), [catalogue](docs/catalogue.md), [inscriptions](docs/inscriptions.md), [tarifs](docs/tarifs.md) | Développeurs, direction | Le modèle et les règles de chaque domaine |

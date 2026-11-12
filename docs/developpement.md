# Développement

## Environnement

Il faut Python 3.12 ou plus récent, et **PostgreSQL** (16 ou plus récent) sur la machine. Docker n'est pas nécessaire.

```sh
brew install postgresql@18 && brew services start postgresql@18   # n'écoute que sur localhost
createdb adherents && createdb adherents_test
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/playwright install chromium   # pour les tests dans un navigateur
export FLASK_APP=wsgi.py FLASK_DEBUG=1
.venv/bin/flask db upgrade
.venv/bin/flask create-admin
.venv/bin/flask run --port 8001
```

- **Fichier `.env` :** inutile en développement. Si vous en créez un à partir de `.env.example`, retirez la ligne `DATA_DIR=/data`.
- **Réseau :** `flask run` écoute sur `127.0.0.1`. N'ajoutez pas `--host 0.0.0.0` : le mode debug permet d'exécuter du code à distance, et l'appli serait visible de tout le réseau.
- **Port :** l'appli utilise le port 8001, pour tourner à côté de l'Inventaire sur le port 8000. Les cookies ont des noms propres pour la même raison.

## Tests

```sh
.venv/bin/python -m pytest                 # tout
.venv/bin/python -m pytest -m "not e2e"    # sans navigateur, plus rapide
.venv/bin/python -m pytest -m "not e2e" -n auto             # en parallèle, un processus par cœur
.venv/bin/python -m pytest -m "not e2e" -n auto --cov=app   # avec la couverture du code
HYPOTHESIS_PROFILE=deep .venv/bin/python -m pytest tests/test_properties.py   # 1000 cas par propriété
```

Écrivez autant de tests que possible à chaque phase : droits par rôle sur chaque route, règles métier, cas limites, validation des formulaires.

| Fichier | Ce qu'il garantit |
|---|---|
| `test_sweep.py` | **Toutes les routes × tous les rôles**, et tous les liens de toutes les pages : aucun lien mort, aucune erreur, aucune donnée non échappée, rien que la CSP bloquerait |
| `test_migrations.py` | Migrations sur une base remplie, retour en arrière, et modèles identiques aux migrations |
| `test_e2e.py` | Vrai navigateur, CSP et CSRF actifs : aucune violation, confirmations, envoi automatique, thèmes, menu mobile, pas de défilement horizontal |
| `test_security.py` | En-têtes, CSP, CSRF, refus d'un secret connu, proxy, feuilles de style |
| `test_auth.py`, `test_throttle.py` | Connexion, sessions, mot de passe, limitation des tentatives |
| `test_totp.py` | Double authentification : connexion, rejeu, codes de secours, obligation par rôle, accès de secours |
| `test_links.py` | Liens à usage unique, mot de passe oublié, changement d'adresse, `email_verified` |
| `test_accounts_admin.py`, `test_groups.py` | Gestion des comptes et des groupes, et qui peut gérer qui |
| `test_oidc.py`, `test_oidc_e2e.py` | Serveur OIDC : le parcours complet, puis tout ce qui doit être refusé ; dans un navigateur, avec une appli cliente Authlib |
| `test_logout.py`, `test_logout_e2e.py` | Déconnexion : chaque façon de fermer une session prévient les bonnes applis, file d'envoi, `end_session_endpoint` |
| `test_inventaire_e2e.py` | Le vrai Inventaire, lancé dans son propre processus : rattachement, connexion, déconnexions. Ignoré si `INVENTAIRE_DIR` (par défaut `../Inventaire`) n'a pas de `.venv` |
| `test_properties.py` | Propriétés sur des données générées, avec Hypothesis |
| Les autres | Un fichier par domaine : rôles, compte, journal, paramètres, emails, santé, erreurs, rapports CSP, CLI |

Dans les tests comme en production, un administrateur a la double authentification : `make_account` la configure et `login` saisit le code. Elle n'a pas de réglage pour la désactiver.

**Base de test :** PostgreSQL, base `adherents_test` (ou `TEST_DATABASE_URL`). Le schéma est recréé au début de chaque session, puis les tables sont vidées avant et après chaque test. Les serveurs des tests de bout en bout, les migrations et les redémarrages de l'appli ont chacun une base jetable (`fresh_database()`). Deux lancements de `pytest` ne doivent donc pas tourner en même temps sur la même base. Avec `-n auto`, chaque processus crée et garde sa propre base (`adherents_test_gw0`, `adherents_test_gw1`…). Gardez les tests dans un navigateur en série : sous la charge d'un lancement en parallèle, leurs attentes peuvent expirer.

Le client de test (`conftest.py`) oublie l'utilisateur en cache et annule toute écriture non validée après chaque requête. Sans ça, une session révoquée resterait connectée dans les tests et un `commit` oublié passerait inaperçu.

## Qualité du code

```sh
.venv/bin/ruff check .        # erreurs probables, imports, tournures à simplifier
.venv/bin/ruff format .       # mise en forme, lignes de 120 caractères
.venv/bin/pre-commit install  # une fois : Ruff passe ensuite avant chaque commit
.venv/bin/pip install pip-audit && .venv/bin/pip-audit -r requirements.txt   # failles connues des dépendances
```

Les règles sont dans `pyproject.toml`. Le formatage d'origine de tout le code est un commit à part, listé dans `.git-blame-ignore-revs` : `git config blame.ignoreRevsFile .git-blame-ignore-revs` le fait ignorer par `git blame`.

**Intégration continue** (`.github/workflows/ci.yml`), à chaque push sur `main` et à chaque pull request : Ruff, les tests sans navigateur sous Python 3.12 (celui de l'image Docker) et 3.14, les tests dans un navigateur, et `pip-audit`. Dependabot propose les mises à jour chaque semaine. Un second workflow (`codeql.yml`) cherche les failles dans le code Python. Les actions sont épinglées par empreinte de commit, avec la version en commentaire : Dependabot met les deux à jour.

## Essayer avec l'Inventaire en local

Les deux applis tournent côte à côte, sur `127.0.0.1` uniquement. Utilisez des dossiers de données à part, pour ne toucher ni aux bases habituelles ni au `.env` de l'Inventaire (`FLASK_SKIP_DOTENV=1`).

1. **Adhérents**, sur le port 8001 :

   ```sh
   export FLASK_APP=wsgi.py FLASK_SKIP_DOTENV=1 DATA_DIR=/tmp/essai/adherents SECRET_KEY=une-cle-locale-assez-longue
   export BASE_URL=http://127.0.0.1:8001 OIDC_ISSUER=http://127.0.0.1:8001
   .venv/bin/flask db upgrade && .venv/bin/flask create-admin
   .venv/bin/flask run --port 8001
   ```

2. Dans **Applications → Nouvelle application**, déclarez l'Inventaire :
   - adresse de retour : `http://127.0.0.1:8000/connexion/oidc/retour` ;
   - adresse après déconnexion : `http://127.0.0.1:8000/connexion` ;
   - adresse des avis de déconnexion : `http://127.0.0.1:8000/connexion/oidc/deconnexion` ;
   - un groupe autorisé, dont vos comptes d'essai font partie.

   Notez l'identifiant et le secret.

3. **L'Inventaire**, sur le port 8000, depuis son dossier :

   ```sh
   export FLASK_APP='app:create_app()' FLASK_SKIP_DOTENV=1 DATA_DIR=/tmp/essai/inventaire SECRET_KEY=une-autre-cle-locale
   export BASE_URL=http://127.0.0.1:8000 OIDC_PROVIDER_NAME=Adhérents OIDC_SCOPES='openid email profile groups'
   export OIDC_DISCOVERY_URL=http://127.0.0.1:8001/.well-known/openid-configuration
   export OIDC_CLIENT_ID=<identifiant> OIDC_CLIENT_SECRET=<secret> OIDC_LINK_BY_EMAIL=0
   .venv/bin/flask db upgrade
   .venv/bin/flask run --port 8000
   ```

Ouvrez les deux applis par `127.0.0.1`, et non `localhost` : c'est l'adresse déclarée. Les cookies des deux applis ne se mélangent pas, car Adhérents a ses propres noms de cookies. Pour le rattachement des comptes et les rôles par groupes, suivez `docs/oidc-adherents.md` dans l'Inventaire.

## Ajouter un écran

1. Écrivez la route dans `app/blueprints/`, et la logique dans `app/services/`, sans `commit`.
2. Protégez-la avec `@require("droit")` ou `@login_required`.
3. Si le droit est nouveau, ajoutez-le à `PERMISSION_GROUPS` et à `DEFAULT_PERMISSIONS` si besoin.
4. **Déclarez la route dans `ACCESS` (`tests/test_sweep.py`).** Sinon les tests échouent : c'est voulu, pour que chaque écran ait un accès décidé explicitement.
5. Pas d'attribut `onclick` ni `style` dans les templates : utilisez une classe CSS et un attribut `data-*` géré dans `app.js`.
6. Tracez les actions importantes avec `activity.log()`.

## Modifier le modèle de données

```sh
.venv/bin/flask db migrate -m "description"
# relisez le fichier généré dans migrations/versions/
.venv/bin/flask db upgrade
.venv/bin/python -m pytest tests/test_migrations.py
```

- Un modèle modifié sans migration fait échouer `test_models_match_migrations`.
- Les tests de migration tournent chacun dans une base PostgreSQL jetable, créée puis supprimée.
- Gardez `CHECK` pour les vrais invariants ; un statut amené à évoluer se valide plutôt dans le code.
- Ajoutez les contraintes et cascades des nouvelles tables à `test_migrations.py`.

## Données réelles

Aucune vraie donnée d'adhérent ne doit entrer dans le dépôt, et surtout pas les exports du logiciel de gestion, qui contiennent des données de mineurs. `data/` et `.env` sont ignorés par Git.

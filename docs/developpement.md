# Développement

## Environnement

Il faut Python 3.12 ou plus récent. Docker n'est pas nécessaire.

```sh
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
| `test_properties.py` | Propriétés sur des données générées, avec Hypothesis |
| Les autres | Un fichier par domaine : rôles, compte, journal, paramètres, emails, santé, erreurs, rapports CSP, CLI |

Dans les tests comme en production, un administrateur a la double authentification : `make_account` la configure et `login` saisit le code. Elle n'a pas de réglage pour la désactiver.

Le client de test (`conftest.py`) oublie l'utilisateur en cache et annule toute écriture non validée après chaque requête. Sans ça, une session révoquée resterait connectée dans les tests et un `commit` oublié passerait inaperçu.

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
- Sous SQLite, modifier une table la recrée. Le test sur base remplie vérifie que les données et les clés étrangères survivent.
- Pour un statut amené à évoluer, validez dans le code plutôt qu'avec un `CHECK`, difficile à modifier sous SQLite. Gardez `CHECK` pour les vrais invariants.
- Ajoutez les nouvelles tables aux données de départ (`SEED`) de `test_migrations.py`.

## Données réelles

Aucune vraie donnée d'adhérent ne doit entrer dans le dépôt, et surtout pas les exports du logiciel de gestion, qui contiennent des données de mineurs. `data/` et `.env` sont ignorés par Git.

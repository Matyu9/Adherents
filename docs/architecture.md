# Architecture

## Organisation du code

```
app/
├── __init__.py      Fabrique de l'appli, en-têtes de sécurité, CSP
├── config.py        Variables d'environnement
├── extensions.py    SQLAlchemy, CSRF, Login ; fold() et réglages SQLite
├── models.py        Modèle de données
├── permissions.py   Catalogue des droits, can(), @require
├── errors.py        Pages d'erreur
├── filters.py       Dates en français
├── cli.py           flask create-admin, flask run-tasks
├── blueprints/      Routes : valident les formulaires et la transaction
├── services/        Règles métier : ne font pas de commit
├── templates/       Jinja
└── static/          css/app.css, css/dark.css, js/app.js, vendor/ (Pico, HTMX)
```

Il n'y a aucune étape de build. Pico et HTMX sont copiés dans `static/vendor/` : rien n'est chargé depuis un service externe.

## Conventions

- **Les routes valident la transaction.** Une fonction de service ne fait jamais de `commit` : c'est ce qui permet de regrouper plusieurs opérations et le journal dans une même transaction. Les tests annulent toute écriture non validée après chaque requête, donc un `commit` oublié se voit tout de suite.
- **Tous les droits passent par `can(user, action)`**, utilisée à la fois par `@require` sur les routes et par les templates pour masquer les boutons. Une action inconnue lève une erreur : une faute de frappe ne peut pas ouvrir un accès en silence.
- **Pas de JavaScript ni de style en ligne.** Les comportements se déclarent par attributs et sont gérés dans `app.js` : `data-confirm`, `data-autosubmit`, `data-open-nav`, `data-close-nav`.
- **Toute action qui compte passe par `activity.log()`**, dans la même transaction.
- **Recherche sans accents :** `fold_contains()` trouve « Lefèvre » en tapant « lefevre ».

## Données

| Table | Rôle |
|---|---|
| `account` | Compte de connexion : email, mot de passe haché, rôle, `active`, préférences, et `sub` (UUID opaque, futur identifiant OIDC) |
| `login_session` | Une connexion ouverte sur un appareil. La supprimer invalide ses cookies, même copiés ailleurs. |
| `role` | Droits d'un rôle. Les rôles de base n'y figurent qu'une fois modifiés. |
| `activity` | Journal : auteur, compte concerné, action, message |
| `notification` | Notifications ; `dedup_key` rend les relances idempotentes |
| `app_setting` | Réglages modifiables dans l'appli (nom, logo, couleur) |
| `throttle_event` | Tentatives comptées pour la limitation de débit |

`Person` et `Household` arrivent en phase 1. `Account` ne doit pas devenir la fiche d'une personne : voir le [plan](plan.md).

## Rôles et droits

| Rôle de base | Droits par défaut |
|---|---|
| Administrateur | Tous, sans qu'on puisse les lui retirer, plus la gestion des rôles |
| Gestionnaire | Voir le journal |
| Adhérent | Aucun droit d'administration |

Les rôles se modifient et se créent dans **Administration → Rôles**. Ajouter un droit se fait dans `PERMISSION_GROUPS` (`permissions.py`).

## Sécurité

| Protection | Fonctionnement |
|---|---|
| **CSP bloquante** | `default-src 'self'` sans `unsafe-inline` ni `unsafe-eval`. Les violations remontent sur `/csp-report`, limité en débit et en taille, et sont écrites dans les journaux sans rien stocker en base. |
| **HTMX** | Configuré avec `allowEval: false` et `includeIndicatorStyles: false` pour respecter la CSP. |
| **Sessions révocables** | Le cookie porte un jeton vérifié en base à chaque requête : déconnexion, changement de mot de passe ou désactivation du compte agissent immédiatement. |
| **Limitation des tentatives** | 8 échecs en 15 minutes par email ou par adresse IP. Le compte est en base, donc commun à tous les workers. Seule une empreinte HMAC de la clé est stockée. |
| **Comptes inconnus** | Même message et même calcul de hachage qu'un mauvais mot de passe. Le texte saisi n'est conservé au journal que s'il correspond à un compte. |
| **CSRF** | Sur tous les formulaires et requêtes HTMX. Le jeton vit aussi longtemps que la session, pour qu'un long formulaire ouvert sur un téléphone n'échoue pas à l'envoi. |
| **Journal** | Les champs de `SENSITIVE_FIELDS` (`password_hash`, `iban`, `bic`) sont masqués. Les connexions sont effacées au bout d'un an, car elles contiennent des adresses IP. |
| **Logo** | Réencodé en PNG, ce qui retire les métadonnées, et servi sous un nom aléatoire. |
| **Cache** | `no-store` sur les pages de connexion, de mot de passe et de compte. |

## Tâches périodiques

`flask run-tasks`, lancée par le service `scheduler`, efface les connexions trop anciennes et les tentatives périmées, puis envoie les emails en attente. Elle écrit aussi `data/scheduler.last`, que `/sante` utilise pour vérifier que le planificateur tourne.

## Différences avec l'Inventaire

Le socle vient de l'Inventaire, mais les deux applis évoluent séparément. Les corrections apportées sont listées dans le [journal des versions](../CHANGELOG.md). N'ont pas été repris, car sans objet ou prévus plus tard : le client OIDC, l'API, les jetons personnels, les avatars, les textes d'emails modifiables, le tableau de bord à la carte et la corbeille.

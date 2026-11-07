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
| `account` | Compte de connexion : email, mot de passe haché, rôle, `active`, préférences, et `sub` (UUID opaque, identifiant OIDC) |
| `oidc_client` | Appli connectée : identifiant, empreinte du secret, adresses de retour, groupes autorisés et transmis |
| `oidc_code`, `oidc_token` | Codes d'autorisation et jetons d'accès, en empreinte |
| `oidc_consent` | Ce qu'une personne a accepté de transmettre à une appli |
| `oidc_key` | Clés de signature, privée chiffrée |
| `account_token` | Lien à usage unique (invitation, mot de passe oublié, confirmation d'adresse). Seule son empreinte est stockée. |
| `recovery_code` | Code de secours de la double authentification. Seule son empreinte est stockée. |
| `access_group`, `account_access_group` | Groupes et leurs membres. `key` est la valeur transmise aux applis : elle ne change jamais. |
| `login_session` | Une connexion ouverte sur un appareil. La supprimer invalide ses cookies, même copiés ailleurs. |
| `role` | Droits d'un rôle. Les rôles de base n'y figurent qu'une fois modifiés. |
| `activity` | Journal : auteur, compte concerné, action, message |
| `notification` | Notifications ; `dedup_key` rend les relances idempotentes |
| `app_setting` | Réglages modifiables dans l'appli (nom, logo, couleur) |
| `throttle_event` | Tentatives comptées pour la limitation de débit |

`Person` et `Household` arrivent avec l'annuaire (phase 1). `Account` ne doit pas devenir la fiche d'une personne : voir le [plan](plan.md).

## Rôles et droits

| Rôle de base | Droits par défaut |
|---|---|
| Administrateur | Tous, sans qu'on puisse les lui retirer, plus la gestion des rôles |
| Gestionnaire | Voir le journal |
| Adhérent | Aucun droit d'administration |

Les rôles se modifient et se créent dans **Administration → Rôles**. Ajouter un droit se fait dans `PERMISSION_GROUPS` (`permissions.py`).

Gérer un compte permet d'en prendre le contrôle, car un lien d'invitation s'affiche quand l'email ne part pas. `accounts.can_manage()` limite donc un non-administrateur aux comptes qui n'ont ni plus de droits ni d'autres groupes que lui, et il ne peut attribuer aucun rôle plus riche que le sien. Personne n'agit sur son propre rôle, son propre accès ou sa propre double authentification depuis cet écran : il reste donc toujours au moins un administrateur actif.

## Sécurité

| Protection | Fonctionnement |
|---|---|
| **CSP bloquante** | `default-src 'self'` sans `unsafe-inline` ni `unsafe-eval`. Les violations remontent sur `/csp-report`, limité en débit et en taille, et sont écrites dans les journaux sans rien stocker en base. |
| **HTMX** | Configuré avec `allowEval: false` et `includeIndicatorStyles: false` pour respecter la CSP. |
| **Sessions révocables** | Le cookie porte un jeton vérifié en base à chaque requête : déconnexion, changement de mot de passe ou désactivation du compte agissent immédiatement. |
| **Limitation des tentatives** | 8 échecs en 15 minutes par email ou par adresse IP. Le compte est en base, donc commun à tous les workers. Seule une empreinte HMAC de la clé est stockée. |
| **Comptes inconnus** | Même message et même calcul de hachage qu'un mauvais mot de passe. Le texte saisi n'est conservé au journal que s'il correspond à un compte. |
| **Liens par email** | Usage unique, 7 jours pour une invitation, 24 heures pour une adresse, 1 heure pour un mot de passe. Ouvrir le lien ne consomme rien, car les antivirus de messagerie les suivent : il faut envoyer le formulaire. Limite connue : le lien figure dans les journaux d'accès de gunicorn et du proxy tant qu'il n'a pas servi. |
| **`email_verified`** | Vrai seulement après un clic sur un lien parti par email. Un lien transmis à la main ne prouve rien, et un changement d'adresse par un responsable remet la valeur à faux. Les applis clientes s'y fieront. |
| **Mot de passe oublié** | Même réponse et même durée que le compte existe ou non : l'envoi se fait hors de la requête. Avec la double authentification, le lien change le mot de passe sans ouvrir de session : une boîte mail piratée ne suffit pas. |
| **Double authentification** | Secret chiffré avec une clé dérivée de `SECRET_KEY`, tolérance de ±30 secondes, un code ne sert qu'une fois, 5 essais en 15 minutes. Obligatoire pour les administrateurs et tout rôle qui gère les comptes ou les groupes. L'activer, la désactiver ou changer d'adresse demande à nouveau le mot de passe. |
| **CSRF** | Sur tous les formulaires et requêtes HTMX. Le jeton vit aussi longtemps que la session, pour qu'un long formulaire ouvert sur un téléphone n'échoue pas à l'envoi. |
| **Journal** | Les champs de `SENSITIVE_FIELDS` (`password_hash`, `iban`, `bic`) sont masqués. Les connexions sont effacées au bout d'un an, car elles contiennent des adresses IP. |
| **Logo** | Réencodé en PNG, ce qui retire les métadonnées, et servi sous un nom aléatoire. |
| **Cache** | `no-store` sur les pages de connexion, de mot de passe et de compte. |

## Tâches périodiques

`flask run-tasks`, lancée par le service `scheduler`, efface les connexions trop anciennes, les tentatives périmées et les liens expirés, puis envoie les emails en attente. Elle écrit aussi `data/scheduler.last`, que `/sante` utilise pour vérifier que le planificateur tourne.

## Serveur OIDC

| Fichier | Rôle |
| --- | --- |
| `app/blueprints/oidc.py` | Points d'accès : découverte, `authorize`, `token`, `userinfo`, `jwks`, consentement |
| `app/services/oidc_server.py` | Branchement d'Authlib, et ce qui y est resserré |
| `app/services/oidc.py` | Clés, applis, consentements, jetons, informations transmises |
| `app/blueprints/applications.py` | Écran « Applications » |

**Ce qui est accepté :** Authorization Code avec PKCE S256 uniquement, pour des clients qui ont un secret (`client_secret_basic` ou `client_secret_post`), avec le scope `openid`. Pas de refresh token, pas de `request`, pas de `response_mode` autre que `query`.

**Ce qui est stocké :** les codes, les jetons d'accès et les secrets clients en empreinte SHA-256. Les clés privées sont chiffrées avec une clé dérivée de `SECRET_KEY`.

**Jetons :**

- le jeton d'identité est signé en RS256, et sa clé est désignée par `kid` ;
- le jeton d'accès est opaque et ne sert qu'à `/oidc/userinfo`, qui revérifie le compte, l'appli et les groupes à chaque appel ;
- un code rejoué est refusé et annule les jetons qu'il a donnés.

**Reconnexion demandée par une appli (`prompt=login`, `max_age`) :** la session en cours ne change qu'après la nouvelle saisie, pour qu'un simple lien ne déconnecte personne.

**Heures :** les durées des codes et des jetons sont en UTC, à cause du changement d'heure. `auth_time` vient de `LoginSession.created_at`, en heure locale : il peut se tromper d'une heure pour une session ouverte pendant l'heure répétée d'octobre.

**Qui entre :** seuls les membres d'un groupe autorisé de l'appli, administrateurs compris. Sans groupe autorisé, personne n'entre. L'appli ne reçoit que les groupes cochés « transmis », et seulement avec le scope `groups`.

**Clés :** renouvelées tous les 90 jours par `flask run-tasks`, ou à la main. Une clé retirée perd sa partie privée et reste publiée 30 jours.

**Retour vers l'appli :** après un formulaire, il passe par `/oidc/reprendre`, une page à rafraîchissement automatique. Chrome applique `form-action 'self'` à toutes les redirections qui suivent un formulaire : cette nouvelle navigation évite d'assouplir la CSP.

## Différences avec l'Inventaire

Le socle vient de l'Inventaire, mais les deux applis évoluent séparément. Les corrections apportées sont listées dans le [journal des versions](../CHANGELOG.md). N'ont pas été repris, car sans objet ou prévus plus tard : le client OIDC (Adhérents est le serveur), l'API, les jetons personnels, les avatars, les mots de passe provisoires, la fusion et l'archivage de fiches, les textes d'emails modifiables, le tableau de bord à la carte et la corbeille.

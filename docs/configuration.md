# Configuration

L'appli se règle par des variables d'environnement, lues dans le fichier `.env` (modèle : [`.env.example`](../.env.example)). Il faut redémarrer après un changement.

Le nom, le sous-titre, la couleur et le logo se règlent directement dans **Paramètres → Apparence**.

## Général

| Variable | Défaut | Rôle |
|---|---|---|
| `SECRET_KEY` | aucun | Signe les cookies et les empreintes de la limitation des tentatives, et chiffre les secrets de double authentification. **Obligatoire en production** : l'appli refuse de démarrer sans elle, ou avec la valeur d'exemple. **La changer rend inutilisables toutes les doubles authentifications et tous les codes de secours** : chaque personne devra la reconfigurer après une réinitialisation par un responsable, et le premier administrateur passera par `flask create-admin`. Sauvegardez-la à part de la base. |
| `BASE_URL` | `http://localhost:8001` | Adresse publique, utilisée dans les emails. En `https://`, elle active aussi les cookies `Secure`. |
| `ASSO_NAME` | `Mon association` | Nom par défaut, tant qu'aucun n'est défini dans Paramètres → Apparence. |
| `DATA_DIR` | `./data` (`/data` dans Docker) | Dossier des fichiers envoyés (logo) et du marqueur du planificateur. La base est dans PostgreSQL. |
| `OIDC_ISSUER` | `BASE_URL` | Identité du serveur OIDC, inscrite dans les jetons. **À choisir avant de brancher une appli :** la changer oblige à reconfigurer toutes les applis, qui ne reconnaîtraient plus leurs comptes. Un avertissement apparaît au démarrage si elle n'est pas en `https://`, sauf sur `localhost`. |
| `DATABASE_URL` | `postgresql+psycopg:///adherents` | Adresse de la base PostgreSQL (base `adherents` de la machine, par défaut). Avec docker-compose, elle est construite à partir de `POSTGRES_PASSWORD`. |
| `POSTGRES_PASSWORD` | aucun | Docker uniquement : mot de passe de la base, **obligatoire**. |
| `TZ` | `Europe/Paris` dans Docker | Fuseau horaire des dates. |
| `BANK_DATA_KEYS` | aucune | Clés de chiffrement des IBAN, voir [plus bas](#coordonnées-bancaires). Sans elle, l'annuaire marche mais aucun IBAN ne peut être enregistré. |
| `INVENTAIRE_API_URL` | vide | API de l'Inventaire (`https://inventaire.<domaine>/api/v1`), pour montrer aux familles, dans leur espace adhérent, le matériel prêté. En https, sauf sur `localhost`. |
| `INVENTAIRE_API_TOKEN` | vide | Jeton du compte de service de l'Inventaire, créé par `flask jeton-espace-adherent` dans l'Inventaire. Il ne permet que de lire le matériel en garde des personnes de l'annuaire. |

## Réseau et sécurité

| Variable | Défaut | Rôle |
|---|---|---|
| `TRUST_PROXY` | `0` | `1` derrière un reverse proxy : l'appli croit alors les en-têtes `X-Forwarded-*`. **Sans proxy, laissez `0`** : un visiteur pourrait sinon falsifier son adresse et contourner la limitation des tentatives. Un avertissement apparaît au démarrage si `BASE_URL` est en `https://` et que `TRUST_PROXY` vaut `0`. |
| `BIND_ADDR` | `127.0.0.1` | Adresse d'écoute du port 8001 dans Docker. Jamais `0.0.0.0` sur une adresse publique. |
| `CSP_REPORT_ONLY` | `0` | `0` : la Content-Security-Policy bloque. `1` : elle signale sans bloquer, seulement pour un diagnostic temporaire. |

## Emails

Tous facultatifs. L'envoi est actif dès que `SMTP_HOST` et une adresse d'expédition (`SMTP_FROM` ou `SMTP_USER`) sont renseignés.

| Variable | Défaut | Rôle |
|---|---|---|
| `SMTP_HOST` | vide | Serveur SMTP. |
| `SMTP_PORT` | `587` | Port. |
| `SMTP_USER` | vide | Identifiant. |
| `SMTP_PASSWORD` | vide | Mot de passe. |
| `SMTP_FROM` | vide | Adresse d'expédition, sinon `SMTP_USER`. |
| `SMTP_TLS` | `true` | Utilise STARTTLS. |

Sans SMTP, le mot de passe oublié et le changement d'adresse par soi-même sont indisponibles, et une invitation affiche un lien à transmettre à la main. Une adresse confirmée de cette façon reste « non vérifiée ».

Le bouton **Paramètres → M'envoyer un email de test** vérifie les réglages. Une notification de plus de 2 jours n'est plus envoyée, ce qui évite un envoi massif quand le SMTP est configuré après coup.

## Variable propre au développement

| Variable | Rôle |
|---|---|
| `FLASK_DEBUG` | `1` : mode de développement, qui autorise une clé secrète par défaut. **Jamais en production.** |

## Coordonnées bancaires

Les IBAN sont chiffrés avec `BANK_DATA_KEYS`, une clé **distincte de `SECRET_KEY`** : changer l'une ne fait pas perdre l'autre, et une fuite de l'une ne donne pas l'autre.

```sh
flask bank-generate-key          # affiche « 1:… » : à copier dans .env
BANK_DATA_KEYS=1:xxxxxxxx…
```

- **Sauvegarde :** la base et `BANK_DATA_KEYS` se sauvegardent **séparément**. Une copie de la base seule ne livre aucun IBAN ; perdre la clé les rend tous illisibles.
- **Démarrage :** l'appli refuse de démarrer s'il existe en base des IBAN chiffrés avec une clé absente de `BANK_DATA_KEYS`, ou si une clé est mal écrite.
- **Changer de clé :** `flask bank-generate-key` donne une clé numérotée à la suite. Mettez-la en tête (`BANK_DATA_KEYS=2:nouvelle,1:ancienne`), redémarrez, lancez `flask bank-rotate-key`, puis retirez l'ancienne et redémarrez. La clé au numéro le plus haut chiffre ; les autres ne servent qu'à relire.
- **Mode sécurisé :** toute opération bancaire demande un code de double authentification récent. `ELEVATION_MINUTES` (10 par défaut) règle la durée de la fenêtre.
- **Limite :** la clé est sur le même serveur que la base. Ce chiffrement protège d'une fuite de la base, d'une sauvegarde ou d'un disque, pas d'une prise de contrôle du serveur.

## Garde-fous des données personnelles

| Variable | Défaut | Rôle |
|---|---|---|
| `ELEVATION_MINUTES` | `10` | Durée du mode sécurisé ouvert par un code TOTP, pour les opérations bancaires. |
| `DIRECTORY_READ_LIMIT` | `300` | Pages de l'annuaire et des professeurs qu'un compte peut afficher en 15 minutes. Au-delà, il est arrêté et les administrateurs sont prévenus. |
| `IDLE_TIMEOUT_HOURS` | `2` | Inactivité après laquelle se ferme la session d'un compte qui lit des données personnelles. `0` désactive. |

Gardez la base et le dossier des données réservés au compte qui fait tourner l'appli (`chmod 700` sur le dossier, `600` sur la base) : la page de santé le vérifie.

## Serveur OIDC

La sauvegarde doit couvrir la base **et** `SECRET_KEY` : les clés de signature y sont chiffrées avec une clé qui en dérive.

Si `SECRET_KEY` change, la clé de signature devient illisible. Une nouvelle clé la remplace automatiquement et les applis la découvrent seules, donc les connexions continuent. Les secrets TOTP et les codes de secours, eux, restent perdus (voir plus haut).

`flask oidc-rotate-key` met une nouvelle clé en service, comme le bouton de l'écran « Applications ».

Pour brancher une appli : créer ses groupes, la déclarer dans « Applications », puis copier dans sa configuration l'identifiant, le secret (affiché une seule fois) et l'adresse de découverte. Elle doit envoyer PKCE avec S256, et demander le scope `groups` si elle lit les groupes.

### Donner un rôle dans une appli

Adhérents ne décide que de qui entre. C'est l'appli qui donne les droits, d'après les groupes qu'elle reçoit :

1. Créez le groupe dans **Groupes**. Sa **clé** (par exemple `inventaire-admins`) est la valeur transmise : elle ne change jamais, même si le groupe est renommé.
2. Ajoutez-y les personnes, depuis leur fiche dans **Comptes**. Ou bien, dans **Rôles**, cochez ce groupe sur un rôle : chaque compte de ce rôle en fait alors partie, sans rien cocher sur sa fiche. Sur la fiche, ces groupes apparaissent « par le rôle », et ne se retirent qu'en changeant le rôle ou ses groupes.
3. Dans **Applications**, cochez ce groupe parmi les **groupes transmis**. Il doit aussi être autorisé, sauf si ces personnes sont déjà dans un autre groupe autorisé.
4. Dans l'appli, associez la clé au rôle voulu. Pour l'Inventaire, c'est le champ « Groupes OIDC » de l'écran Rôles, ou `OIDC_ADMIN_GROUPS`.

Quand les groupes transmis d'une personne changent, l'appli reçoit un avis de déconnexion. C'est aussi le cas quand on change son rôle, ou les groupes de son rôle. La personne se reconnecte sans rien ressaisir et reçoit son nouveau rôle.

Seul un administrateur modifie les groupes d'un rôle. Un gestionnaire ne peut attribuer qu'un rôle dont il a lui-même tous les groupes : sinon, il ferait de quelqu'un un administrateur de l'Inventaire sans l'être.

### Rattacher les comptes d'une appli existante

Avant la bascule, l'appli rattache chaque compte à sa fiche (pour l'Inventaire : Personnes → Rattacher les comptes OIDC). Elle a besoin de la liste des comptes qui y ont accès : `sub`, email, adresse vérifiée ou non, prénom, nom et groupes transmis. Trois façons de la lui donner :

| Façon | Comment |
|---|---|
| L'appli la récupère elle-même | Cochez « Peut récupérer la liste des comptes » sur l'appli. Elle la lit sur `<OIDC_ISSUER>/oidc/comptes`, avec son identifiant et son secret en HTTP Basic. Pour l'Inventaire, renseignez `OIDC_ACCOUNTS_URL`. Fermé par défaut, et limité contre les essais de secret. |
| Bouton | **Applications → l'appli → Exporter les comptes (JSON)**, puis envoyer le fichier dans l'appli. |
| Ligne de commande | `flask oidc-export-accounts --application "Inventaire" > comptes.json` |

Une appli de l'association voit tous ses comptes. Une appli tierce ne voit que les personnes qui lui ont donné leur accord, et seulement ce qu'elles ont accepté de transmettre : la liste n'en dit pas plus que la connexion. Chaque lecture et chaque export sont notés au journal. Un fichier exporté contient des données personnelles : à supprimer une fois le rattachement fait.

### Espace adhérent : le matériel prêté

Pour que les familles voient dans **Mon espace** le matériel prêté à leurs enfants :

1. Sur l'application de l'Inventaire, cochez « Peut récupérer la liste des comptes ». Une appli de l'association peut alors lire aussi `<OIDC_ISSUER>/oidc/personnes` : identifiant public, nom, année de naissance de chaque personne de l'annuaire, et son `sub` si elle a un compte qui entre dans l'appli. Ni email, ni adresse, ni date de naissance complète. Une appli tierce y est refusée.
2. Dans l'Inventaire, **Personnes → Rattacher à l'annuaire** relie chaque fiche à sa personne, après vérification.
3. Dans l'Inventaire, `flask jeton-espace-adherent` crée le compte de service et affiche son jeton.
4. Ici, renseignez `INVENTAIRE_API_URL` et `INVENTAIRE_API_TOKEN`.

Adhérents vérifie lui-même que la personne connectée est responsable de celles qu'il demande à l'Inventaire. L'Inventaire, lui, ne reçoit que leurs identifiants publics.

### Déconnexion

Deux adresses facultatives, à déclarer pour chaque appli dans « Applications » :

| Adresse | Rôle |
|---|---|
| Adresses après déconnexion | Où l'appli peut renvoyer la personne après l'avoir déconnectée (`post_logout_redirect_uri`), comparées à l'identique. |
| Adresse des avis de déconnexion | Point d'accès Back-Channel Logout de l'appli. Ce serveur l'appelle quand une session se ferme, quand un compte est coupé, quand la personne perd l'accès ou quand les groupes transmis changent. |

Une appli sans adresse d'avis n'est pas prévenue : elle garde ses sessions jusqu'à leur fin normale.

Un avis non reçu est renvoyé par `flask run-tasks` pendant 3 jours, puis abandonné et noté au journal. L'écran de l'appli affiche les avis en attente. Le serveur doit pouvoir joindre ces adresses : depuis Docker, `localhost` désigne le conteneur, pas la machine.

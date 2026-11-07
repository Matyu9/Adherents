# Configuration

L'appli se règle par des variables d'environnement, lues dans le fichier `.env` (modèle : [`.env.example`](../.env.example)). Il faut redémarrer après un changement.

Le nom, le sous-titre, la couleur et le logo se règlent directement dans **Paramètres → Apparence**.

## Général

| Variable | Défaut | Rôle |
|---|---|---|
| `SECRET_KEY` | aucun | Signe les cookies et les empreintes de la limitation des tentatives, et chiffre les secrets de double authentification. **Obligatoire en production** : l'appli refuse de démarrer sans elle, ou avec la valeur d'exemple. **La changer rend inutilisables toutes les doubles authentifications et tous les codes de secours** : chaque personne devra la reconfigurer après une réinitialisation par un responsable, et le premier administrateur passera par `flask create-admin`. Sauvegardez-la à part de la base. |
| `BASE_URL` | `http://localhost:8001` | Adresse publique, utilisée dans les emails. En `https://`, elle active aussi les cookies `Secure`. |
| `ASSO_NAME` | `Mon association` | Nom par défaut, tant qu'aucun n'est défini dans Paramètres → Apparence. |
| `DATA_DIR` | `./data` (`/data` dans Docker) | Dossier de la base et des fichiers envoyés. |
| `OIDC_ISSUER` | `BASE_URL` | Identité du serveur OIDC, inscrite dans les jetons. **À choisir avant de brancher une appli :** la changer oblige à reconfigurer toutes les applis, qui ne reconnaîtraient plus leurs comptes. Un avertissement apparaît au démarrage si elle n'est pas en `https://`, sauf sur `localhost`. |
| `DATABASE_URL` | `sqlite:///<DATA_DIR>/adherents.db` | Adresse de la base. L'appli est conçue pour SQLite. |
| `TZ` | `Europe/Paris` dans Docker | Fuseau horaire des dates. |

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

## Serveur OIDC

La sauvegarde doit couvrir la base **et** `SECRET_KEY` : les clés de signature y sont chiffrées avec une clé qui en dérive.

Si `SECRET_KEY` change, la clé de signature devient illisible. Une nouvelle clé la remplace automatiquement et les applis la découvrent seules, donc les connexions continuent. Les secrets TOTP et les codes de secours, eux, restent perdus (voir plus haut).

`flask oidc-rotate-key` met une nouvelle clé en service, comme le bouton de l'écran « Applications ».

Pour brancher une appli : créer ses groupes, la déclarer dans « Applications », puis copier dans sa configuration l'identifiant, le secret (affiché une seule fois) et l'adresse de découverte. Elle doit envoyer PKCE avec S256, et demander le scope `groups` si elle lit les groupes.

# Configuration

L'appli se règle par des variables d'environnement, lues dans le fichier `.env` (modèle : [`.env.example`](../.env.example)). Il faut redémarrer après un changement.

Le nom, le sous-titre, la couleur et le logo se règlent directement dans **Paramètres → Apparence**.

## Général

| Variable | Défaut | Rôle |
|---|---|---|
| `SECRET_KEY` | aucun | Signe les cookies et les empreintes de la limitation des tentatives. **Obligatoire en production** : l'appli refuse de démarrer sans elle, ou avec la valeur d'exemple. |
| `BASE_URL` | `http://localhost:8001` | Adresse publique, utilisée dans les emails. En `https://`, elle active aussi les cookies `Secure`. |
| `ASSO_NAME` | `Mon association` | Nom par défaut, tant qu'aucun n'est défini dans Paramètres → Apparence. |
| `DATA_DIR` | `./data` (`/data` dans Docker) | Dossier de la base et des fichiers envoyés. |
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

Le bouton **Paramètres → M'envoyer un email de test** vérifie les réglages. Une notification de plus de 2 jours n'est plus envoyée, ce qui évite un envoi massif quand le SMTP est configuré après coup.

## Variable propre au développement

| Variable | Rôle |
|---|---|
| `FLASK_DEBUG` | `1` : mode de développement, qui autorise une clé secrète par défaut. **Jamais en production.** |

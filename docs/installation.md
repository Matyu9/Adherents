# Installation et exploitation

Ce guide s'adresse à la personne qui héberge l'appli.

> **Avant une mise en ligne :** lisez la liste [Avant une mise en production](plan.md#avant-une-mise-en-production) de la feuille de route.

## Ce qu'il faut

- Un serveur Linux avec **Docker** et **Docker Compose**.
- Un **reverse proxy HTTPS** (Caddy, Traefik, nginx…) et un nom de domaine.
- Facultatif : un compte **SMTP**. Sans lui, les notifications restent dans l'appli.

Toutes les données tiennent dans le dossier `data/` : la base SQLite et le logo.

## Installation avec Docker

```sh
git clone <url-du-depot> adherents
cd adherents
cp .env.example .env
```

Générez une clé secrète et collez-la dans `SECRET_KEY` :

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Renseignez aussi `BASE_URL`, et mettez `TRUST_PROXY=1` derrière un reverse proxy. Le détail de chaque variable est dans [Configuration](configuration.md).

L'appli refuse de démarrer si `SECRET_KEY` est vide ou vaut encore la valeur d'exemple.

Le conteneur tourne avec l'utilisateur 1000 : le dossier `data/` doit lui appartenir.

```sh
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
docker compose exec app flask create-admin
```

Docker lance deux services :

- **`app`** : le site, qui applique les migrations au démarrage ;
- **`scheduler`** : les tâches périodiques, toutes les 15 minutes.

Le port publié est `127.0.0.1:8001`, jamais une adresse publique par défaut.

## Mise en ligne

Faites pointer le reverse proxy vers `127.0.0.1:8001`, par exemple avec Caddy :

```
adherents.example.org {
    reverse_proxy 127.0.0.1:8001
}
```

Si le proxy est sur une autre machine, mettez dans `BIND_ADDR` l'adresse privée que voit le proxy. N'utilisez jamais `0.0.0.0` sur une adresse publique.

Vérifiez ensuite l'installation dans **Paramètres → État de l'installation**, ou sur `/sante` : base, migrations, dossier de données, planificateur et emails.

## Sauvegarde et restauration

Il faut sauvegarder tout le dossier `data/`. Pour une copie cohérente de la base pendant que l'appli tourne :

```sh
sqlite3 data/adherents.db ".backup 'sauvegarde.db'"
```

Copiez aussi `data/uploads/` et votre fichier `.env`, qui contient `SECRET_KEY`.

Pour restaurer :

```sh
docker compose down
cp sauvegarde.db data/adherents.db
rm -f data/adherents.db-wal data/adherents.db-shm
docker compose up -d
```

**Une sauvegarde contient des données personnelles.** Stockez-la chiffrée et hors du serveur. Elle contient aussi les clés de signature OIDC.

## Mise à jour

Sauvegardez d'abord, puis :

```sh
git pull
docker compose up -d --build
```

Les migrations s'appliquent au démarrage du service `app`.

## Accès de secours

Si plus aucun administrateur ne peut se connecter :

```sh
docker compose exec app flask create-admin
```

Avec un email déjà connu, la commande remet ce compte administrateur et actif, remplace son mot de passe et ferme ses sessions.

## Incident de sécurité

- **Appareil perdu ou doute sur un compte :** **Paramètres → Sécurité → Déconnecter tout le monde** ferme toutes les sessions sauf la vôtre, y compris celles ouvertes avec « Rester connecté ».
- **Clé secrète divulguée :** changez `SECRET_KEY` et redémarrez. Les cookies existants deviennent invalides.
- **Fuite de données personnelles :** l'association doit la notifier à la CNIL.

## Journaux et diagnostic

```sh
docker compose logs -f app
docker compose logs -f scheduler
```

- **Erreur 500 :** la page affiche une référence à 8 caractères, à rechercher dans les journaux.
- **Quelque chose ne s'affiche pas ou ne réagit plus :** cherchez `Violation CSP` dans les journaux. Pour diagnostiquer sans rien bloquer, mettez temporairement `CSP_REPORT_ONLY=1`.
- **Le planificateur semble arrêté :** `/sante` le signale après une heure sans passage. Vérifiez le service `scheduler`.

## Liste de contrôle

- [ ] `SECRET_KEY` est générée et propre à cette installation.
- [ ] `BASE_URL` commence par `https://` et `TRUST_PROXY=1`.
- [ ] Le port n'est pas accessible directement depuis Internet.
- [ ] `CSP_REPORT_ONLY=0`.
- [ ] Les sauvegardes sont en place, chiffrées, et une restauration a été essayée.
- [ ] `/sante` n'affiche aucun problème.

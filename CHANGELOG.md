# Journal des versions

Les changements notables du projet sont consignés ici. Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## 0.2.0 - 2026-11-06 : phase 2, les comptes

### Ajouté

- **Invitation par lien** : un compte créé n'a pas de mot de passe, la personne le choisit. Sans SMTP ou si l'envoi échoue, le lien s'affiche pour être transmis à la main.
- **Mot de passe oublié**, avec la même réponse et la même durée que le compte existe ou non.
- **Changement d'adresse confirmé par lien**, avec le mot de passe actuel et un avis à l'ancienne adresse.
- **`email_verified`** : vrai seulement après un clic sur un lien reçu par email.
- **Double authentification (TOTP)** avec 10 codes de secours, obligatoire pour les administrateurs et les rôles qui gèrent les comptes ou les groupes.
- **Écran Comptes** : recherche, filtres, invitation, coupure immédiate, sessions, réinitialisation de la double authentification, journal.
- **Écran Groupes**, dont la clé reste fixe pour les futures applis clientes.
- **Droits `account.manage` et `group.manage`** : on ne gère que des comptes qui n'ont ni plus de droits ni d'autres groupes que soi.

### Modifié

- **`flask create-admin`** réinitialise aussi la double authentification, pour rester un vrai accès de secours après la perte d'un téléphone.
- **`flask run-tasks`** efface aussi les liens expirés.
- **Ordre des phases** : les comptes et l'OIDC passent avant l'annuaire, en attendant les exports du logiciel de gestion.

## 0.1.0 - 2026-11-05 : phase 0, le socle

### Ajouté

- **Socle repris de l'Inventaire** : interface (barre latérale, onglets sur mobile, thèmes, 7 palettes, taille du texte), connexion, sessions révocables, Mon compte, notifications, emails, journal, rôles et droits, personnalisation, page d'état de l'installation, Docker.
- **Modèle de compte séparé de la personne** : `Account` ne décrit que la connexion et porte un `sub` opaque, prêt pour le serveur OIDC.
- **Commande `flask create-admin`**, qui sert aussi d'accès de secours.
- **Commande `flask run-tasks`** pour les tâches périodiques : purges et envoi des emails.
- **Suite de tests** : balayage de toutes les routes par rôle, migrations sur une base remplie, et parcours dans un vrai navigateur.
- **Documentation** et plan du projet.

### Corrigé par rapport à l'Inventaire

- **Aucun JavaScript en ligne** : les 43 gestionnaires `onclick`, `onsubmit` et `onchange` sont remplacés par `data-confirm`, `data-autosubmit` et `data-open-nav`, gérés dans `app.js`.
- **CSP bloquante par défaut**, et non plus en simple observation.
- **Thème sombre écrit une seule fois**, dans `dark.css`, au lieu de deux blocs à garder identiques.
- **Limitation des tentatives en base**, commune à tous les workers. Elle survit à un redémarrage et ne conserve qu'une empreinte de l'email ou de l'adresse IP.
- **Cookies à noms propres** (`adherents_session`, `adherents_remember`), pour ne pas entrer en conflit avec l'Inventaire sur `localhost`.
- **Champs sensibles masqués au journal** : `activity.diff()` note qu'un IBAN ou un mot de passe a changé, jamais sa valeur.

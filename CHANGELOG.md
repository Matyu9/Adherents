# Journal des versions

Les changements notables du projet sont consignés ici. Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## Non publié

### Ajouté

- **Annuaire** (phase 1) : personnes, foyers avec leurs responsables 1 et 2 et leurs personnes à charge, téléphones, numéros du logiciel de gestion, saisons et adhésions, profils de professeur. Recherche sans accents, filtres (élèves de la saison, anciens adhérents, professeurs, fiches archivées), fusion de deux fiches ou de deux foyers en double, archivage. Chaque fiche a son historique.
- **Saison en cours** choisie dans Annuaire → Saisons, sans la déduire de la date.
- **Coordonnées bancaires** : IBAN chiffrés (AES-GCM) avec une clé dédiée, `BANK_DATA_KEYS`, et mandats SEPA. Lecture en clair avec le droit `bank.view` et un nouveau code de double authentification, inscrite au journal. `flask bank-generate-key` et `flask bank-rotate-key`. Les mandats sont supprimés 13 mois après leur dernier prélèvement.
- **Rattachement des comptes** à leur fiche, avec des propositions (même email, même nom) qu'une personne valide une à une ; création d'une fiche depuis un compte, et d'un compte avec invitation depuis la fiche d'un adulte.
- Nouveaux droits `directory.view`, `directory.edit`, `directory.merge` et `bank.view`. Le gestionnaire reçoit les trois premiers ; `bank.view` rend la double authentification obligatoire.

- **Groupes d'un rôle** (écran Rôles) : chaque compte du rôle fait partie de ces groupes sans qu'on les coche sur sa fiche. Ils ouvrent les applis et y sont transmis comme les autres. Changer le rôle d'un compte, ou les groupes d'un rôle, prévient les applis concernées.
- **Bouton « Exporter les comptes (JSON) »** sur la fiche de chaque appli : plus besoin de la ligne de commande.
- **`/oidc/comptes`** : une appli autorisée (case « Peut récupérer la liste des comptes », fermée par défaut) lit elle-même la liste de ses comptes, avec son identifiant et son secret. Une appli tierce n'y voit que les personnes qui lui ont donné leur accord, et seulement ce qu'elles ont accepté.
- Les groupes transmis figurent dans la liste des comptes.

### Sécurité

- Un gestionnaire ne peut attribuer qu'un rôle dont il a tous les groupes, et ne gère que des comptes dont il a tous les groupes, rôle compris.

## 0.5.0 - non publiée : phase 5, le branchement de l'Inventaire

### Ajouté

- **`flask oidc-export-accounts --application <nom>`** : exporte en JSON les comptes qui ont accès à une appli, pour qu'elle les rattache à ses fiches avant la bascule. Noté au journal.
- **Parcours de bout en bout avec le vrai Inventaire** (`tests/test_inventaire_e2e.py`) : export, rattachement, connexion, adresse partagée refusée, et les trois façons d'être déconnecté.

### Modifié

- `flask create-admin` envoie tout de suite les avis de déconnexion des sessions qu'il ferme.

## 0.4.0 - non publiée : phase 4, la déconnexion

### Ajouté

- **Back-Channel Logout** : chaque appli où une session a servi reçoit un avis signé quand cette session se ferme (déconnexion, appareil retiré, mot de passe changé, compte coupé, « déconnecter tout le monde », session trop ancienne).
- **Avis aussi quand l'accès change** : groupe autorisé retiré, appli fermée, groupes transmis modifiés, autorisation retirée depuis Mon compte.
- **File d'envoi** : un avis non reçu est réessayé par `flask run-tasks` pendant 3 jours, puis abandonné et noté au journal. L'écran de l'appli affiche les avis en attente.
- **`end_session_endpoint`** (`/oidc/deconnexion`) : une appli peut demander la déconnexion et y renvoyer la personne, sur une adresse déclarée. Sans `id_token_hint` qui désigne cette session, la personne confirme.
- **`sid`** dans le jeton d'identité, et deux nouveaux champs dans « Applications » : adresses après déconnexion, adresse des avis.

### Modifié

- Les formulaires « fermer cette session » désignent la session par son `sid` public, et non plus par son jeton secret.
- Une appli ne peut être supprimée qu'une fois fermée, pour que chacun en soit d'abord déconnecté.
- Un code d'autorisation est refusé si sa session a été fermée avant son échange.

## 0.3.0 - non publiée : phase 3, le serveur OIDC

### Ajouté

- **Serveur OIDC avec Authlib** : découverte, `authorize`, `token`, `userinfo`, `jwks`. Uniquement Authorization Code avec PKCE S256.
- **Écran Applications** : déclarer une appli, choisir les groupes autorisés et ceux qui lui sont transmis, secret affiché une seule fois, fermeture, suppression.
- **Consentement** pour les applis tierces, à retirer depuis Mon compte. Les applis de l'association en sont dispensées.
- **Clés de signature RS256** chiffrées en base, renouvelées tous les 90 jours ou à la demande (`flask oidc-rotate-key`).
- **Droit `client.manage`**, qui impose la double authentification.
- **`OIDC_ISSUER`** : l'identité du serveur, à choisir avant de brancher une appli.

### Sécurité

- Codes, jetons d'accès et secrets stockés en empreinte. Un code vaut 60 secondes, ne sert qu'une fois, et son rejeu annule ses jetons.
- `/oidc/userinfo` revérifie à chaque appel le compte, l'appli et les groupes : couper un compte ou le retirer d'un groupe prend effet aussitôt.
- Un groupe utilisé par une appli ne peut plus être supprimé.

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

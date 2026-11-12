# Journal des versions

Les changements notables du projet sont consignés ici. Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## Non publié

### Ajouté : gestion des cours, effectifs et inscriptions en cours de saison

- **Page d'un cours** (Catalogue → un cours) : élèves inscrits avec leurs responsables et leur date d'arrivée, effectif face à la capacité, élèves partis en cours de saison, liste en CSV, lien pour écrire aux familles en copie cachée.
- **Inscrire, changer de cours, noter l'abandon d'un cours**, depuis la page du cours ou la fiche de l'élève. Nouveau droit `enrollment.edit`, donné au gestionnaire.
- **Cotisation « à revoir »** : un changement d'inscription marque la cotisation déjà calculée de chaque foyer de l'élève, avec un motif, sans la recalculer. Filtre « À revoir » dans Tarifs → Cotisations, et bouton « rien à changer ».
- **Migration `a0000007effectifs`** : date d'entrée de chaque inscription (reprise de sa date d'enregistrement), lien de transfert, motifs « à revoir ». Voir [docs/cours.md](docs/cours.md).
- **Feuille de route** : la gestion des cours, en six étapes (effectifs, cours individuels, liste d'attente, séances et agendas, agendas des salles Google Workspace, présences).

### Changé : les types de discipline se composent

- **Types de discipline** : la liste fixe (instrument, formation musicale, pratique collective, éveil, autre) laisse place à une liste que chaque association compose, dans **Catalogue → Disciplines, niveaux et lieux**. Un type a un nom et un ordre ; un type qui a servi s'archive au lieu de se supprimer. Rien n'est plus propre à la musique.
- **Migration `a0000006types`** : les types déjà saisis deviennent des lignes de la nouvelle liste, et chaque discipline garde le sien.
- Dans Mon espace et la documentation, « instruments prêtés » devient « matériel prêté ».

### Outillage

- **Ruff** vérifie et met en forme tout le code (`pyproject.toml`, qui remplace aussi `pytest.ini`), avec `pre-commit` pour le lancer avant chaque commit.
- **Intégration continue** sur GitHub Actions : Ruff, tests sous Python 3.12 et 3.14 avec PostgreSQL 18, tests dans un navigateur, failles connues des dépendances (`pip-audit`). Dependabot pour les mises à jour.
- **Sécurité du dépôt** : `SECURITY.md` (signalement privé d'une faille), analyse CodeQL du code Python à chaque push et chaque lundi, actions GitHub épinglées par empreinte de commit.
- **Tests en parallèle** avec `pytest -m "not e2e" -n auto` (une base par processus) et couverture avec `--cov=app`.
- **Dépendances** : Flask 3.1.3, Pillow 13.0.0 et python-dotenv 1.2.2, qui corrigent des failles connues signalées par `pip-audit`.
- **Guide d'utilisation** ([docs/guide-utilisateur.md](docs/guide-utilisateur.md)) pour les familles, les professeurs, le secrétariat et les administrateurs.

### Changé : PostgreSQL remplace SQLite

- **Base PostgreSQL** (16 ou plus récent, 18 conseillé) : `DATABASE_URL`, et un service `db` dans docker-compose (mot de passe dans `POSTGRES_PASSWORD`), jamais exposé hors du réseau Docker.
- **Migrations remises à zéro** : une migration initiale (`a0000001initial`) remplace celles écrites pour SQLite. Aucune base de production n'existait ; une base SQLite de développement ne se convertit pas, il faut repartir d'une base vide.
- Recherche sans accents par l'extension `unaccent` ; écritures sérialisées par un verrou consultatif, comme le faisait `BEGIN IMMEDIATE`.
- Tests sur PostgreSQL (`adherents_test`), avec une base jetable pour les serveurs de bout en bout et les migrations.

### Ajouté

- **Tarifs** (phase 9) : moteur de calcul des cotisations à partir d'une grille construite depuis zéro. Éléments à prix de base, règles appliquées dans l'ordre choisi (conditions reliées par ET et OU, pourcentage, montant, prix fixe, offert, remplacement, ajout, remise sur le total, groupes exclusifs), classement des élèves et arrondi réglables par saison. Situation des foyers par saison (tranche, quotient, résidence, étiquettes), cotisations proposées, corrigées avec un motif puis validées, effet d'une règle en brouillon sur toute la saison, copie des tarifs d'une saison à l'autre. Nouveaux droits `fee.view`, `fee.edit` et `fee.assess` (donnés au gestionnaire) et `fee.situation`. Écrans dans **Enseignement → Tarifs** : éléments, règles (formulaire de conditions ET / OU sans JavaScript, ordre, brouillon et effet sur la saison), élément facturé par chaque cours, simulateur, réglages et régies, cotisations des foyers (calcul, corrections, validation, éléments pris à part) et situation des foyers. Voir [docs/tarifs.md](docs/tarifs.md).
- **Mon espace** (phase 7) : chaque responsable voit son foyer (adresse, téléphones, IBAN masqués), les adhésions et les cours de chaque membre, ses dossiers d'inscription et les consentements donnés ; un élève de 16 ans ou plus avec son compte voit ses cours. Le **matériel prêté** vient de l'Inventaire, par un compte de service (`INVENTAIRE_API_URL`, `INVENTAIRE_API_TOKEN`).
- **`/oidc/personnes`** : les personnes de l'annuaire (identifiant public, nom, année de naissance, `sub` si elles ont un compte qui entre dans l'appli), pour une appli de l'association qui relie ses fiches. Même accès que `/oidc/comptes`, refusé aux applis tierces.
- **Campagnes d'inscription** (phase 6) : le secrétariat ouvre une campagne par saison, avec ses dates et ses consentements. Chaque responsable retrouve le dossier de son foyer pré-rempli depuis l'annuaire, avec les cours qui prolongent ceux de l'an dernier ; il corrige, choisit les cours (un cours complet est grisé), répond aux consentements, indique un IBAN s'il le souhaite, puis envoie. Voir [docs/inscriptions.md](docs/inscriptions.md).
- **Revue des dossiers** : ce que la validation changera dans l'annuaire, champ par champ ; renvoi à la famille avec un message, ou validation, qui crée les adhésions, les inscriptions aux cours, les consentements datés (avec leur texte) et le compte bancaire. Nouveaux droits `registration.view` et `registration.manage`, donnés au gestionnaire.
- **Nouvelles familles** : formulaire public, si la campagne l'autorise, avec un lien d'accès envoyé par email.
- **Report vers le logiciel de gestion** : fichier des changements, sans IBAN ou, en mode sécurisé, avec ; dossiers notés comme reportés.
- **Relances** hebdomadaires des familles qui n'ont pas envoyé leur dossier.
- **Mes élèves** : un professeur voit les élèves de ses cours et leurs responsables.
- **Conservation des données** : durée en saisons, à fixer ; anonymisation des anciens adhérents décidée à l'écran (droit `directory.anonymize`) et suppression des anciens dossiers.
- Le catalogue affiche le nombre d'inscrits de chaque cours ; un cours qui a des élèves ne se supprime plus.
- **Catalogue** (phase 8) : disciplines, niveaux, sites et salles communs à toutes les saisons ; cours de chaque saison avec leur professeur, leur créneau, leur salle et leur effectif maximum ; disciplines enseignées par chaque professeur, par saison. Les chevauchements de salle ou de professeur sont signalés, un cours peut être fermé aux inscriptions, et un professeur parti n'est plus proposé. Nouveaux droits `catalog.view` et `catalog.edit`, donnés au gestionnaire. Voir [docs/catalogue.md](docs/catalogue.md).
- **Copie d'une saison** sur une saison vide, avec un aperçu de ce qui sera repris, laissé de côté ou à compléter.
- La fiche d'un professeur montre ses cours et ses disciplines de la saison en cours.
- **Annuaire** (phase 1) : personnes, foyers avec leurs responsables 1 et 2 et leurs personnes à charge, téléphones, numéros du logiciel de gestion, saisons et adhésions, profils de professeur. Recherche sans accents, filtres (élèves de la saison, anciens adhérents, professeurs, fiches archivées), fusion de deux fiches ou de deux foyers en double, archivage. Chaque fiche a son historique.
- **Saison en cours** choisie dans Annuaire → Saisons, sans la déduire de la date.
- **Coordonnées bancaires** : IBAN chiffrés (AES-GCM) avec une clé dédiée, `BANK_DATA_KEYS`, et mandats SEPA. Lecture en clair avec le droit `bank.view` et un nouveau code de double authentification, inscrite au journal. `flask bank-generate-key` et `flask bank-rotate-key`. Les mandats sont supprimés 13 mois après leur dernier prélèvement.
- **Rattachement des comptes** à leur fiche, avec des propositions (même email, même nom) qu'une personne valide une à une ; création d'une fiche depuis un compte, et d'un compte avec invitation depuis la fiche d'une personne de 16 ans ou plus (accord d'un responsable légal avant 18 ans).
- **Fusion champ par champ** : ce qui n'est rempli que d'un côté est repris, et l'écran demande quoi garder quand les deux fiches diffèrent.
- Un IBAN présent sur plusieurs foyers est signalé en permanence sur chacun d'eux.
- **Écran Professeurs**, avec son droit `teacher.manage` : fiche et contrat d'un nouveau professeur en une fois, ou contrat ajouté à un adulte déjà dans l'annuaire. La fiche d'un adhérent ne propose plus de contrat.

### Sécurité

- **Mode sécurisé** pour toutes les opérations bancaires : un code de double authentification récent (10 minutes), lié à la session.
- **Double authentification obligatoire** pour tout rôle qui lit des données personnelles (annuaire, IBAN, contrats) : le gestionnaire est concerné.
- **Limite de lecture** de l'annuaire par compte, avec alerte des administrateurs.
- **Fermeture des sessions inactives** (2 h) pour les comptes qui lisent des données personnelles.
- Fusionner une fiche qui a un compte demande aussi le droit de gérer les comptes.
- Le journal ne garde plus la valeur du lieu de naissance, des observations, du contact d'urgence ni des contrats.
- En-têtes `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, `X-Robots-Tag`, et HSTS en HTTPS.
- La page de santé signale une base lisible par les autres comptes du serveur.

### Modifié

- Les messages après une action restent visibles sous la barre du haut, même quand la page s'ouvre plus bas, et se referment d'un clic.
- La date d'abandon d'une adhésion n'apparaît que si le statut est « Abandon ».
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

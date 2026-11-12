# Feuille de route

Ce document décrit ce que l'appli vise, les choix de conception qui la structurent, ce qui est fait et ce qui reste. Le détail des changements est dans le [journal des versions](../CHANGELOG.md).

## Objectif

Adhérents a deux missions :

1. **Gérer les adhérents d'une association** : annuaire, catalogue des cours, inscriptions, cotisations, puis règlements. Une association qui a déjà un logiciel de gestion peut commencer par s'en servir comme d'une interface avec les familles, puis le remplacer.
2. **Être le fournisseur d'identité de l'association.** Toute appli standard doit pouvoir s'y brancher en OpenID Connect, sans code spécifique. L'Inventaire, qui suit le matériel prêté, en est le premier client.

```
            Adhérents
  ┌──────────────────────────────┐
  │ Annuaire, foyers, dossiers   │
  │ Cours, cotisations, SEPA     │
  │ Comptes et groupes           │
  │ Serveur OIDC  ───────────────┼──▶ Inventaire
  └──────────────────────────────┘──▶ future appli
```

## Choix de conception

| Sujet | Choix |
|---|---|
| Comptes | Adhérents décide qui peut se connecter. Chaque appli garde ses droits détaillés. |
| Connexion | Adhérents est lui-même le serveur OIDC, sans service d'identité à part. Les comptes sont dans la base de l'appli, pas dans un annuaire LDAP. |
| Comptes, personnes et foyers | Trois notions séparées. Un parent a une seule adresse email et plusieurs enfants ; un enfant sans email n'a pas de compte, son responsable légal gère sa fiche depuis le sien. |
| Adhésion et accès | Indépendants : une saison terminée ne coupe pas un compte. Un parent doit encore voir le matériel que son enfant a en garde. |
| Comptes des mineurs | Dès 16 ans, avec l'accord d'un responsable légal, confirmé à la création et noté au journal. |
| Accès des professeurs | Un professeur ne voit que ses élèves et leurs responsables, à partir des inscriptions. |
| Inscriptions | La famille saisit dans un dossier ; l'annuaire ne change qu'à la validation du dossier par le secrétariat. Voir [inscriptions.md](inscriptions.md). |
| Rien de propre à une activité | Types de discipline, disciplines, niveaux, tarifs et régies sont ceux que l'association crée. Aucune valeur n'est fournie d'office. |
| Tarifs | Une grille par saison, construite depuis zéro : des éléments à prix de base et des règles ordonnées. Le calcul est proposé, puis validé par une personne. Voir [tarifs.md](tarifs.md). |
| IBAN | Conservés dans l'appli, chiffrés avec une clé dédiée, et lus en clair seulement avec un droit à part et un code de double authentification récent. Voir [annuaire.md](annuaire.md#iban-et-mandats-sepa). |
| Mandat SEPA | Suivi dans l'appli : référence, date de signature, premier et dernier prélèvement. Le document signé n'y est pas déposé. |
| Conservation des données | La durée n'a pas de valeur par défaut : rien n'est anonymisé tant que l'association ne l'a pas fixée. |
| Base de données | PostgreSQL : migrations fiables avant les fonctions financières, et sauvegardes continues possibles. |
| Socle technique | Copié de l'Inventaire, pas partagé sous forme de paquet : les deux applis évoluent séparément. |
| Logiciel de gestion existant | S'il y en a un, il reste la référence jusqu'à la bascule, sauf pendant qu'un dossier d'inscription est ouvert. |

## Où en est le projet

| Partie | État | Détail |
|---|---|---|
| Socle | Fait | Interface, droits par rôle, journal, notifications, emails, état de l'installation |
| Comptes | Fait | Invitations, mot de passe oublié, groupes, sessions révocables, double authentification |
| Serveur OIDC | Fait | Authorization Code avec PKCE, consentement, rotation des clés |
| Déconnexion | Fait | `end_session_endpoint` et Back-Channel Logout, avec une file d'envoi réessayée |
| Branchement de l'Inventaire | Fait | Rattachement des comptes, parcours testé avec le vrai Inventaire |
| Annuaire | Fait, sauf l'import | [annuaire.md](annuaire.md) |
| Catalogue | Fait | [catalogue.md](catalogue.md) |
| Campagnes d'inscription | Fait | [inscriptions.md](inscriptions.md) |
| Espace adhérent | Fait | Foyer, cours, dossiers, matériel prêté |
| Tarifs et cotisations | Fait | [tarifs.md](tarifs.md) |
| Gestion des cours | En cours : effectifs, inscriptions en cours de saison, cours individuels et liste d'attente faits | [cours.md](cours.md), et ci-dessous |
| Import depuis un logiciel de gestion | À faire | Voir ci-dessous |
| Règlements | À faire | Voir ci-dessous |
| Bascule | À faire | Voir ci-dessous |

## Ce qui reste

### Gestion des cours

La vie d'un cours pendant la saison, une fois le catalogue construit. Chaque étape se livre et se teste seule.

1. **Effectifs et inscriptions en cours de saison** (fait, voir [cours.md](cours.md)).
   - Une page par cours : élèves, effectif face à la capacité, export, email aux responsables.
   - Le secrétariat inscrit un élève qui a une adhésion de la saison, le transfère vers un autre cours ou note l'abandon d'un seul cours. L'inscription garde sa date d'entrée et, après un transfert, le cours d'origine.
   - Un cours complet n'est plus proposé dans les dossiers.
   - Tout changement d'inscription marque la cotisation du foyer « à revoir », sans la recalculer seul. Une cotisation validée garde son statut et porte le signal.
   - Droit `enrollment.edit`.
2. **Cours individuels** (fait). Un cours est individuel par un choix explicite, pas parce qu'il n'a pas de capacité. Chaque élève a son heure de passage et sa durée. Deux passages qui se chevauchent, ou un passage hors du créneau, sont signalés sans être refusés.
3. **Liste d'attente** (fait).
   - La famille s'y inscrit depuis son dossier quand un cours est complet ; le secrétariat peut aussi y ajouter un élève.
   - L'ordre est une position explicite, que le secrétariat peut changer.
   - Quand une place se libère, le secrétariat est prévenu et inscrit le premier de la liste. Rien n'est automatique.
4. **Calendrier et séances.**
   - Pour chaque saison : dates de reprise et de fin des cours, et périodes sans cours saisies par l'association. Aucune liste de vacances n'est fournie d'office.
   - Les séances de chaque cours sont générées à la demande. On peut relancer la génération : elle ne touche ni aux séances passées ni à celles modifiées à la main.
   - Une séance peut avoir un remplaçant, qui voit les élèves de cette séance et fait l'appel.
   - **Le professeur déplace, annule ou ajoute ses séances lui-même.** Le changement s'applique tout de suite : la salle est réservée, les familles sont prévenues par email, le secrétariat est notifié et peut revenir en arrière.
   - Le délai jusqu'auquel le professeur peut modifier une séance est un réglage de l'association, sans valeur par défaut : tant qu'il n'est pas fixé, il n'y a pas de limite.
   - Le créneau habituel d'un cours reste au secrétariat.
   - **Plannings** par salle, par professeur et par site : la semaine type, ou une semaine réelle avec ses annulations.
   - **Agendas à s'abonner (`.ics`)** pour chaque compte, par une adresse secrète que le titulaire peut régénérer :
     - un professeur y trouve ses séances et ses remplacements ;
     - une famille, les séances de ses enfants et leurs heures de passage.
     - Aucun nom d'élève dans les événements. Les calendriers ne relisent ces adresses que toutes les quelques heures : une annulation du jour passe donc aussi par email.
5. **Agendas des salles (Google Workspace, facultatif).**
   - Une salle peut être reliée à l'agenda d'une ressource Workspace, partagé avec un compte de service. Pas de délégation sur tout le domaine.
   - Avant d'enregistrer un cours ou de déplacer une séance, l'appli lit l'occupation de la salle chez Google. Une réunion Workspace apparaît comme un chevauchement.
   - Chaque séance devient un événement de l'agenda de la salle, avec un identifiant tiré de la séance : relancer l'envoi ne crée jamais de doublon.
   - Les envois passent par une file réessayée. Si Google ne répond pas, l'appli continue en le signalant.
   - **Salles lues dans Workspace :** l'écran « Synchroniser avec Workspace » liste les bâtiments et les salles de l'organisation. Il montre un aperçu avant d'écrire : salles nouvelles, renommées ou disparues. Une salle déjà saisie peut être reliée au lieu d'être recréée.
     - L'adresse de l'agenda sert de clé de correspondance, et la capacité de la salle est reprise.
     - Une salle disparue est signalée et on propose de l'archiver. Rien n'est supprimé, et les salles hors Workspace restent possibles.
     - Pour lister les salles, le compte de service reçoit un rôle d'administration personnalisé limité à la lecture des ressources d'agenda. Pas de délégation sur tout le domaine : une fuite de la clé ne donne accès qu'aux salles.
     - L'écran montre les salles dont l'agenda n'est pas encore partagé avec le compte de service.
   - Le lien avec Google est un module à part, derrière une interface « agenda de salle » : sans configuration, rien ne change, et d'autres serveurs d'agenda pourront s'y brancher.
6. **Présences.**
   - Le professeur fait l'appel sur ses séances, depuis son téléphone : présent, absent ou excusé.
   - Pas de motif d'absence en texte libre : il révélerait vite des données de santé.
   - Le professeur peut corriger l'appel pendant 7 jours, ensuite seul le secrétariat le peut.
   - Le secrétariat voit les absences répétées, avec un seuil qu'il règle.
   - Les familles voient le statut des séances de leurs enfants dans leur espace. Ce réglage est actif par défaut et l'association peut le couper.
   - Les présences suivent la durée de conservation des données.

### Import depuis un logiciel de gestion

- Import des personnes, des foyers, des adhésions et des cours, avec essai à blanc et rapport de contrôle.
- Création des comptes à partir de l'import, déjà rattachés à leur fiche, et invitation en masse.
- Format définitif du fichier de report des inscriptions vers le logiciel de gestion.
- **Prérequis :** des exemples d'exports du logiciel, avec des données fictives. Ces fichiers ne doivent jamais entrer dans le dépôt Git.

### Règlements

- **Saisie manuelle** par le secrétariat : chèque, espèces, virement, carte, chèques-vacances, avoir, subvention.
- **Prélèvements SEPA** : échéancier, fichier pour la banque (SEPA XML, pain.008), retours et rejets. Le fichier contient les IBAN en clair : il est généré à la demande, jamais stocké.
- **Paiement en ligne** d'une cotisation (HelloAsso), avec rapprochement automatique des paiements reçus.
- **Soldes** par foyer et par régie, relances des impayés, reçus.
- **Dans l'espace adhérent :** ses cotisations, ses règlements, son solde et un lien de paiement.
- **Prérequis pour une association :** son identifiant créancier SEPA et le format accepté par sa banque.

### Bascule

Pour une association qui remplace son logiciel de gestion : un dernier import complet, la comparaison des soldes, puis l'appli devient la référence. De préférence au début d'une saison.

### Laissé de côté pour l'instant

- **Catalogue :** accompagnateur d'un cours.
- **Espace adhérent :** la modification de ses coordonnées, qui passe par le dossier d'inscription ou par le secrétariat.
- **Comptes :** la suppression d'un compte ; en attendant, un compte se coupe.
- **Déconnexion :** Front-Channel Logout, plus fragile et inutile si chaque appli reçoit les avis.
- **OIDC :** refresh tokens, `client_credentials`, inscription dynamique des clients, connexion par des comptes externes, passkeys.

## Pièges repérés

1. **Email partagé :** une appli cliente qui lie les comptes par email vérifié donnerait à un parent la fiche de son enfant, si celle-ci porte l'adresse du parent. D'où le rattachement des comptes par leur identifiant (`sub`), vérifié à l'écran avant toute connexion.
2. **`form-action 'self'` :** Chrome applique cette règle aux redirections qui suivent un formulaire. La connexion suivie d'une redirection vers l'appli cliente serait bloquée. Le retour passe donc par une page intermédiaire (`/oidc/reprendre`), sans assouplir la politique de sécurité du contenu.
3. **Adresse de l'émetteur :** elle est liée aux `sub` enregistrés dans chaque appli et ne doit plus changer. Il faut la choisir avant la mise en production.
4. **Panne d'Adhérents :** les sessions déjà ouvertes dans les applis continuent, mais plus personne ne peut se connecter. Sa sauvegarde devient critique, clés de signature comprises.
5. **Une seule appli pour l'OIDC et les IBAN :** une faille exposerait les deux. La clé dédiée, le droit à part, le code de double authentification et le journal des lectures limitent ce risque sans le supprimer. Si l'appli grossit, la partie paiements pourra sortir dans un service séparé.

## Avant une mise en production

- Longueur minimale des mots de passe : 12 caractères conseillés, avant d'inviter les adhérents.
- Adresse définitive du serveur OIDC.
- Hébergement et HTTPS.
- Sauvegardes chiffrées, avec `SECRET_KEY` et `BANK_DATA_KEYS` sauvegardées à part de la base.
- Suite de conformité de l'OpenID Foundation. Son profil Basic OP n'envoie pas PKCE, obligatoire ici : il faudra la lancer avec un point d'entrée de test.
- Preuve avec une appli tierce réelle (Nextcloud, Gitea, Grafana…) : les tests utilisent une appli cliente générique.
- Registre des traitements RGPD et information des familles.

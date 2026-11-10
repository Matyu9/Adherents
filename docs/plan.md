# Plan du projet

Ce document regroupe les décisions prises depuis le 4 novembre 2026 et le découpage en phases. À tenir à jour quand une décision change.

## Objectif

Adhérents a deux missions :

1. **Faire le lien entre le logiciel de gestion et les adhérents**, surtout pour les inscriptions et réinscriptions, **puis le remplacer** : catalogue, cotisations et règlements (décision du 8 novembre 2026).
2. **Être le fournisseur d'identité de l'association.** L'Inventaire en sera le premier client, et toute appli standard devra pouvoir s'y brancher sans code spécifique.

```
            Adhérents
  ┌──────────────────────────────┐
  │ Annuaire, foyers, dossiers   │
  │ Cours, cotisations, SEPA     │
  │ Comptes et groupes           │
  │ Serveur OIDC  ───────────────┼──▶ Inventaire
  └──────────────────────────────┘──▶ future appli
```

## Décisions prises

| Sujet | Décision |
|---|---|
| Comptes | Adhérents décide qui peut se connecter. Chaque appli garde ses droits détaillés. |
| Connexion | Adhérents est lui-même le serveur OIDC, sans Authentik. |
| Emplacement du serveur OIDC | Dans l'appli Adhérents, en module isolé (`app/idp/`), qu'on pourra extraire plus tard. |
| Socle technique | Copié de l'Inventaire, pas partagé en paquet. |
| Remplacement du logiciel de gestion (8 novembre 2026) | À terme, Adhérents le remplace : saisons, inscriptions, tarifs et cotisations entreront dans l'appli. Le logiciel de gestion reste la référence jusqu'à la bascule. |
| IBAN (8 novembre 2026) | Conservés dans l'appli, chiffrés avec une clé dédiée. Voir [annuaire.md](annuaire.md#iban-et-mandats-sepa). Remplace la décision de les supprimer après transfert. |
| Modèle de l'annuaire | [annuaire.md](annuaire.md), mis en place le 9 novembre 2026 (migration `a0000006annuaire`). |
| Modèle du catalogue | [catalogue.md](catalogue.md), mis en place le 10 novembre 2026 (migration `a0000002catalogue`). |
| Inscriptions | [inscriptions.md](inscriptions.md), mises en place le 10 novembre 2026 (migrations `a0000003inscriptions` et `a0000004conservation`). L'annuaire ne change qu'à la validation d'un dossier. |
| Mandat SEPA (8 novembre 2026) | Suivi dans l'appli : RUM, date de signature, premier et dernier prélèvement. Pas de dépôt du document signé : son lieu de conservation reste à préciser. |
| Adhésion (9 novembre 2026) | Chaque élève est adhérent et paie son adhésion, une par saison. Un élève de moins de 16 ans est représenté à l'assemblée générale par un responsable, qui ne paie pas d'adhésion. |
| Accès des professeurs (9 novembre 2026) | Un professeur ne voit que ses élèves et leurs responsables, à partir des inscriptions. |
| Date de bascule (9 novembre 2026) | Aucune : on construit au fil de l'eau, et la date se décidera quand l'appli couvrira tout. |
| Base de données (9 novembre 2026) | PostgreSQL, à la place de SQLite : migrations sans pièges avant les phases financières, et sauvegardes continues avec restauration à la seconde (pgBackRest, à mettre en place). Les migrations SQLite ont été remplacées par une migration initiale, aucune base de production n'existant encore. L'Inventaire reste en SQLite. |
| Comptes des mineurs (9 novembre 2026) | Dès 16 ans, avec l'accord d'un responsable légal confirmé à la création et noté au journal. Avant, le responsable gère la fiche depuis son compte. La gestion des comptes sera retravaillée. |
| Règlements (9 novembre 2026) | Trois voies : prélèvement SEPA généré par l'appli, paiement en ligne sur HelloAsso, et saisie manuelle par le secrétariat (chèque, espèces, ANCV…). |
| Ordre des phases (6 novembre 2026) | Sans exports du logiciel de gestion pour l'instant : les comptes et l'OIDC passent d'abord (2 → 3 → 4 → 5), l'annuaire ensuite. |
| Comptes des adhérents (6 novembre 2026) | Le système de comptes se construit dès maintenant, mais les adhérents ne sont invités qu'une fois l'annuaire en place, pour que leurs comptes soient créés déjà rattachés à leur fiche. |

## Principes proposés, à confirmer

Ces principes ont été proposés mais pas formellement validés. Ils guident le travail tant qu'aucune décision ne les remplace :

- **Séparer `Account`, `Person` et `Household`.** Un parent a une seule adresse email et plusieurs enfants, et un enfant sans email n'a pas de compte : son responsable légal le gère depuis le sien.
- **Garder l'adhésion et l'accès indépendants.** Une saison terminée ne coupe pas un compte : un parent doit encore voir l'instrument que son enfant a en garde.
- **Considérer le logiciel de gestion comme la référence jusqu'à la bascule**, sauf pendant qu'un dossier est ouvert.

## Questions ouvertes

| Question | Recommandation | À trancher avant |
|---|---|---|
| Exports du logiciel de gestion : comment se présentent-ils ? | Voir [ci-dessous](#exports-attendus-pour-la-phase-1) | Phase 1, décalée après la phase 5 |
| Quel fichier sert à l'import initial, et le logiciel de gestion sait-il importer ? | Dépend des exports | Phase 1 |
| Base de l'appli ou OpenLDAP pour les comptes ? | Base de l'appli, retenue par défaut pour la phase 2 : OpenLDAP ajoute un service sans remplacer ni le serveur OIDC ni le reste | Phase 2 |
| Longueur minimale des mots de passe : 8 caractères suffisent-ils pour un compte qui ouvrira toutes les applis ? | À relever avant d'inviter les adhérents | Phase 3 |
| Où est conservé le mandat SEPA signé : papier au secrétariat, signature électronique ? | À préciser par l'association | Phase 10 |
| Format du fichier de prélèvement accepté par la banque | SEPA XML (pain.008) : l'ancien format AFB ne sert plus pour les prélèvements depuis 2014. À confirmer avec la banque, avec l'identifiant créancier SEPA (ICS) de l'association | Phase 10 |
| Une ou deux campagnes HelloAsso, pour les régies EMHN et OHN ? | À préciser par le trésorier | Phase 10 |
| Adresse définitive du serveur OIDC ? | Par exemple `https://compte.<domaine>` | Mise en production de la phase 3 |

### Questions pour la directrice

Elles bloquent les tarifs et les cotisations (phase 9), pas l'annuaire :

- Comment s'applique la dégressivité « défavorable famille » : quel enfant paie plein tarif, quelle réduction pour les suivants ?
- Comment la tranche de quotient familial est-elle attribuée : justificatif CAF, avis d'imposition, tranche C par défaut ?
- Un parent présent dans deux familles est-il saisi deux fois aujourd'hui ?
- Que recouvrent cycle, niveau, catégorie (scolaire ou non) et modalité d'une inscription ?
- Assemblée générale : quel responsable représente un élève de moins de 16 ans, et un parent de trois enfants a-t-il trois voix ?
- Combien de temps garder les données d'un ancien adhérent ?

### Exports attendus pour la phase 1

Ils peuvent modifier le modèle des foyers et des personnes.

- **Personnes :** adhérents et élèves, avec toutes les colonnes disponibles.
- **Familles :** responsables légaux et payeurs, si l'export est séparé.
- **Inscriptions :** sur deux saisons si possible.
- **Catalogue :** activités, cours, instruments.
- **Modèle d'import du logiciel de gestion**, s'il existe : c'est le plus important, car il fixe ce que l'appli devra produire.
- **RIB :** seulement la forme des colonnes, avec de faux IBAN.

Sur la forme :

- garder le format d'origine, sans rouvrir ni enregistrer le fichier dans Excel ;
- utiliser des données fictives, car il y a des mineurs ; une trentaine de lignes suffit ;
- garder les cas compliqués : parent avec plusieurs enfants, parents séparés avec deux payeurs, adulte seul, ancien adhérent, noms accentués, champs vides.

**Ces fichiers ne doivent jamais entrer dans le dépôt Git.**

## Phases

Les comptes et l'OIDC passent avant les inscriptions, car celles-ci s'appuient sur les personnes et les droits.

**Ordre de réalisation : 0 → 2 → 3 → 4 → 5 → 1 → 8 → 6 → 7 → 9 → 10 → 11.** Le catalogue (8) passe avant les campagnes (6), où les familles choisissent leurs activités. Les numéros de phase ne changent pas.

### Phase 0 : socle (terminée le 5 novembre 2026)

- Nouveau dépôt à partir du socle de l'Inventaire : interface, permissions, journal, emails, Docker.
- Corrections : plus de JavaScript en ligne, CSP bloquante, thème sombre sans doublon, limitation des tentatives en base.
- **Tests :** balayage des routes par rôle, migrations sur base remplie.

### Phase 1 : annuaire (étapes 1 à 5 écrites le 9 novembre 2026, branche `phase-1-annuaire`)

Modèle : voir [annuaire.md](annuaire.md), qui liste aussi les écarts avec la proposition.

1. **Modèles et migration** : personnes, foyers et leurs membres, téléphones, numéros externes, professeurs, saisons et adhésions. Plus `Account.person_id`, et la saison en cours dans les réglages.
2. **IBAN** : chiffrement AES-GCM avec la clé `BANK_DATA_KEYS`, `flask bank-rotate-key`, refus de démarrer sans la clé. Lecture en clair avec le droit `bank.view` et une nouvelle saisie du TOTP, journalisée.
3. **Écrans** :
   - liste avec recherche sans accents et filtres (saison, élèves actifs, anciens, professeurs) ;
   - fiche d'une personne, fiche d'un foyer avec ses membres ;
   - fusion de deux fiches, archivage.
4. **Droits** : `directory.view`, `directory.edit`, `directory.merge`, `bank.view`. Le lieu de naissance reste réservé aux gestionnaires. L'accès d'un professeur à ses élèves attend les inscriptions (phases 8 et 6).
5. **Rattachement des comptes et invitation des adhérents** (voir ci-dessous).
6. **Import** depuis le logiciel de gestion, avec essai à blanc et rapport de contrôle. La correspondance avec les exports viendra quand ils seront disponibles, sans modifier le modèle. **Seule étape restante.**

- **Tests :** foyer partagé, parent dans deux foyers, doublons et fusion, droits d'accès aux fiches, IBAN (voir [annuaire.md](annuaire.md#tests-prévus)).
- **Rattachement des comptes :** relier chaque compte existant à sa personne, avec un écran de vérification, car l'email seul ne suffit pas (adresse partagée, parents séparés). `Account` reçoit alors un lien vers `Person`, vide au départ.
- **Invitation des adhérents :** les comptes sont créés à partir de l'import, déjà rattachés à leur fiche. En attendant, un compte se crée depuis la fiche d'un adulte qui a une adresse, avec son invitation ; l'invitation en masse viendra avec l'import.
- **Prérequis :** les exports du logiciel de gestion, pour l'import seulement : les étapes 1 à 5 n'en dépendent pas.

### Phase 2 : comptes (terminée le 6 novembre 2026)

- Invitation, mot de passe oublié, changement d'email confirmé par lien.
- Écran de gestion des comptes, groupes, sessions révocables et journal.
- TOTP obligatoire pour les administrateurs : ce compte ouvrira toutes les applis. Il l'est aussi pour tout rôle qui gère les comptes ou les groupes.
- **Tests :** limitation des tentatives, révocation, `email_verified` vrai uniquement après un clic sur le lien.
- **Repris de l'Inventaire :** seulement le mécanisme des liens à usage unique. Adhérents est le serveur d'identité : rien de ce qui concerne le client OIDC, les prêts, les mots de passe provisoires, les photos, les jetons d'API, la fusion ou l'archivage n'a été repris.
- **Reporté :** la suppression d'un compte, avec l'anonymisation de la phase 6 ; en attendant, un compte se coupe. L'import d'une liste d'adresses n'a pas été fait : les comptes des adhérents viendront de l'annuaire.

### Phase 3 : serveur OIDC (écrite, en cours de test sur la branche `phase-3-oidc`)

- **Points d'accès :** découverte, `authorize`, `token`, `userinfo`, `jwks`, avec Authlib.
- **Règles strictes :**
  - Authorization Code uniquement, avec PKCE S256 ;
  - URL de retour comparée à l'identique ;
  - code à usage unique, valable 60 secondes et stocké sous forme d'empreinte ;
  - jetons signés en RS256, avec rotation des clés ;
  - secrets clients affichés une seule fois.
- **Écran « Applications » :** déclarer un client, choisir les groupes autorisés à s'y connecter et ceux qui lui sont transmis, demander un consentement sauf pour les applis de l'association.
- **`sub` :** un UUID opaque, jamais l'email ni l'identifiant interne. Le champ existe déjà.
- **Tests :** code rejoué, mauvais `redirect_uri`, PKCE absent ou faux, compte coupé, personne hors groupe autorisé. La suite de conformité de l'OpenID Foundation (profil Basic OP) sert de critère de réussite.
- **Suite de conformité, pas encore passée :** Basic OP n'envoie pas PKCE, sauf dans un test. Comme PKCE est obligatoire ici, presque tous ses tests échoueraient tels quels. À trancher : la lancer avec un point d'entrée de test sans PKCE, hors de l'appli, et un émetteur joignable par la suite.

### Phase 4 : déconnexion (écrite le 7 novembre 2026, sur la branche `phase-4-deconnexion`)

- Déconnexion initiée par l'appli (`end_session_endpoint`).
- Back-Channel Logout, avec une file d'envoi réessayée par le `scheduler` si une appli est injoignable.
- Déconnexion envoyée quand un compte est coupé, ou quand les groupes transmis à une appli changent.
- **Tests :** coupure propagée, appli en panne puis rétablie, jeton de déconnexion invalide.
- **Fait aussi :** `sid` public sur chaque session, mis dans le jeton d'identité ; avis quand une autorisation est retirée ; filet de sécurité dans `flask run-tasks` pour une session disparue sans prévenir ; suppression d'une appli seulement une fois fermée.
- **Non fait :** Front-Channel Logout (des iframes vers chaque appli), plus fragile et inutile si chaque appli reçoit les avis.

### Phase 5 : branchement de l'Inventaire (écrite le 7 novembre 2026, branches `phase-5-inventaire` ici et `phase-5-adherents` dans l'Inventaire)

- **Dans l'Inventaire :** point d'accès Back-Channel Logout qui ferme ses `LoginSession`, déconnexion transmise au serveur OIDC, `external_id` sur `User`.
- **Migration des comptes :** script avec écran de vérification, qui pré-remplit `oidc_sub`.
- **`external_id` :** laissé vide à cette phase. Les enfants n'ont pas de compte, donc il désignera une personne et sera rempli après l'annuaire, pour la phase 7.
- **Bascule :** `OIDC_ONLY=1` une fois validé, en gardant un administrateur local.
- **Preuve de généralité :** brancher aussi une appli tierce standard, sans toucher à son code.
- **Tests :** parcours Playwright avec les deux applis réelles.
- **Fait :**
  - ici, `flask oidc-export-accounts`, qui exporte les comptes ayant accès à une appli ;
  - dans l'Inventaire, l'écran « Rattacher les comptes OIDC », réservé aux administrateurs. Seuls l'adresse vérifiée et le nom ensemble y sont cochés d'office ;
  - `OIDC_LINK_BY_EMAIL=0` dans l'Inventaire : sans cela, une ligne « adresse partagée » laissée décochée aurait quand même donné la fiche de l'enfant au parent, à sa première connexion ;
  - l'accès de secours des administrateurs, `/connexion?local=1` ;
  - `tests/test_inventaire_e2e.py`, qui lance le vrai Inventaire dans son propre processus.
- **Trouvé par ce parcours et corrigé dans l'Inventaire :**
  - une fiche d'emprunteur rattachée restait inactive, et la personne était refusée ;
  - le bouton « Se connecter avec… » redirigeait après un formulaire. C'était signalé sans être bloqué, car la CSP de l'Inventaire est en mode rapport par défaut, mais il aurait cassé sous Chrome en mode bloquant. Il passe maintenant par une page intermédiaire.
- **Pas fait :** la preuve avec une appli tierce réelle, car il faudrait l'installer, donc Docker ou un service externe. Les tests utilisent une appli cliente Authlib générique, qui ne connaît d'Adhérents que son adresse de découverte. Reste à choisir l'appli (Nextcloud, Gitea, Grafana…).

### Phase 6 : campagnes d'inscription (écrite le 10 novembre 2026, branche `phase-6-campagnes`)

Fonctionnement : voir [inscriptions.md](inscriptions.md).

- **Fait :** campagnes avec leurs consentements, dossier pré-rempli des familles, formulaire public des nouvelles familles avec vérification de l'email, revue et validation qui recopient le dossier dans l'annuaire (adhésions, inscriptions aux cours, consentements datés, IBAN), fichier de report vers le logiciel de gestion, relances hebdomadaires, « Mes élèves » pour les professeurs, durée de conservation et anonymisation.
- **Décidé en l'écrivant :** l'annuaire ne change qu'à la validation, par le secrétariat ; la famille saisit dans le dossier. La durée de conservation n'a pas de valeur par défaut : rien n'est anonymisé tant que la direction ne l'a pas fixée.
- **Pas fait :** mandat SEPA, cotisations, liste d'attente, heure de passage, format définitif du fichier de report (il attend le modèle d'import du logiciel de gestion).

Prévu au départ :

1. Un admin ouvre la campagne de la saison, avec ses dates.
2. Le parent retrouve son dossier pré-rempli depuis le logiciel de gestion.
3. Il corrige ses informations, choisit les activités, donne ses consentements et renseigne son IBAN.
4. Le dossier passe par les statuts : brouillon, soumis, à corriger, validé. Tant que le logiciel de gestion reste la référence, s'y ajoute « reporté dans le logiciel de gestion ».
5. La validation crée l'adhésion et les inscriptions de la saison. Avant la bascule, l'admin récupère aussi les changements à reporter dans le logiciel de gestion.

Les nouvelles inscriptions passent par un formulaire public avec vérification de l'email. Les relances concernent les dossiers incomplets et les familles qui n'ont pas répondu. Cette phase prévoit aussi les durées de conservation et l'anonymisation, ainsi que les consentements datés par saison (droit à l'image…).

Une fois cette phase faite, chaque professeur voit ses élèves.

**IBAN saisis par les familles :** la protection est construite en phase 1 (voir [annuaire.md](annuaire.md#protection)). S'y ajoutent ici :

- **Saisie** uniquement dans un champ, sans dépôt de RIB en PDF ou en photo, avec contrôle de la clé de l'IBAN.
- **Rattachement** au foyer payeur, et non à l'enfant. Le titulaire du compte est enregistré à part.
- **Fichier de report** vers le logiciel de gestion, tant qu'il reste la référence : il contient les IBAN en clair sur l'ordinateur de l'admin. L'appli ne peut pas le protéger, donc il faut rappeler à l'écran de le supprimer après l'import.
- **RGPD :** durée de conservation à justifier au registre des traitements ; mandat supprimé 13 mois après son dernier prélèvement.

### Phase 7 : espace adhérent

- Le parent voit son foyer, ses dossiers et les instruments de ses enfants.
- Nouvelle route API dans l'Inventaire pour lire les prêts par `external_id`, avec un compte de service limité à cette lecture.
- Une fois les phases 9 et 10 faites : ses cotisations, ses règlements et son solde, et un lien de paiement HelloAsso.
- **Tests :** un parent ne voit que son foyer, y compris en modifiant l'identifiant dans l'URL.

### Phase 8 : catalogue (écrite le 10 novembre 2026, branche `phase-8-catalogue`)

Modèle : voir [catalogue.md](catalogue.md).

- Disciplines, niveaux, sites et salles, communs à toutes les saisons. Ce qui a servi s'archive au lieu de se supprimer.
- Cours d'une saison : discipline, niveau, groupe, professeur, jour, horaires, salle, effectif maximum, fermeture à la main. Chevauchements de salle ou de professeur signalés.
- Disciplines enseignées par chaque professeur, par saison ; confier un cours ajoute sa discipline.
- Copie du catalogue d'une saison sur une saison vide, avec un aperçu avant d'écrire.
- Droits `catalog.view` et `catalog.edit`, donnés au gestionnaire.
- **Tests :** copie de saison, cours complet, professeur parti, valeurs archivées, chevauchements, fusion de deux fiches de professeur, contraintes de la migration.
- **Pas fait :** l'import des cours (avec celui de la phase 1), le remplaçant et l'accompagnateur d'un cours.

### Phase 9 : tarifs et cotisations

- **Grille par saison** : éléments de cotisation (adhésion, formules, pratiques collectives, location…) et tarifs selon la commune, la tranche de quotient familial et le statut adulte. Les codes d'éléments changent d'une saison à l'autre : aucun ne sert de clé durable.
- **Régies** : EMHN et OHN, chaque élément relevant de l'une d'elles.
- **Situation du foyer par saison** : tranche de quotient familial, et le quotient seulement si nécessaire, visible du trésorier et de la direction.
- **Calcul des cotisations** d'un foyer à partir de ses inscriptions, avec la dégressivité et l'arrondi au décime. Le calcul est proposé, puis validé : un gestionnaire peut le corriger, et la correction est tracée.
- **Prérequis :** les réponses de la directrice sur la dégressivité et le quotient familial.
- **Tests :** grille comparée aux montants appelés par le logiciel de gestion sur une saison importée ; c'est le critère de réussite.

### Phase 10 : règlements

- **Saisie manuelle** par le secrétariat : chèque, espèces, virement, carte, ANCV, chèque loisirs, chèque CE, avoir, subvention.
- **Prélèvements SEPA** : échéancier, fichier pour la banque, retours et rejets. Le fichier contient les IBAN en clair : il est généré à la demande, jamais stocké.
- **HelloAsso** : paiement en ligne d'une cotisation, et rapprochement automatique des paiements reçus.
- **Soldes** par foyer et par régie, relances des impayés, reçus.
- **Prérequis :** l'identifiant créancier SEPA de l'association et le format accepté par la banque.
- **Tests :** fichier SEPA validé contre le schéma XML, rejet d'un prélèvement, paiement HelloAsso reçu deux fois.

### Phase 11 : bascule

- Dernier import complet depuis le logiciel de gestion, comparaison des soldes, puis l'appli devient la référence.
- La date est à choisir quand les phases 8 à 10 seront terminées, de préférence au début d'une saison.
- Le logiciel de gestion reste consultable en lecture pour l'historique.

## Pièges déjà repérés

1. **Email partagé :** l'Inventaire lie les comptes par email vérifié. Si la fiche d'un enfant porte l'adresse du parent, le parent récupérerait cette fiche et ses prêts. D'où le pré-remplissage de `oidc_sub` en phase 5, avant toute connexion.
2. **`form-action 'self'` :** Chrome applique cette règle aux redirections après un formulaire. Le login POST suivi d'une redirection vers l'appli cliente serait bloqué. Résolu en phase 3 sans toucher à la CSP : après un formulaire, le retour passe par une page intermédiaire (`/oidc/reprendre`), vérifié avec Playwright sous Chromium.
3. **Adresse de l'émetteur :** elle est liée aux `sub` enregistrés dans chaque appli et ne doit plus changer ensuite. Il faut la choisir avant la production.
4. **Panne d'Adhérents :** les sessions déjà ouvertes dans l'Inventaire continuent, mais plus personne ne peut se connecter. Sa sauvegarde devient critique, clés de signature comprises.
5. **Une seule appli pour l'OIDC et les IBAN :** une faille exposerait les deux. Les IBAN étant désormais conservés, leur clé dédiée, le droit `bank.view` avec une nouvelle saisie du TOTP et le journal des lectures limitent ce risque sans le supprimer. Si l'appli grossit, on pourra sortir la partie paiements dans un service séparé.

## Avant la mise en production

- Longueur minimale des mots de passe : 12 caractères conseillés, avant d'inviter les adhérents.
- Adresse définitive du serveur OIDC.
- Hébergement et HTTPS.
- Sauvegardes chiffrées, avec `SECRET_KEY` et `BANK_DATA_KEYS` sauvegardées à part de la base.
- Suite de conformité OIDC.
- Preuve avec une appli tierce réelle.
- Registre des traitements RGPD et information des familles.

## Pour plus tard

Refresh tokens, `client_credentials`, inscription dynamique des clients, connexion par des comptes externes (Google…), passkeys.

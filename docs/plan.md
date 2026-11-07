# Plan du projet

Ce document regroupe les décisions prises depuis le 4 novembre 2026 et le découpage en phases. À tenir à jour quand une décision change.

## Objectif

Adhérents a deux missions :

1. **Faire le lien entre le logiciel de gestion et les adhérents**, surtout pour les inscriptions et réinscriptions.
2. **Être le fournisseur d'identité de l'association.** L'Inventaire en sera le premier client, et toute appli standard devra pouvoir s'y brancher sans code spécifique.

```
            Adhérents
  ┌──────────────────────────────┐
  │ Annuaire, foyers, dossiers   │
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
| Paiements | Hors périmètre. |
| IBAN | Conservés seulement jusqu'au transfert vers le logiciel de gestion, chiffrés, puis supprimés. |
| Mandat SEPA | Pas de document dans l'appli : au plus une coche « Mandat signé », posée par un admin et tracée au journal. |
| Ordre des phases (6 novembre 2026) | Sans exports du logiciel de gestion pour l'instant : les comptes et l'OIDC passent d'abord (2 → 3 → 4 → 5), l'annuaire ensuite. |
| Comptes des adhérents (6 novembre 2026) | Le système de comptes se construit dès maintenant, mais les adhérents ne sont invités qu'une fois l'annuaire en place, pour que leurs comptes soient créés déjà rattachés à leur fiche. |

## Principes proposés, à confirmer

Ces principes ont été proposés mais pas formellement validés. Ils guident le travail tant qu'aucune décision ne les remplace :

- **Séparer `Account`, `Person` et `Household`.** Un parent a une seule adresse email et plusieurs enfants, et un enfant sans email n'a pas de compte : son responsable légal le gère depuis le sien.
- **Garder l'adhésion et l'accès indépendants.** Une saison terminée ne coupe pas un compte : un parent doit encore voir l'instrument que son enfant a en garde.
- **Considérer le logiciel de gestion comme la référence**, sauf pendant qu'un dossier est ouvert.

## Questions ouvertes

| Question | Recommandation | À trancher avant |
|---|---|---|
| Exports du logiciel de gestion : comment se présentent-ils ? | Voir [ci-dessous](#exports-attendus-pour-la-phase-1) | Phase 1, décalée après la phase 5 |
| Quel fichier sert à l'import initial, et le logiciel de gestion sait-il importer ? | Dépend des exports | Phase 1 |
| Base de l'appli ou OpenLDAP pour les comptes ? | Base de l'appli, retenue par défaut pour la phase 2 : OpenLDAP ajoute un service sans remplacer ni le serveur OIDC ni le reste | Phase 2 |
| Longueur minimale des mots de passe : 8 caractères suffisent-ils pour un compte qui ouvrira toutes les applis ? | À relever avant d'inviter les adhérents | Phase 3 |
| Combien de chiffres de l'IBAN garder (4 à 8) ? | 4 : ils suffisent à reconnaître le compte | Phase 6 |
| Où va le mandat SEPA signé : logiciel de gestion, papier, banque ? | À préciser par l'association | Phase 6 |
| Adresse définitive du serveur OIDC ? | Par exemple `https://compte.<domaine>` | Mise en production de la phase 3 |

### Exports attendus pour la phase 1

Ils peuvent modifier le modèle des foyers et des personnes, ensuite coûteux à changer sous SQLite.

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

**Ordre de réalisation : 0 → 2 → 3 → 4 → 5 → 1 → 6 → 7.** La phase 1 attend les exports du logiciel de gestion. Les numéros de phase ne changent pas.

### Phase 0 : socle (terminée le 5 novembre 2026)

- Nouveau dépôt à partir du socle de l'Inventaire : interface, permissions, journal, emails, Docker.
- Corrections : plus de JavaScript en ligne, CSP bloquante, thème sombre sans doublon, limitation des tentatives en base.
- **Tests :** balayage des routes par rôle, migrations sur base remplie.

### Phase 1 : annuaire

- Personnes, foyers, responsables légaux, recherche sans accents, fusion de doublons et archivage.
- Import depuis le logiciel de gestion avec essai à blanc, avec un `external_ref` sur chaque fiche.
- **Tests :** foyer partagé, doublons, droits d'accès aux fiches.
- **Rattachement des comptes :** relier chaque compte existant à sa personne, avec un écran de vérification, car l'email seul ne suffit pas (adresse partagée, parents séparés). `Account` reçoit alors un lien vers `Person`, vide au départ.
- **Invitation des adhérents :** les comptes sont créés à partir de l'import, déjà rattachés à leur fiche.
- **Prérequis :** les exports du logiciel de gestion.

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

### Phase 5 : branchement de l'Inventaire

- **Dans l'Inventaire :** point d'accès Back-Channel Logout qui ferme ses `LoginSession`, déconnexion transmise au serveur OIDC, `external_id` sur `User`.
- **Migration des comptes :** script avec écran de vérification, qui pré-remplit `oidc_sub`.
- **`external_id` :** laissé vide à cette phase. Les enfants n'ont pas de compte, donc il désignera une personne et sera rempli après l'annuaire, pour la phase 7.
- **Bascule :** `OIDC_ONLY=1` une fois validé, en gardant un administrateur local.
- **Preuve de généralité :** brancher aussi une appli tierce standard, sans toucher à son code.
- **Tests :** parcours Playwright avec les deux applis réelles.

### Phase 6 : campagnes d'inscription

1. Un admin ouvre la campagne de la saison, avec ses dates.
2. Le parent retrouve son dossier pré-rempli depuis le logiciel de gestion.
3. Il corrige ses informations, choisit les activités, donne ses consentements et renseigne son IBAN.
4. Le dossier passe par les statuts : brouillon, soumis, à corriger, validé, transféré au logiciel de gestion.
5. L'admin ne récupère que les changements à reporter dans le logiciel de gestion.

Les nouvelles inscriptions passent par un formulaire public avec vérification de l'email. Les relances concernent les dossiers incomplets et les familles qui n'ont pas répondu. Cette phase prévoit aussi les durées de conservation et l'anonymisation.

**Protection des IBAN, à construire avant d'ouvrir les inscriptions :**

- **Chiffrement** dans l'appli avant d'aller en base, avec une clé hors de `data/`, et une clé par campagne détruite à sa clôture.
- **Suppression** quand l'admin confirme « transféré au logiciel de gestion », et non au téléchargement du fichier, pour ne rien perdre si l'import échoue. Le `scheduler` supprime aussi ceux des dossiers abandonnés à la fin de la campagne. Seuls les derniers chiffres restent.
- **Affichage** masqué par défaut. Le voir en entier demande un droit dédié `bank.view` et une nouvelle saisie du TOTP, et chaque consultation est journalisée.
- **Fuites à bloquer :** jamais d'IBAN dans les claims OIDC, l'API, les emails, les logs ou les exports généraux. `activity.diff()` le masque déjà.
- **Saisie** uniquement dans un champ, sans dépôt de RIB en PDF ou en photo.
- **Rattachement** au payeur, c'est-à-dire au compte ou au foyer, et non à l'enfant. Le titulaire du compte est enregistré à part.
- **Fichier d'export :** il contient les IBAN en clair sur l'ordinateur de l'admin. L'appli ne peut pas le protéger, donc il faut rappeler à l'écran de le supprimer après l'import.
- **RGPD :** durée de conservation à justifier au registre des traitements. Aucune durée officielle pour les IBAN n'a été trouvée : à confirmer sur le site de la CNIL.
- **Tests :** après la suppression, l'IBAN est introuvable dans le fichier de base.

### Phase 7 : espace adhérent

- Le parent voit son foyer, ses dossiers et les instruments de ses enfants.
- Nouvelle route API dans l'Inventaire pour lire les prêts par `external_id`, avec un compte de service limité à cette lecture.
- **Tests :** un parent ne voit que son foyer, y compris en modifiant l'identifiant dans l'URL.

## Pièges déjà repérés

1. **Email partagé :** l'Inventaire lie les comptes par email vérifié. Si la fiche d'un enfant porte l'adresse du parent, le parent récupérerait cette fiche et ses prêts. D'où le pré-remplissage de `oidc_sub` en phase 5, avant toute connexion.
2. **`form-action 'self'` :** Chrome applique cette règle aux redirections après un formulaire. Le login POST suivi d'une redirection vers l'appli cliente serait bloqué. Résolu en phase 3 sans toucher à la CSP : après un formulaire, le retour passe par une page intermédiaire (`/oidc/reprendre`), vérifié avec Playwright sous Chromium.
3. **Adresse de l'émetteur :** elle est liée aux `sub` enregistrés dans chaque appli et ne doit plus changer ensuite. Il faut la choisir avant la production.
4. **Panne d'Adhérents :** les sessions déjà ouvertes dans l'Inventaire continuent, mais plus personne ne peut se connecter. Sa sauvegarde devient critique, clés de signature comprises.
5. **Une seule appli pour l'OIDC et les IBAN :** une faille exposerait les deux. C'est une raison de plus de ne pas conserver les IBAN après le transfert.

## Pour plus tard

Refresh tokens, `client_credentials`, inscription dynamique des clients, connexion par des comptes externes (Google…), passkeys.

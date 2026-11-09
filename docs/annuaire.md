# Annuaire : modèle de données

Proposition du 8 novembre 2026, complétée le 9, **mise en place le 9 novembre 2026** (phase 1, migration `a0000006annuaire`). Les écarts avec la proposition sont listés [à la fin](#écarts-avec-la-proposition). Elle part des exports décrits pour le logiciel de gestion et de deux décisions du jour :

- à terme, Adhérents **remplace** le logiciel de gestion : le modèle doit pouvoir porter les saisons, les inscriptions et les cotisations, même si on ne les construit pas maintenant ;
- Adhérents **conserve les IBAN**, de façon sécurisée.

## Vue d'ensemble

```
                    Account (connexion, déjà existant)
                       │ 0..1
                       ▼
 ExternalRef ──▶ Person ◀──────── HouseholdMember ────────▶ Household ◀── ExternalRef
                  │  │          (responsable 1/2, à charge)     │  │
                  │  └── Phone                          Phone ──┘  │
                  │                                                │
                  ├── Teacher (profil, 0..1)                        │
                  └── Membership ──▶ Season                        └── BankAccount ── SepaMandate
                     (adhésion d'une saison)                           (payeur = foyer, IBAN chiffré)
```

Trois idées le structurent :

1. **Une personne est unique**, quel que soit le nombre de ses rôles : élève, responsable, professeur. Un parent présent dans deux familles (familles recomposées) reste une seule `Person`, membre de deux foyers.
2. **Le foyer est le payeur.** Les cotisations, l'adresse postale et les IBAN se rattachent au foyer, pas à l'enfant.
3. **Ce qui change chaque saison n'est pas sur la fiche.** Catégorie, classe et statut adhérent ou ancien adhérent vont dans `Membership`. La fiche d'une personne ne dépend d'aucune saison.

## Tables

### `person` : une personne

| Colonne | Type | Remarque |
|---|---|---|
| `id` | entier | interne, jamais exposé |
| `uuid` | texte(36), unique | identifiant public : c'est lui que l'Inventaire recevra comme `external_id` (phase 7) |
| `civility` | texte(10), facultatif | `M.`, `Mme` |
| `first_name`, `last_name` | texte(80) | recherche sans accents avec `fold_contains()` |
| `birth_name` | texte(80), facultatif | nom de naissance |
| `sex` | texte(1), facultatif | `F`, `M` : utile pour les statistiques demandées aux écoles |
| `birth_date` | date, facultatif | elle détermine mineur ou majeur, donc qui gère la fiche |
| `birth_place` | texte(120), facultatif | demandé pour le payeur (mandat, chèques vacances) : visible des seuls gestionnaires |
| `email` | texte(255), facultatif, **non unique** | adresse **propre** à la personne. L'adresse d'un parent n'est jamais recopiée sur la fiche de son enfant |
| adresse | voir `AddressMixin` | vide en général : on prend celle du foyer. Remplie pour un parent séparé ou un professeur |
| `emergency_contact` | texte(255), facultatif | nom et téléphone en clair |
| `notes` | texte | observations internes |
| `archived_at` | date et heure | personne archivée, jamais supprimée sans anonymisation |
| `created_at`, `updated_at` | | `TimestampMixin` |

`account_id`, facultatif et unique, relie la fiche à son compte. Il reste vide pour les comptes existants, jusqu'à l'écran de rattachement. Un enfant n'a pas de compte : son responsable gère sa fiche depuis le sien.

### `household` : un foyer, c'est-à-dire le payeur

| Colonne | Type | Remarque |
|---|---|---|
| `id`, `uuid` | | comme `person` |
| `name` | texte(120) | « Famille Dupont », proposé à partir du responsable 1, modifiable |
| adresse | `AddressMixin` | adresse postale de la famille |
| `notes`, `archived_at`, horodatage | | |

**`AddressMixin`** (dans `person` et `household`) : `address_recipient` (destinataire, la ligne 1 de l'export), `address_street` (texte sur plusieurs lignes), `postal_code`, `city` et `country` (`FR` par défaut). Le critère tarifaire « habite la commune » se déduira du code postal et de la ville plutôt que d'être saisi.

### `household_member` : qui fait partie du foyer

| Colonne | Type | Remarque |
|---|---|---|
| `household_id`, `person_id` | clé primaire double | |
| `role` | `guardian` ou `dependent` | responsable ou à charge |
| `guardian_rank` | 1, 2 ou vide | responsable 1 ou 2, unique par foyer |
| `relationship` | texte(40), facultatif | lien juridique : mère, père, tuteur, « lui-même »… |

Cas couverts :

- **adulte seul** : il est le responsable 1 de son propre foyer ;
- **parent dans deux familles** : une seule personne, avec deux lignes ;
- **parents séparés, un seul foyer** : responsables 1 et 2, le second avec sa propre adresse sur sa fiche.

On ne couvre pas un beau-parent responsable d'un seul des enfants du foyer : l'export ne le distingue pas non plus.

### `phone` : téléphones

`id`, `person_id` ou `household_id` (exactement l'un des deux, vérifié par contrainte), `number` (au format international, `+33…`), `label` (« domicile », « portable mère »…), `position`.

L'export range les téléphones 1 à 6 dans la famille, 7 à 9 chez le responsable 2 et 10 et 11 chez l'élève : ils se répartissent donc entre le foyer et les personnes. Un numéro impossible à normaliser est signalé à l'import, pas enregistré.

### `external_ref` : numéros du logiciel de gestion

`id`, `kind` (`student`, `household` ou `teacher`), `value`, puis `person_id` ou `household_id`. Unique sur (`kind`, `value`).

Une table plutôt qu'une colonne, car une même personne peut avoir deux numéros si elle est élève et professeur. C'est ce qui rend l'import rejouable sans doublons. Ces lignes ne servent plus une fois le logiciel de gestion abandonné, et le reste du modèle n'en dépend pas.

### `season` et `membership` : l'adhésion d'une saison

`season` : `id`, `label` (« 2026-2027 », unique), `starts_on`, `ends_on`.

**Saison en cours :** elle est choisie dans les réglages, pas déduite de la date. La campagne de réinscription de la saison suivante s'ouvre pendant que la saison actuelle tourne encore.

`membership` : une personne inscrite comme élève pour une saison.

| Colonne | Remarque |
|---|---|
| `person_id`, `season_id` | uniques ensemble |
| `status` | `active` ou `withdrawn` |
| `withdrawn_on` | date de l'abandon |
| `category` | scolaire, non scolaire, adulte… |
| `school_level`, `school_class` | niveau et classe scolaires de la saison |

- **Création :** à l'import, une ligne par élève de l'export des inscriptions, en `withdrawn` en cas d'abandon. En phase 6, la validation d'un dossier crée la ligne de la nouvelle saison.
- **Historique :** une ligne par saison, jamais supprimée. Un **ancien adhérent** est une personne sans adhésion active sur la saison en cours.
- **Sans effet sur l'accès :** l'adhésion ne coupe aucun compte (voir le [plan](plan.md#principes-proposés-à-confirmer)).
- **Plus tard :** les inscriptions aux disciplines pointeront vers l'adhésion de la saison.

**Décidé le 9 novembre 2026 :** chaque élève est adhérent et paie son adhésion. Un élève de moins de 16 ans est représenté à l'assemblée générale par un responsable, qui ne paie pas d'adhésion. La liste des votants se déduit donc des adhésions, de la date de naissance et des responsables, sans table de plus. Reste à savoir quel responsable vote, et si un parent de plusieurs enfants a plusieurs voix.

### `teacher` : profil de professeur

| Colonne | Remarque |
|---|---|
| `person_id` | clé primaire, qui pointe vers `person` |
| `status` | CDI, CDD, vacataire, bénévole… |
| `started_on`, `left_on` | arrivée et départ ; un départ n'efface pas la fiche |
| `work_email` | adresse professionnelle, prioritaire ; l'adresse personnelle reste sur `person.email` |
| `notes` | |

Une personne à la fois professeur et élève a une seule fiche, avec un profil `teacher`, une adhésion et deux numéros externes. Les disciplines enseignées viendront avec le catalogue, par saison. Les droits dans l'appli passent par le compte (rôle ou groupe « Professeurs »), pas par cette table.

## IBAN et mandats SEPA

### `bank_account`

| Colonne | Type | Remarque |
|---|---|---|
| `id` | | |
| `household_id` | | le payeur |
| `holder_person_id` | facultatif | titulaire, s'il est dans l'annuaire |
| `holder_name` | texte(120) | titulaire tel qu'écrit sur le RIB |
| `iban_ciphertext` | binaire | chiffré, voir plus bas |
| `iban_last4` | texte(4) | seule partie affichée par défaut : `•••• •••• 1234` |
| `iban_fingerprint` | texte(64), indexé | empreinte HMAC avec une clé dérivée de `BANK_DATA_KEYS`, recalculée à la rotation. Elle sert à repérer un même IBAN sur deux foyers et à rejouer l'import sans doublons, sans rien déchiffrer |
| `bic` | texte(11) | pas secret |
| `key_version` | entier | clé qui a chiffré la ligne, pour la rotation |
| `created_at`, `updated_at` | | |

Le code banque, le code guichet, le numéro de compte et la clé RIB de l'export ne sont pas importés : on les recalcule depuis l'IBAN.

### `sepa_mandate`

`id`, `bank_account_id`, `rum` (unique), `signed_on`, `first_debit_on`, `last_debit_on`, `revoked_on`.

On ne stocke pas de document de mandat (voir le plan). Le `scheduler` supprimera un mandat 13 mois après son dernier prélèvement, puis le compte bancaire qui n'a plus de mandat.

### Protection

- **Une clé dédiée, `BANK_DATA_KEYS`**, distincte de `SECRET_KEY`. Changer `SECRET_KEY` ne doit pas faire perdre les IBAN, et une fuite de l'une ne donne pas l'autre. On y met une liste de clés numérotées, pour pouvoir passer à une nouvelle clé : `flask bank-rotate-key` rechiffre tout, puis on retire l'ancienne. L'appli refuse de démarrer s'il existe des IBAN sans la clé qui les a chiffrés.
- **Chiffrement AES-GCM** (paquet `cryptography`, déjà installé), avec l'identifiant du foyer en données associées. Un chiffré recopié d'une ligne à l'autre ne se déchiffre plus.
- **Lecture en clair** réservée au droit `bank.view`, en **mode sécurisé** (code TOTP saisi depuis moins de 10 minutes, sur cette session), et chaque lecture est écrite au journal. Ajouter, supprimer un IBAN ou toucher un mandat demande aussi le mode sécurisé.
- **Jamais en clair ailleurs** : ni dans les claims OIDC, l'API, les emails, les logs, les exports généraux ou le journal (`activity.diff()` les masque déjà).
- **Sauvegardes** : la base et `BANK_DATA_KEYS` sont sauvegardées **séparément**. Une copie de la base seule ne livre aucun IBAN.

**Limite à connaître :** la clé est sur le même serveur que la base. Ce chiffrement protège contre une fuite de la base, d'une sauvegarde ou d'un disque. Il ne protège pas d'une prise de contrôle complète du serveur. Seul un service de clés externe le ferait, ce qui est disproportionné ici.

Le tableau des décisions du [plan](plan.md) prévoyait de supprimer les IBAN après transfert. Cette proposition le remplace.

## Plus tard, sans changer ce qui précède

Ces tables se brancheront sur `person`, `household` et `season`, sans retoucher l'annuaire :

- **catalogue** : disciplines, niveaux, cours avec leur horaire, leur salle et leur professeur ;
- **inscriptions** : une personne inscrite à une discipline et à un niveau pour une saison, avec son statut ;
- **tarifs** : éléments de cotisation et grille **par saison**. Les codes d'éléments changent d'une saison à l'autre, donc aucun code ne sert de clé durable ;
- **situation fiscale du foyer par saison** : tranche de quotient familial (A, B, C), et le quotient seulement si nécessaire ;
- **cotisations appelées et règlements**, rattachés au foyer ;
- **disciplines des professeurs**, par saison.

## Non repris

- n° de sécurité sociale ;
- identifiants et mots de passe de l'extranet du logiciel de gestion ;
- données de santé (traitements, allergies, médecin) : non reprises pour l'instant (décidé le 8 novembre 2026). Si elles le sont un jour, ce sera dans une table à part, avec son propre droit de lecture ;
- droit à l'image et autres consentements : ils arrivent avec les campagnes d'inscription (phase 6), datés et rattachés à une saison.

## Tests prévus

- un parent dans deux foyers, et un adulte seul responsable de lui-même ;
- un seul responsable 1 par foyer ;
- un téléphone rattaché à exactement une fiche, et un numéro externe unique par type ;
- l'adresse d'un parent absente de la fiche de son enfant ;
- migration sur une base remplie, comme pour les phases précédentes ;
- IBAN :
  - introuvable en clair dans le fichier de la base ;
  - indéchiffrable si le chiffré est recopié sur un autre foyer ;
  - toujours lisible après une rotation de clé ;
  - refus de démarrer sans la clé ;
  - absent du journal et des exports.

## Écarts avec la proposition

Décidés en écrivant la phase 1, le 9 novembre 2026 :

- **Le lien compte ↔ fiche est `person.account_id`**, et non `account.person_id`. Ajouter une clé étrangère à `account` aurait obligé à recréer cette table sous SQLite ; avec les clés étrangères actives, sa suppression temporaire aurait vidé en cascade les sessions, les liens et les codes de tous les comptes. Le sens du lien ne change rien d'autre : 0 ou 1 compte par fiche, 0 ou 1 fiche par compte.
- **L'empreinte des IBAN** est dérivée de `BANK_DATA_KEYS` plutôt que d'une troisième clé. Elle change donc à chaque rotation, qui la recalcule ; entre l'ajout d'une clé et la rotation, la recherche d'un même IBAN essaie toutes les clés.
- **L'affichage masqué** est `•••• •••• 1234` : le pays et la clé de contrôle font partie du chiffré.
- **Le journal note la fiche concernée** (`activity.person_id`, `activity.household_id`), ce qui donne l'historique de chaque fiche et de chaque foyer.
- **Compte dès 16 ans** (décidé le 9 novembre 2026) : avant 16 ans, pas de compte, le responsable gère la fiche depuis le sien. À 16 et 17 ans, la personne qui crée le compte confirme qu'un responsable légal a donné son accord, et le journal le note. La gestion des comptes sera retravaillée bientôt.
- **Professeurs à part** (9 novembre 2026) : la table `teacher` ne change pas, mais on ne devient professeur que depuis l'écran **Professeurs**, avec le droit `teacher.manage`, jamais depuis la fiche d'un adhérent. Un parent ou un élève qui enseigne garde une seule fiche : l'écran Professeurs le cherche dans l'annuaire, parmi les adultes seulement. Le contrat ne s'affiche que dans cet écran ; la fiche de l'annuaire montre seulement un badge. Un professeur qui n'est ni adhérent ni membre d'un foyer n'apparaît pas dans la liste des personnes.
- **Fusion** : les deux fiches se mélangent. Un champ rempli d'un seul côté est repris ; quand les deux ont une valeur différente, l'écran demande laquelle garder (sans choix, celle de la fiche gardée). Une différence d'accent ou de casse n'est pas un conflit. Foyers, téléphones, adhésions et notes s'additionnent ; une adhésion en double sur la même saison est abandonnée, et deux fiches qui ont chacune un compte ne fusionnent pas.


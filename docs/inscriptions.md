# Inscriptions : campagnes, dossiers, inscriptions aux cours

Les inscriptions s'appuient sur l'[annuaire](annuaire.md) et le [catalogue](catalogue.md).

## Le parcours

```
 Campagne ouverte ──▶ Dossier du foyer ──▶ envoyé ──▶ validé ──▶ reporté dans le logiciel de gestion
  (dates, consentements)  pré-rempli          │          │
                          brouillon ◀── à corriger        └─▶ annuaire : fiches, adhésions,
                                                               inscriptions aux cours,
                                                               consentements, IBAN
```

1. Le secrétariat **ouvre une campagne** pour une saison, avec ses dates et les consentements à demander.
2. Un **responsable** du foyer ouvre le dossier depuis son compte (**Inscriptions**). Le dossier est pré-rempli depuis l'annuaire : le foyer, ses responsables, et ses élèves avec les cours qui prolongent ceux de l'an dernier (même discipline, même niveau, même groupe si possible).
3. La famille **corrige**, choisit les cours, répond aux consentements, indique un IBAN si elle le souhaite, puis **envoie** le dossier en certifiant l'exactitude des informations.
4. Le secrétariat **relit** le dossier : l'écran montre, champ par champ, ce que la validation changera dans l'annuaire. Il le **renvoie** à la famille avec un message, ou le **valide**.
5. **La validation recopie le dossier dans l'annuaire.** Tant que le logiciel de gestion reste la référence, le secrétariat y reporte ensuite les changements, puis note le dossier comme **reporté**.

**L'annuaire ne change qu'à la validation.** Ce que la famille saisit reste dans le dossier (`dossier.data`, en JSON) jusque-là. Un dossier abandonné ne laisse donc rien dans l'annuaire, et le secrétariat garde la main sur chaque changement.

## Tables

| Table | Rôle |
|---|---|
| `campaign` | Campagne d'une saison : titre, ouverture et clôture (date et heure), message aux familles, formulaire public ou non |
| `campaign_consent` | Consentement demandé : intitulé, texte, pour chaque élève ou pour le foyer, obligatoire ou non |
| `dossier` | Dossier d'un foyer pour une campagne : statut, saisie de la famille (`data`), IBAN chiffré, message de retour, dates |
| `enrollment` | Inscription d'un élève à un cours, rattachée à son adhésion de la saison, datée du jour de la validation. Ce qu'elle devient pendant la saison : voir [cours.md](cours.md). |
| `consent` | Réponse donnée pour une saison, datée, avec le texte tel qu'il était affiché et le nom de la personne qui a répondu |

Règles portées par la base :

- **un dossier par foyer et par campagne** ; une nouvelle famille, sans foyer, n'est pas concernée ;
- un cours qui a des élèves ne se supprime pas, ni une campagne qui a des dossiers, ni une saison qui a des campagnes ;
- un consentement appartient à une personne **ou** à un foyer, jamais aux deux.

**Statuts d'un dossier :** brouillon, envoyé, à corriger, validé, reporté. La famille ne modifie le dossier qu'en brouillon ou « à corriger », et seulement pendant la campagne.

## Les choix de la famille

- **Cours :** seuls ceux de la saison de la campagne sont proposés. Un cours **complet** (fermé à la main, ou effectif maximum atteint par les inscriptions validées) est grisé, sauf s'il était déjà choisi. À la validation, un cours devenu complet entre-temps est signalé au secrétariat, qui décide.
- **Adulte qui prend des cours :** un responsable peut s'inscrire lui-même. Il n'a qu'une fiche, celle de responsable.
- **Élève qui ne revient pas :** il reste dans le dossier, décoché. Seul un élève ajouté dans le dossier peut en être retiré.
- **Consentements :** leur texte est recopié dans chaque réponse (`consent.text`). Si le texte change ensuite, la preuve de ce qui a été accepté ne bouge pas. Ils sont datés de l'envoi du dossier.

## Nouvelles familles

Si la campagne l'autorise, un **formulaire public** (`/inscription/nouvelle/<campagne>`) demande seulement une adresse email. Un lien d'accès au dossier y est envoyé ; il vaut 30 jours, et seule son empreinte est gardée.

- Ouvrir le lien prouve l'adresse (`email_verified_at`) et note le dossier dans la session du navigateur.
- Une nouvelle demande avec la même adresse renvoie un lien vers le **même** dossier, et l'ancien lien cesse de fonctionner.
- La réponse est la même, que l'adresse soit connue ou non. Les demandes sont limitées à 5 par heure, par adresse et par IP.
- Sans envoi d'emails configuré, le formulaire est fermé.
- **À la validation**, le foyer et les fiches sont créés. Le secrétariat vérifie d'abord dans l'annuaire que la famille n'y est pas déjà ; sinon, il fusionnera les fiches ensuite.

## IBAN saisis par les familles

- Saisie dans un champ seulement, contrôlée (clé modulo 97), sans dépôt de RIB.
- **Chiffré aussitôt** avec `BANK_DATA_KEYS`, comme dans l'annuaire, mais lié au **dossier** (le foyer peut ne pas exister encore). Un chiffré recopié d'un dossier à l'autre ne se déchiffre plus. La rotation des clés rechiffre aussi les dossiers.
- Jamais réaffiché en clair à la famille : seuls les quatre derniers chiffres.
- À la validation, il devient un compte bancaire du foyer, sauf si le foyer a déjà cet IBAN. Un IBAN présent sur un autre foyer est signalé.

## Report vers le logiciel de gestion

Tant que le logiciel de gestion reste la référence, **Campagnes → Report** liste les dossiers validés et pas encore reportés.

- **Fichier sans IBAN** (droit `registration.manage`) : une ligne par changement, dossier par dossier.
- **Fichier avec les IBAN en clair** (droits `registration.manage` et `bank.view`, mode sécurisé) : chaque téléchargement est inscrit au journal. L'écran rappelle de supprimer le fichier après la saisie.
- Les changements sont **gardés au moment de la validation**, puisqu'ensuite l'annuaire n'en montre plus aucun.
- **Format provisoire** (CSV, point-virgule) : il sera adapté au modèle d'import du logiciel de gestion quand on l'aura.

## Relances

Une fois par semaine au plus (tâches périodiques), pour chaque campagne ouverte, une notification (et un email si le SMTP est configuré) part vers les responsables qui ont un compte :

- un foyer dont un élève avait une adhésion active la saison précédente et qui n'a pas encore ouvert son dossier ;
- un dossier commencé mais pas envoyé, ou renvoyé « à corriger ».

Les nouvelles familles sans compte ne sont pas relancées : elles ont leur lien par email.

## Professeurs : leurs élèves

**Mes élèves** (`/inscription/mes-eleves`) : un professeur dont le compte est rattaché à sa fiche voit, saison par saison, les élèves inscrits à ses cours, leur âge et leurs responsables (nom, téléphone, email). Rien d'autre de l'annuaire.

## Conservation et anonymisation

**Annuaire → Conservation**, avec le droit `directory.anonymize` (administrateur par défaut).

- **La durée se compte en saisons et n'a pas de valeur par défaut.** C'est une question posée à la direction ; tant qu'elle n'est pas fixée, rien n'est proposé.
- **Sont proposées** les personnes sans adhésion depuis ce nombre de saisons, sans compte, sans profil de professeur, et dont aucun membre du foyer n'a d'adhésion récente.
- **L'anonymisation se décide à l'écran**, fiche par fiche, jamais automatiquement.
- **Ce qui est effacé :**
  - nom, coordonnées, téléphones, numéros externes, consentements et observations ;
  - le jour et le mois de naissance (l'année reste, au 1er janvier) ;
  - le texte du journal qui concernait la personne.
- **Ce qui reste :** les adhésions et inscriptions, sans nom, pour les statistiques des saisons passées.
- **Un foyer dont tous les membres sont anonymisés** perd son nom, son adresse, ses IBAN, ses mandats et ses dossiers.
- **Les dossiers des campagnes trop anciennes** se suppriment depuis le même écran : leur contenu a été recopié dans l'annuaire, ou n'a jamais abouti.

## Droits

| Droit | Donne accès à |
|---|---|
| `registration.view` | les campagnes, les dossiers et leur contenu (sans IBAN en clair) |
| `registration.manage` | créer et modifier les campagnes, renvoyer ou valider un dossier, le fichier de report sans IBAN |
| `registration.manage` + `bank.view` | le fichier de report avec les IBAN, en mode sécurisé |
| `directory.anonymize` | la durée de conservation et l'anonymisation |

Le gestionnaire a `registration.view` et `registration.manage` ; la migration les ajoute aussi à un rôle Gestionnaire déjà enregistré. Une famille n'a besoin d'aucun droit : elle n'ouvre que les dossiers des foyers dont elle est responsable.

## Pas fait

- **Le mandat SEPA** : son lieu de conservation reste à décider, avec les règlements. L'IBAN du dossier devient un compte bancaire, sans mandat.
- **Les cotisations** : voir [tarifs.md](tarifs.md).
- **Une liste d'attente** pour les cours complets : le souhait se note en texte libre.
- **L'heure de passage** d'un élève dans un cours individuel.
- **L'invitation des responsables** qui n'ont pas de compte : elle se fait depuis la fiche, comme avant. L'invitation en masse viendra avec l'import.

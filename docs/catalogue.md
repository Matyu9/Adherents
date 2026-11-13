# Catalogue : modèle de données

Le catalogue décrit **ce qu'on enseigne, où et par qui**, saison par saison. Les [inscriptions](inscriptions.md) des élèves aux cours et les [tarifs](tarifs.md) se branchent sur ces tables sans les modifier.

## Vue d'ensemble

```
 Discipline ◀──┬── Course ──▶ Season
 Level ◀───────┤     │
 Room ─▶ Site ◀┘     └──▶ Teacher (profil de l'annuaire)
                                │
 TeacherDiscipline ─────────────┘  (saison, professeur, discipline)
```

Deux règles le structurent :

1. **Les listes de référence sont communes à toutes les saisons** : types de discipline, disciplines, niveaux, sites et salles. Une valeur qui a servi ne se supprime pas, on l'archive. Elle n'est plus proposée pour les nouveaux cours, mais les saisons passées la gardent.
2. **Un cours appartient à une saison.** On ne le modifie pas d'une saison à l'autre : on recopie le catalogue sur la saison suivante, puis on l'ajuste. L'historique reste donc exact.

## Tables

### Listes de référence

| Table | Colonnes | Remarque |
|---|---|---|
| `discipline_type` | `name` (unique), `position`, `archived_at` | Les types que l'association se donne (« Instrument », « Atelier », « Sport collectif »…). Aucun n'existe à l'installation. `position` fixe leur ordre, et celui des disciplines avec eux. C'est le « type de discipline » des exports. |
| `discipline` | `name` (unique), `type_id`, `archived_at` | `type_id` est facultatif. Un type qui a des disciplines ne se supprime pas ; archivé, il n'est plus proposé pour les nouvelles disciplines. |
| `level` | `name` (unique), `position`, `archived_at` | `position` fixe l'ordre des listes (cycle 1 avant cycle 2). |
| `site` | `name` (unique), `address`, `archived_at` | L'école, une salle municipale… Un site archivé ferme toutes ses salles. |
| `room` | `site_id`, `name`, `archived_at` | Nom unique dans son site. |

Deux noms qui ne diffèrent que par les accents ou la casse sont refusés à la saisie (« Flute » et « Flûte »).

### `course` : un cours d'une saison

| Colonne | Remarque |
|---|---|
| `season_id`, `discipline_id` | obligatoires |
| `level_id` | facultatif : un atelier peut être ouvert à tous les niveaux |
| `group_label` | distingue deux cours de même discipline et de même niveau (« groupe A », « ados ») |
| `teacher_id` | pointe vers `teacher`, pas vers `person` : seul un professeur peut avoir un cours. Vide : professeur à trouver. |
| `weekday`, `starts_at`, `ends_at` | jour (0 = lundi) et créneau, tous facultatifs ; un horaire demande un jour, et le début et la fin vont ensemble |
| `room_id` | facultatif |
| `capacity` | effectif maximum ; vide : pas de limite |
| `individual` | cours individuel : chaque élève a son heure de passage ([cours.md](cours.md#cours-individuels)) |
| `marked_full` | fermé aux inscriptions à la main (le `cours_complet` des exports) |
| `notes` | non recopiées au journal |

**Cours individuel :** un cours de 17 h à 19 h regroupe plusieurs élèves qui passent à des heures différentes. L'horaire est celui du créneau du professeur ; l'heure de passage de chaque élève ira avec son inscription.

**Cours complet :** `Course.is_full(inscrits)` est vrai si le cours est fermé à la main, ou si le nombre d'inscrits atteint l'effectif maximum. Le nombre d'inscrits est celui des inscriptions actives (voir [cours.md](cours.md)).

**Chevauchements :** deux cours du même jour dont les créneaux se recouvrent, dans la même salle ou avec le même professeur, sont signalés à l'enregistrement et dans la liste. C'est un avertissement, pas un refus : deux petits groupes peuvent partager une grande salle. Un cours qui finit à 18 h et un autre qui commence à 18 h ne se chevauchent pas.

### `teacher_discipline` : disciplines d'un professeur, par saison

Clé primaire (`season_id`, `teacher_id`, `discipline_id`). Confier un cours à un professeur ajoute d'office sa discipline pour la saison. À l'inverse, on ne retire pas une discipline à un professeur qui a encore un cours de cette discipline sur la saison.

## Professeur parti

Un professeur est **présent pendant une saison** s'il est arrivé avant sa fin (`started_on`) et n'est pas parti avant son début (`left_on`).

- **Nouveau cours :** seuls les professeurs présents pendant la saison sont proposés.
- **Cours existant :** son professeur reste choisi même s'il est parti, pour qu'un enregistrement ne le retire pas sans qu'on l'ait décidé. Le formulaire et la liste signalent qu'il part ou qu'il est parti pendant la saison.
- **Profil de professeur retiré** (erreur de saisie) : ses cours restent, sans professeur, et ses disciplines sont effacées.
- **Fusion de deux fiches :** les cours et les disciplines de la fiche abandonnée passent à la fiche gardée.

## Copie d'une saison sur la suivante

**Catalogue → Copier une saison.** L'écran montre d'abord un aperçu, sans rien écrire. La copie ne se fait qu'après confirmation, et seulement vers une saison **vide** (sans cours ni disciplines de professeurs) : elle sert à démarrer une saison, pas à fusionner deux catalogues.

| Dans la saison d'origine | Dans la copie |
|---|---|
| cours d'une discipline archivée | non recopié |
| niveau archivé | cours recopié sans niveau |
| salle ou site archivé | cours recopié sans salle |
| professeur absent de la nouvelle saison | cours recopié sans professeur |
| cours fermé à la main | rouvert |
| effectif maximum, jour, horaire, groupe, notes | repris tels quels |
| disciplines des professeurs | reprises pour les professeurs encore là, sauf les disciplines archivées |

Une saison qui a des cours ou des disciplines de professeurs ne se supprime pas.

## Droits

| Droit | Donne accès à |
|---|---|
| `catalog.view` | la liste des cours, les professeurs de la saison et leurs disciplines, les listes de référence |
| `catalog.edit` | tout modifier, copier une saison |

Le gestionnaire a les deux. La migration les ajoute aussi à un rôle Gestionnaire déjà enregistré. Le contrat d'un professeur reste réservé à `teacher.manage` : le catalogue ne montre que son nom.

## Plus tard, sans changer ce qui précède

- **Inscriptions** : une adhésion de la saison, un cours, l'heure de passage et la durée pour un cours individuel. Faites : un cours complet est grisé dans les dossiers, et le secrétariat inscrit, transfère ou note l'abandon d'un élève en cours de saison ([cours.md](cours.md)). L'heure de passage d'un cours individuel aussi.
- **Import** depuis le logiciel de gestion : les colonnes de l'export des cours (saison, site, discipline, niveau, jour, horaires, salle, professeur, cours complet, groupe) ont toutes leur place ici. La correspondance se fera quand les exports seront disponibles.
- **Professeur remplaçant** : prévu, séance par séance, dans la [gestion des cours](plan.md#gestion-des-cours). **Accompagnateur** d'un cours : non repris pour l'instant.
- **Un professeur ne voit que ses élèves** : quand les inscriptions existeront, à partir de `course.teacher_id`.

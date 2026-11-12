# Gestion des cours

Le [catalogue](catalogue.md) décrit les cours d'une saison, et les [inscriptions](inscriptions.md) y placent les élèves au moment de la rentrée. Ce document décrit la vie d'un cours **pendant** la saison : qui le suit, qui arrive, qui part, qui change de groupe.

Le travail se fait par étapes (voir la [feuille de route](plan.md#gestion-des-cours)). Ce document décrit ce qui est en place : **l'étape 1, effectifs et inscriptions en cours de saison**.

## Une inscription dans le temps

Une inscription (`enrollment`) relie l'adhésion d'un élève pour une saison à un cours de cette saison.

| Colonne | Remarque |
|---|---|
| `status` | `active` (inscrit) ou `withdrawn` (parti) |
| `enrolled_on` | date d'entrée dans le cours : la validation du dossier, ou l'arrivée en cours de saison. Obligatoire, aujourd'hui par défaut. |
| `withdrawn_on` | premier jour où l'élève ne suit plus le cours. Jamais avant `enrolled_on`. Le même jour : l'élève n'a jamais suivi le cours. |
| `replaces_id` | après un transfert, l'inscription quittée. Vidé si celle-ci disparaît. |
| `dossier_id` | le dossier qui a créé l'inscription, s'il y en a un |

Un élève **suit un cours à la date `d`** si `enrolled_on ≤ d` et que `withdrawn_on` est vide ou postérieur à `d`. Les séances et les présences s'appuieront sur cette règle.

La paire (adhésion, cours) reste unique : un élève qui revient dans un cours qu'il avait quitté **reprend son inscription** (nouvelle date d'entrée, plus de date de départ), sans en créer une seconde. Seules la date d'entrée et le départ le plus récents sont donc conservés. Le détail reste au journal.

## Ce que fait le secrétariat

Avec le droit `enrollment.edit` (donné au gestionnaire) :

| Action | Effet |
|---|---|
| **Inscrire** un élève, depuis la page du cours ou depuis sa fiche | L'élève doit avoir une adhésion **active** sur la saison du cours. La date doit tomber dans la saison. Un cours complet accepte l'élève avec un avertissement : c'est une décision du secrétariat, pas une erreur. |
| **Changer de cours** (transfert) | L'ancienne inscription se termine à la date choisie, la nouvelle commence le même jour et garde le lien vers l'ancienne. Une seule entrée au journal. |
| **Noter l'abandon** d'un cours | Seul ce cours se termine. L'adhésion reste active, comme les autres cours. Pour un abandon complet, c'est l'adhésion qu'on passe en « Abandon » sur la fiche. |

Ce qui est refusé : un cours et une adhésion de saisons différentes, une adhésion abandonnée, un élève déjà inscrit au cours, une date hors de la saison, un départ avant l'entrée, une inscription déjà terminée.

La validation d'un dossier d'inscription date les inscriptions qu'elle crée du jour de la validation.

## Page d'un cours

**Catalogue → un cours** (droits `catalog.view` et `directory.view` : la liste vient de l'annuaire). Elle montre :

- l'effectif face à la capacité, et si le cours est complet ;
- les élèves inscrits, avec leur âge, leurs responsables et leur date d'entrée, et ceux venus d'un autre cours ;
- les élèves partis en cours de saison, avec leurs dates et le cours rejoint en cas de transfert ;
- **Télécharger la liste** : un fichier CSV (nom, prénom, naissance, date d'entrée, responsables, téléphone, email), jamais mis en cache ;
- **Écrire aux familles** : un lien `mailto:` avec les adresses des responsables en copie cachée (l'élève lui-même s'il n'a pas de responsable), sans doublon. L'appli n'envoie rien : le message part de la messagerie de la personne.

La fiche d'un élève montre aussi, pour chaque adhésion, ses cours, avec un lien pour changer de cours ou en ajouter un.

## Cotisation « à revoir »

Un changement d'inscription peut changer la cotisation, mais l'appli **ne la recalcule pas seule** : le calcul reste proposé puis validé par une personne ([tarifs](tarifs.md)).

`assessment.review_reasons` reçoit un motif lisible (« Alice Martin est passé(e) de Flûte à Piano le 02/11/2026 ») quand :

- un élève est inscrit, transféré ou quitte un cours ;
- un dossier validé ajoute une inscription à un foyer dont la cotisation est déjà calculée ;
- une adhésion est ajoutée sur la fiche, ou change de statut.

Le motif va à la cotisation de la saison de **chaque foyer** de l'élève : un enfant de parents séparés compte dans les deux. Rien n'est noté pour un foyer dont la cotisation n'est pas encore calculée. Un même motif n'est pas répété, et seuls les 20 derniers sont gardés : le journal reste la trace complète.

Une cotisation validée **garde son statut** et porte le signal. Le signal tombe :

- quand la cotisation est recalculée (après réouverture, si elle était validée) ;
- ou quand un gestionnaire indique qu'il n'y a **rien à changer** (droit `fee.assess`), ce qui est noté au journal.

Dans **Tarifs → Cotisations**, le filtre **À revoir** liste les foyers concernés.

## Droits

| Droit | Donne accès à |
|---|---|
| `catalog.view` et `directory.view` | la page d'un cours et la liste de ses élèves |
| `enrollment.edit` | inscrire, transférer, noter un abandon |
| `fee.assess` | indiquer qu'une cotisation à revoir n'a rien à changer |

La migration `a0000007effectifs` ajoute `enrollment.edit` à un rôle Gestionnaire déjà enregistré. Les inscriptions existantes y prennent la date de leur enregistrement, sans dépasser leur date de départ.

## Tests

`tests/test_courses.py` couvre :

- l'inscription, le transfert et l'abandon, avec chaque refus ;
- la reprise d'une inscription quittée ;
- le cours complet ;
- la liste des élèves et les adresses sans doublon ;
- le fichier CSV ;
- le signal « à revoir » : chaque foyer, cotisation validée, sans doublon, limite, recalcul, rien à changer, dossier validé, adhésion modifiée ;
- les écrans et leurs droits.

`tests/test_migrations.py` vérifie la reprise des dates et les contraintes sur une vraie base, et `tests/test_sweep.py` les droits de chaque nouvelle route.

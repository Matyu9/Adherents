# Gestion des cours

Le [catalogue](catalogue.md) décrit les cours d'une saison, et les [inscriptions](inscriptions.md) y placent les élèves au moment de la rentrée. Ce document décrit la vie d'un cours **pendant** la saison : qui le suit, qui arrive, qui part, qui change de groupe.

Le travail se fait par étapes (voir la [feuille de route](plan.md#gestion-des-cours)). Ce document décrit ce qui est en place, étape par étape.

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

## Cours individuels

Un cours est **individuel** par une case cochée sur le cours (`course.individual`), pas parce qu'il n'a pas d'effectif maximum : un atelier sans limite reste collectif. La migration `a0000008individuel` ne coche la case d'aucun cours existant. La case est recopiée avec le catalogue.

Le créneau du cours (17 h à 19 h) est celui du professeur. Chaque élève y a :

| Colonne | Remarque |
|---|---|
| `slot_starts_at` | heure de passage, facultative |
| `slot_minutes` | durée en minutes (1 à 240). Elle peut être connue avant l'heure ; une heure sans durée est refusée. |

Sur la page du cours, les élèves sont rangés par heure de passage, ceux sans heure à la fin, et les **plages libres** du créneau sont indiquées. Le secrétariat (`enrollment.edit`) saisit l'heure et la durée sur la ligne de l'élève. Trois cas sont **signalés sans être refusés**, comme les chevauchements du catalogue :

- un passage qui déborde du créneau du cours ;
- deux élèves qui se chevauchent (un passage qui finit à 17 h 30 et un qui commence à 17 h 30 ne se gênent pas) ;
- un élève qui a un autre cours le même jour au même moment.

Le professeur voit l'heure de passage dans **Mes élèves**, rangée dans l'ordre de la journée, et la famille dans **Mon espace**, à la place de l'horaire du créneau.

**Plus tard :** la durée comme condition des règles de tarif (formule 30 ou 45 minutes).

## Liste d'attente

Table `waitlist_entry` : un cours, une adhésion de la même saison, une **position** explicite, un statut (`waiting` en attente, `enrolled` inscrit, `removed` retiré), la date d'entrée et de clôture, et le dossier qui l'a demandée, s'il y en a un. Une entrée par élève et par cours : revenir en attente rouvre l'entrée, en fin de liste. Rien n'est supprimé par l'appli. Un cours qui a une liste d'attente ne se supprime pas.

- **Depuis le dossier :** sous un cours complet, la famille coche « M'inscrire sur la liste d'attente de ce cours ». L'entrée est créée à la validation, quand l'adhésion existe.
- **Depuis la page du cours :** le secrétariat ajoute un élève de la saison, change l'ordre (↑ ↓, les rangs sont renumérotés), retire une personne ou l'inscrit à une date choisie.
- **Une place se libère** (abandon, transfert, effectif maximum relevé, cours rouvert) : les comptes qui ont `enrollment.edit` reçoivent une notification avec le premier de la liste. **Rien n'est inscrit d'office.** Le même effectif ne prévient qu'une fois par jour.
- Inscrire directement un élève en attente clôt son entrée.
- La famille voit dans **Mon espace** les cours où ses enfants attendent.
- La fusion de deux fiches garde les inscriptions (avec leurs dates et heures de passage) et les listes d'attente de la fiche abandonnée.

## Calendrier et séances

**Enseignement → Calendrier** (droit `lesson.manage`).

| Table / colonne | Rôle |
|---|---|
| `season.lessons_from`, `lessons_until` | reprise et fin des cours ; vides : les dates de la saison |
| `season_break` | périodes sans cours (nom, premier et dernier jour inclus). L'association les saisit : aucune liste de vacances ni de jours fériés n'est fournie. Un jour férié est une période d'un jour. |
| `lesson` | une séance : cours, date, horaire, salle, remplaçant, statut (`planned`, `cancelled`), motif |

Colonnes de suivi d'une séance :

- `scheduled_on` : le jour du créneau régulier dont elle vient, unique par cours ; vide pour une séance ajoutée (rattrapage).
- `edited` : modifiée à la main, donc laissée telle quelle par la génération.
- `previous` : l'état d'avant la dernière modification, pour revenir en arrière.
- `revision` : augmente à chaque changement ; c'est le `SEQUENCE` des agendas.
- `changed_by`, `changed_at` : qui a fait le dernier changement, et quand.

**Génération.** Pour chaque cours qui a un jour et un horaire, une séance par semaine entre reprise et fin des cours, hors périodes sans cours. L'écran montre d'abord un aperçu (à créer, à recaler, à retirer). On peut la relancer après avoir changé un créneau, ajouté une période ou décalé les dates :

- les séances qui manquent sont créées ;
- les séances **à venir** qui n'ont pas été modifiées à la main sont recalées sur le créneau du cours (jour, horaire, salle) ;
- celles qui ne tombent plus sur un jour de cours sont retirées (annulées si l'appel a été fait) ;
- les séances passées et celles modifiées à la main ne bougent pas.

Un élève **suit une séance** s'il est inscrit au cours ce jour-là (date d'entrée et de départ). Dans un cours individuel, son heure de passage suit la séance : déplacée d'une heure, la séance décale son passage d'autant.

## Séances modifiées par le professeur

Le professeur du cours (pas le remplaçant) déplace, annule ou ajoute une séance depuis **Mes séances**, sans validation :

- **Modifier la séance** : date, horaire et salle. L'écran montre les salles libres à ce créneau (d'après les autres séances, et l'agenda de la salle s'il est relié), puis signale sans refuser une salle prise, un professeur déjà en cours, un élève qui a un autre cours, un jour pendant une période sans cours.
- **Annuler** : le motif est obligatoire, il est transmis aux familles.
- **Ajouter** une séance de rattrapage.

Le changement s'applique tout de suite :

- les familles des élèves de la séance reçoivent une notification (et l'email qui va avec), ou un email direct si elles n'ont pas de compte ;
- les comptes qui ont `lesson.manage` reçoivent une notification avec le lien vers les séances du cours ;
- tout est noté au journal.

**Ce que le professeur ne peut pas faire :** modifier une séance déjà commencée, déplacer ou ajouter une séance dans le passé, changer le créneau habituel du cours (secrétariat, catalogue). Le **délai** avant une séance en deçà duquel il ne peut plus la modifier est un réglage de l'association (`lesson_teacher_delay_hours`), **sans valeur par défaut** : tant qu'il n'est pas fixé, il n'y a pas de limite.

**Le secrétariat** fait tout cela sans délai, sur toutes les séances, et en plus :

- **revient en arrière** sur la dernière modification d'une séance : l'état d'avant revient, une séance ajoutée disparaît, et les familles sont prévenues ;
- nomme un **remplaçant** pour une séance, qui reçoit une notification, voit la séance dans Mes séances et pourra faire l'appel, mais ne la déplace pas.

## Plannings

**Enseignement → Planning** (droit `catalog.view`) : toutes les séances, ou celles d'une salle, d'un professeur ou d'un site, en trois affichages :

- **Semaine** : une grille horaire, un quart d'heure par ligne, de la première à la dernière séance (9 h - 18 h au moins), le dimanche seulement s'il a des séances. Deux séances qui se chevauchent le même jour se partagent la largeur. Le jour courant est marqué, avec un trait à l'heure qu'il est.
- **Mois** : un calendrier du lundi au dimanche, quatre séances par jour au plus (« + 2 autres » mène à la semaine).
- **Semaine type** : les créneaux des cours de la saison, sans dates.

Une séance **passée** reste affichée, grisée ; une séance **annulée** est barrée et hachurée, avec son motif ; une séance modifiée est marquée. Chaque séance mène à la liste des séances de son cours (pour qui peut la voir).

**Couleur d'un cours** (`course.color`, migration `a0000013couleurs`) : une couleur choisie dans le formulaire du cours parmi dix, sinon tirée de la discipline (ses cours se ressemblent). Elle est recopiée avec le catalogue. Les couleurs sont des classes CSS définies pour les thèmes clair et sombre ; la grille est placée par les classes de `static/css/planning-grid.css` (fichier généré), car la politique de sécurité du contenu interdit les styles en ligne.

**Mon planning** (menu, pour toute personne reliée à sa fiche) : les mêmes vues semaine et mois, avec les séances qu'on donne ou remplace et celles des élèves dont on est responsable, avec leur prénom et leur heure de passage. Le prénom n'apparaît que dans l'appli, jamais dans l'agenda à s'abonner.

## Agenda à s'abonner

Chaque compte peut créer une adresse secrète (`/seances/agenda/<jeton>.ics`), depuis **Mon compte**, **Mon espace** ou **Mes séances**, et l'ajouter à Google Agenda, Apple, Outlook… :

- un professeur y trouve ses séances et ses remplacements ;
- un responsable, les séances des enfants de ses foyers (et les siennes), avec l'heure de passage d'un cours individuel ;
- les séances du mois passé et de l'année à venir. Une séance annulée reste, marquée `STATUS:CANCELLED` ; chaque changement augmente son `SEQUENCE`.

**Aucun nom d'élève** dans les événements : seulement le cours, la salle, l'horaire et le motif d'un changement. L'adresse peut circuler, et le calendrier qui la lit en garde une copie.

Le jeton est montré **une seule fois**, à sa création ; seule son empreinte (SHA-256) est gardée, dans `account.calendar_token`. En créer un nouveau coupe l'ancienne adresse ; on peut aussi la supprimer. Un compte coupé n'a plus d'agenda. Les heures sont écrites en UTC, calculées depuis le fuseau `TIMEZONE` (`Europe/Paris` par défaut) : les changements d'heure tombent juste.

Les calendriers relisent ces adresses toutes les quelques heures (jusqu'à 24 h chez Google) : c'est pourquoi un changement de séance passe aussi par notification et email.

## Agendas des salles (Google Workspace)

Facultatif : sans `GOOGLE_SERVICE_ACCOUNT_FILE`, rien de cette partie n'est actif et l'appli se comporte comme avant. Le code est dans `app/services/room_calendars.py`, et ne parle à Google que par `app/services/google_workspace.py` (un client de quelques fonctions, sans dépendance de plus). Un autre serveur d'agenda (Microsoft 365, CalDAV) pourrait remplacer ce second module.

| Colonne / table | Rôle |
|---|---|
| `room.calendar_id` | adresse de l'agenda de la salle (`…@resource.calendar.google.com`), unique |
| `room.capacity` | places de la salle ; un cours dont l'effectif maximum les dépasse est signalé |
| `site.external_id` | identifiant du bâtiment dans l'organisation, unique |
| `room_calendar_job` | envois en attente : agenda, identifiant de l'événement, `put` ou `delete`, contenu, essais, prochaine tentative, dernière erreur |

**Lire l'occupation.** Avant de déplacer ou d'ajouter une séance, l'appli lit l'agenda de chaque salle reliée sur le créneau. Une réunion de l'organisation apparaît comme salle « réservée 14:00-15:30 », dans la liste des salles et dans les avertissements. Ne comptent pas : les événements posés par l'appli (déjà connus comme séances), ceux marqués « disponible », ceux annulés. Si Google ne répond pas ou si l'agenda n'est pas partagé, la salle est marquée « agenda illisible » : rien n'est bloqué, on vérifie à la main.

**Réserver.** Chaque séance prévue dans une salle reliée devient un événement de son agenda :

- identifiant `adherents<id de la séance>`, choisi par l'appli : renvoyer remplace l'événement, sans doublon ;
- titre : le cours et le professeur (ou le remplaçant). **Jamais de nom d'élève** : toute l'organisation voit ces agendas ;
- une séance déplacée vers une autre salle quitte l'agenda de l'ancienne ; annulée ou supprimée, son événement est retiré ;
- l'appli écrit directement dans l'agenda de la salle, sans l'inviter : il n'y a donc pas de refus automatique de la salle en cas de conflit, d'où la lecture de l'occupation avant chaque enregistrement.

Les envois passent par la file `room_calendar_job`, envoyée par `flask run-tasks` (service scheduler). Un seul envoi en attente par événement : le dernier état l'emporte. En cas d'échec, l'envoi est réessayé de plus en plus tard (jusqu'à 6 h d'écart), puis abandonné après 12 essais, avec une ligne au journal.

**Salles de l'organisation** (Catalogue → Disciplines, niveaux et lieux → **Salles de l'organisation**, droit `catalog.edit`). L'écran lit les bâtiments et les salles de l'organisation et montre un aperçu, sans rien écrire :

- **nouvelles salles** à créer (le site est celui du bâtiment, créé au besoin et relié par son identifiant) ;
- **salles du catalogue à relier**, quand le nom est le même (sans tenir compte des accents ni de la casse) ;
- **salles du catalogue sans agenda**, à relier à la main ou à laisser hors de l'organisation (une salle municipale) ;
- **renommées** dans l'organisation ;
- **disparues** de l'organisation, à archiver si on le souhaite : rien n'est supprimé ;
- **agenda non partagé** avec le compte de service : à partager, sinon l'appli ne peut ni lire ni réserver.

Le bouton **Envoyer les séances à venir** met en file toutes les séances à venir des salles reliées : à faire à la mise en route, et après avoir relié une salle. L'agenda d'une salle peut aussi se saisir à la main dans sa fiche.

**Mise en place côté Google** : voir [installation.md](installation.md#agendas-des-salles-google-workspace).

## Présences

Table `attendance` : une séance, une inscription, un statut (`present`, `absent`, `excused`), qui l'a noté et quand. Une ligne par élève et par séance. **Pas de motif en texte libre** : il révélerait vite des données de santé. « Excusé » dit seulement que l'absence était prévenue.

**L'appel** (`/seances/<id>/appel`, depuis **Mes séances** ou les séances d'un cours) :

- la feuille liste les élèves qui suivent le cours **le jour de la séance** : un élève arrivé ou parti en cours de saison n'y figure qu'à ses dates ;
- un bouton par statut et par élève, « présent » coché d'office, sans JavaScript, pensé pour le téléphone ;
- le professeur du cours et le remplaçant de la séance font l'appel **à partir du jour de la séance**, et le corrigent pendant **7 jours** ; ensuite, la feuille est en lecture seule pour eux, et seul le secrétariat (`lesson.manage`) la corrige ;
- pas d'appel sur une séance annulée ; une séance dont l'appel est fait n'est jamais supprimée par la génération : elle est annulée.

**Absences répétées** (Enseignement → **Absences**, `lesson.manage`) : les élèves qui ont au moins N absences non excusées sur la saison, par cours. Le seuil se règle dans le calendrier et n'a **pas de valeur par défaut** : sans seuil, personne n'est signalé.

**Les familles** voient dans **Mon espace**, pour chaque cours de leurs enfants, le nombre de présences et la date de chaque absence (absent ou excusé). Ce réglage (`attendance_family_visible`) est **actif par défaut** ; l'association peut le couper dans le calendrier.

**Conservation** : l'anonymisation d'une fiche efface ses présences.

## Droits

| Droit | Donne accès à |
|---|---|
| `catalog.view` et `directory.view` | la page d'un cours et la liste de ses élèves |
| `enrollment.edit` | inscrire, transférer, noter un abandon, fixer l'heure de passage, gérer la liste d'attente ; reçoit les avis de place libre |
| `fee.assess` | indiquer qu'une cotisation à revoir n'a rien à changer |
| `lesson.manage` | calendrier de la saison, génération, séances de tous les cours, retour en arrière, remplaçants, appel sans délai, absences répétées, réglages des présences ; reçoit les avis des changements faits par les professeurs |
| `catalog.view` | les plannings |
| `catalog.edit` | les salles de l'organisation, l'envoi des séances à venir aux agendas des salles |
| (professeur du cours) | ses séances : déplacer, annuler, ajouter ; l'appel pendant 7 jours |
| (remplaçant d'une séance) | l'appel de cette séance pendant 7 jours |
| (tout compte) | son agenda à s'abonner |

Les migrations `a0000007effectifs` et `a0000010seances` ajoutent `enrollment.edit` et `lesson.manage` à un rôle Gestionnaire déjà enregistré. Les inscriptions existantes y prennent la date de leur enregistrement, sans dépasser leur date de départ.

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

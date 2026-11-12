# Tarifs : éléments, règles et calcul des cotisations

Le moteur est dans `app/services/pricing.py`, les services dans `app/services/fees.py`, les écrans dans le menu **Tarifs**. Reste à rejouer une saison importée d'un logiciel de gestion, pour comparer les montants.

L'idée : **partir d'une grille vide** et laisser le gestionnaire construire les tarifs de la saison lui-même, avec des éléments et des règles. Rien n'est figé dans le code : ni la dégressivité, ni le quotient familial, ni les variantes « famille ».

## Les éléments

Ce qu'on facture : adhésion, formule 30 min, chorale, location… Chacun a un libellé, un **prix de base** (en centimes), une catégorie libre (« Formules », « Pratiques collectives »…) et une régie : l'entité qui encaisse, quand l'association en a plusieurs (« École », « Orchestre »…).

## Les règles

Une règle modifie les prix. Elle se compose de quatre parties.

**Conditions.** Ce sont des groupes reliés par **OU** ; dans un groupe, les conditions sont reliées par **ET**. Une règle sans condition s'applique toujours.

```
  (tranche QF parmi A)  ET  (résident = oui)
OU
  (étiquettes du foyer : a l'une de « boursier »)
```

Critères disponibles :

| Niveau | Critère | Type |
|---|---|---|
| Foyer | tranche de quotient familial | choix |
| Foyer | résident de la commune | oui / non |
| Foyer | étiquettes | étiquettes |
| Foyer | nombre d'élèves | nombre |
| Foyer | nombre d'éléments (tous, un élément ou une catégorie) | nombre |
| Élève | âge, saisons déjà suivies, rang dans le foyer | nombre |
| Élève | adulte | oui / non |
| Élève | étiquettes, disciplines | étiquettes |
| Élève | nombre d'éléments (tous, un élément ou une catégorie) | nombre |
| Élément | élément, catégorie | choix |
| Élément | rang dans sa catégorie, chez l'élève ou dans le foyer | nombre |

Les **étiquettes** se posent à la main sur un foyer ou un élève (« personnel », « boursier »…). Elles couvrent les cas qu'on n'a pas prévus.

Un **fait inconnu** (âge non saisi, tranche non renseignée) ne satisfait aucune condition, sauf « hors de » et « n'a aucune de ».

**Cible.** Une règle qui modifie un prix vise des éléments ou des catégories, avec des exclusions possibles. Sans cible, elle vise tout. Une exclusion l'emporte toujours sur la cible.

**Effet.**

| Effet | Exemple |
|---|---|
| Pourcentage | −10 % ; +20 % hors commune. Calculé sur le montant à ce stade, ou sur le prix de base |
| Montant | −50 € |
| Prix fixe | 300 € en tranche A |
| Offert | pratique collective offerte avec une formule |
| Remplacer par un autre élément | formule 30 min → formule 30 min « famille » |
| Ajouter un élément | adhésion pour chaque élève, frais de dossier une fois par foyer |
| Pourcentage ou montant sur le total du foyer | −5 % à partir de trois élèves |

Une règle qui ajoute un élément pour le foyer, ou qui porte sur le total, ne consulte que des critères du foyer. Une règle qui ajoute un élément pour chaque élève ne consulte pas les critères d'élément.

**Groupe exclusif** (facultatif). Une ligne déjà touchée par une règle d'un groupe ne l'est plus par les règles suivantes du même groupe. Par exemple, « QF A » et « Boursier » dans le groupe « social » ne se cumulent pas : la première dans l'ordre l'emporte.

## L'ordre

Les règles s'appliquent **de haut en bas**, chacune sur le résultat des précédentes. On les réordonne à la main. L'ordre change le résultat, et c'est voulu :

- −10 % puis −50 € sur 420 € donnent 328 € ; −50 € puis −10 % donnent 333 € ;
- un élément ajouté ne subit que les règles placées **après** l'ajout ;
- une règle sur le total se calcule sur le total **à sa place dans la liste** : on la met en général à la fin.

## Les rangs

**Le rang de l'élève** sert à la dégressivité. Le classement est un réglage de la saison :

- du plus cher au moins cher (le plus cher paie plein tarif) ;
- du moins cher au plus cher ;
- du plus âgé au plus jeune ;
- du plus jeune au plus âgé.

Le classement par prix peut ne compter que certaines catégories, par exemple les seules formules. Les égalités sont départagées par l'ordre d'inscription, et un âge inconnu passe en dernier. Seuls les élèves qui prennent au moins un élément sont classés.

**Le rang d'un élément dans sa catégorie** sert pour un « 2ᵉ instrument » chez l'élève, ou une « 3ᵉ pratique collective du foyer ». Les éléments sont classés par prix de base décroissant.

Les rangs sont **figés au début de chaque règle** : ce qu'une règle change ne modifie pas les rangs qu'elle consulte. Ils reflètent en revanche les règles précédentes, par exemple un élément ajouté ou remplacé.

## L'arrondi

C'est un réglage de la saison : au centime, au décime le plus proche ou à l'euro le plus proche. Il s'applique à chaque ligne ou au total du foyer, toujours en dernier. Les pourcentages sont arrondis au centime le plus proche, et un demi-centime arrondit au-dessus.

## La trace

Chaque ligne garde les règles appliquées, avec le montant avant et après chacune, par exemple :

```
Formule 30 min : 420,00 → QF A (−30 %) 294,00 → Famille (−10 %) 264,60 → Arrondi 264,60
```

Un montant ne descend jamais sous zéro.

## Exemple : la dégressivité

```
1. Adhésion             Ajouter « Adhésion » pour chaque élève
2. QF A        [social] Si tranche QF parmi A           → Formules −30 %
3. Boursier    [social] Si étiquette foyer « boursier » → Formules −50 %
4. Famille 3+  [famille] Si rang de l'élève ≥ 3         → Formules −20 %
5. Famille 2   [famille] Si rang de l'élève ≥ 2         → Formules −10 %
6. Collectif offert     Si l'élève a ≥ 1 élément « Formules » → Pratiques collectives : offert
7. Hors commune         Si résident = non → tout sauf Adhésion +20 %
```

## Sécurité

Les règles sont des données (JSON), vérifiées par `validate_rule` avant tout enregistrement : critères, opérateurs et effets viennent de listes fermées. Aucune formule n'est saisie ni exécutée.

## Tables

| Table | Rôle |
|---|---|
| `regie` | Entité qui encaisse (« École », « Orchestre »…) ; elle s'archive au lieu de se supprimer |
| `fee_item` | Élément d'une saison : libellé (unique dans la saison), prix de base en centimes, catégorie, régie, code du logiciel de gestion pour information |
| `fee_rule` | Règle d'une saison : position, nom, conditions, cible et effet (JSON vérifié par le moteur), groupe exclusif, active ou en brouillon |
| `fee_season` | Réglages de la saison : moteur (classement, arrondi), tranches de QF avec leurs plafonds, tranche par défaut, communes résidentes |
| `household_season` | Situation d'un foyer pour une saison : tranche saisie, quotient, justificatif, résidence forcée, étiquettes |
| `fee_pick` | Élément pris en dehors des cours (location…), ajouté par un gestionnaire pour un élève du foyer |
| `assessment` | Cotisation d'un foyer pour une saison : proposée ou validée, total calculé, avertissements |
| `assessment_line` | Ligne : élément d'un élève, règle sur le total ou arrondi ; montant calculé, montant retenu, trace des règles, correction (motif, auteur, date) |

S'y ajoutent `course.fee_item_id`, l'élément facturé à chaque élève du cours, et `membership.fee_tags`, les étiquettes de l'élève pour la saison.

Règles portées par la base :

- un élément, une régie, un foyer ou une saison qui ont servi ne se suppriment pas ;
- une cotisation par foyer et par saison ;
- une correction a toujours un motif et une date ;
- une fiche supprimée laisse ses lignes de cotisation, avec son nom.

## Ce que le foyer prend

- **Élèves** : les membres du foyer qui ont une adhésion active dans la saison. Leur âge est pris au début de la saison. Un membre sans date de naissance est adulte s'il est responsable.
- **Éléments** : celui de chaque cours suivi (les inscriptions abandonnées ne comptent pas), puis ceux pris à part.
- **Tranche** : celle saisie ; sinon celle du quotient (plafond exclu : un quotient de 600 avec A < 600 est en B) ; sinon la tranche par défaut.
- **Résidence** : celle saisie ; sinon la commune du foyer comparée à la liste de la saison, sans tenir compte des accents ni des majuscules.
- **Avertissements**, gardés avec la cotisation : un cours qui ne facture rien, un élève présent dans plusieurs foyers (compté dans chacun), un élément pris pour quelqu'un qui n'est pas élève, une tranche inconnue.

## Cotisations

1. **Calcul** d'un foyer ou de toute la saison : la cotisation est enregistrée comme **proposée**. La recalculer remplace ses lignes. Si elle a des corrections, le recalcul est refusé, sauf demande expresse.
2. **Correction** d'une ligne : nouveau montant et motif obligatoires, inscrits au journal. Elle peut être annulée.
3. **Validation** : la cotisation ne bouge plus. Pour la corriger, on la **rouvre** avec un motif.
4. **Écart** : si les règles ou les inscriptions changent après coup, un nouveau calcul est comparé au total enregistré, sans rien modifier.

## Effet d'une règle

Avant d'activer une règle en brouillon (ou d'en désactiver une), on voit, foyer par foyer, le total avant et après, à la place de la règle dans l'ordre. Le simulateur calcule un foyer fictif avec les règles actives et, au choix, des règles en brouillon.

## Copie d'une saison

Elle se fait sur une saison qui n'a encore ni élément ni règle, et recopie :

- les éléments ;
- les règles, avec leur ordre et leur état, en renumérotant les éléments qu'elles citent ;
- les réglages.

Les cours de la nouvelle saison qui ne facturent rien reprennent l'élément du cours équivalent : même discipline, même niveau, même groupe.

## Fusions de l'annuaire

Deux foyers fusionnés regroupent leurs éléments pris à part, leurs cotisations et leurs situations. Si les deux ont une situation pour la même saison, celle du foyer gardé reste. La fusion est refusée s'ils ont chacun une cotisation pour la même saison.

## Droits

| Droit | Pour | Gestionnaire |
|---|---|---|
| `fee.view` | consulter les tarifs et les cotisations | oui |
| `fee.edit` | éléments, règles, réglages, copie de saison | oui |
| `fee.assess` | calculer, corriger, valider | oui |
| `fee.situation` | voir et modifier la situation des foyers (quotient familial) | non : à donner au trésorier et à la direction |

## Écrans

Menu **Enseignement → Tarifs**, saison par saison :

| Onglet | Contenu | Droit |
|---|---|---|
| Éléments | grille de la saison : libellé, catégorie, régie, prix de base, nombre de cours qui le facturent ; ajout, modification, suppression d'un élément inutilisé ; copie d'une saison | `fee.view`, `fee.edit` pour modifier |
| Règles | règles dans l'ordre d'application, en phrases (« Si … ou … Alors … ») ; monter, descendre, activer, remettre en brouillon, supprimer ; effet de l'activation ou de la désactivation, foyer par foyer | `fee.view`, `fee.edit` pour modifier |
| Cours | élément facturé par chaque cours de la saison | `fee.view`, `fee.edit` pour modifier |
| Simulateur | foyer fictif (tranche, résidence, étiquettes, élèves et leurs éléments), avec ou sans les règles en brouillon cochées | `fee.view` |
| Cotisations | foyers de la saison, filtrés par statut ; calcul de toutes les cotisations | `fee.view`, `fee.assess` pour calculer |
| Réglages | classement des élèves, arrondi, tranches de QF, tranche par défaut, communes résidentes, régies | `fee.edit` |

**Cotisation d'un foyer** (aussi depuis la fiche du foyer, bouton « Cotisation ») : situation résumée (réservée à `fee.situation`), avertissements, cotisation enregistrée avec la trace des règles de chaque ligne, correction d'une ligne (montant et motif), validation, réouverture avec un motif, écart avec un nouveau calcul, éléments pris en dehors des cours. Sans cotisation enregistrée, le calcul actuel est affiché, avec un bouton pour l'enregistrer.

**Situation du foyer** (`fee.situation`) : quotient, tranche forcée, justificatif, résidence forcée, étiquettes du foyer et de chaque élève.

**Formulaire d'une règle.** Il marche sans JavaScript : « + ET une condition », « + OU un autre bloc » et « Retirer » renvoient le formulaire sans rien enregistrer, et la touche Entrée enregistre. Les valeurs se tapent en clair :

- un nombre : « 3 » ;
- deux nombres pour « entre » : « 10 et 14 » ;
- « oui » ou « non » ;
- une liste séparée par des virgules : « A, B ». Pour les éléments, on tape leurs libellés, sans tenir compte des accents ni des majuscules.

Les montants s'écrivent en euros (« -12,50 »), les pourcentages tels quels (« -10 »). Une règle nouvelle est enregistrée en brouillon, sauf si on coche « L'activer tout de suite ». Avec JavaScript, seuls les champs utiles à l'effet choisi sont affichés.

## Reste à faire

- Rejeu d'une saison importée et comparaison avec les montants appelés par le logiciel de gestion (critère de réussite du plan), quand les exports seront là.
- Affichage des cotisations dans l'espace adhérent, puis règlements.

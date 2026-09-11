# SPEC_RULES_v3 — Règles normatives actives LSS Tracking

Version : v3 canonique 2026-09-04
Statut : Source de vérité unique pour le moteur de calcul et les règles de présentation
Priorité : Ce document prime sur les documents historiques, sur la référence antérieure et sur les addenda de suivi.

> Ce fichier est la seule source de vérité opérationnelle.
> Il remplace les anciennes règles historiques et les évolutions cumulées.
> Les fichiers de contexte historiques sont archivés dans [CHANGELOG_REGLES.md](CHANGELOG_REGLES.md).

---

## 1. Paramètres et seuils actifs

| Paramètre | Valeur active | Rôle | Applicabilité |
|---|---:|---|---|
| `SEUIL_DISTANCE_MIN_TRAJET_KM` | 0,3 km | Distance minimale pour qu’un trajet soit valide | Trajets / validation |
| `SEUIL_PAUSE_COUPURE_TCC` | 30 min | Pause coupante : reset de la session TCC | TCC / pauses |
| `SEUIL_AFFICHAGE_PAUSE_MIN` | 30 min | Seules les pauses ≥ 30 min sont affichées | Vue plateforme |
| `HEURE_PRE_CONSOLIDATION` | 23:59:59 | Heure de clôture de journée | Consolidation |
| `TTJ_MAX` | 12:00 | Borne maximale de TTJ | Calcul TTJ |
| `TCC_PREALERT_THRESHOLD` | 4:00 h | Pré-alerte si TCC approche le seuil | Alerte |
| `SEUIL_VITESSE_ARRET` | obsolète dans le calcul historique | Le calcul d’arrêt repose sur les données des portails / mini-manœuvres | Arrêts |

---

## 2. Structure des règles

Chaque règle suit ce format strict :
- [ID-RÈGLE] : identifiant unique
- Statut : Active / Superseded / Archived
- Définition / Formule : règle normative
- Conditions : conditions d’application
- Exceptions : cas particuliers, exclusions, masquages

---

## 3. Règles actives

### [R-01] TCJ — déduction de tous les arrêts
- Statut : Active
- Définition / Formule :
  `TCJ = (maintenant − heure_depart) − Σ (durée de TOUS les arrêts, quelle que soit leur durée)`
- Conditions :
  - Les arrêts sont issus des données des portails MZoneX / CamtrackPro.
  - Les mini-manœuvres < 0,3 km sont traitées comme des arrêts dans le calcul.
  - Un trajet est valide si sa distance est ≥ 0,3 km.
  - Pour un trajet terminé / consolidé, `maintenant` = `heure_de_fin`.
- Exceptions :
  - Les manœuvres < 0,3 km ne produisent ni horaire comptabilisé ni trajet comptabilisé dans les compteurs.
  - Les arrêts courts (< 30 min) restent présents dans le calcul, mais peuvent être masqués à l’affichage.

### [R-02] Affichage / stockage — découplage strict
- Statut : Active
- Définition / Formule :
  - L’affichage montre les trajets valides + pauses ≥ 30 min.
  - Les données de base ne sont jamais effacées.
- Conditions :
  - Le stockage conserve toutes les heures réelles (audit / trace).
  - La couche d’affichage filtre les éléments non pertinents.
- Exceptions :
  - Les pauses courtes (< 30 min) sont masquées à l’écran.
  - Les trajets invalides ne sont pas supprimés de la base, mais ils ne sont pas rendus comme trajets actifs.

### [R-03] TTJ — temps écoulé depuis le départ
- Statut : Active
- Définition / Formule :
  `TTJ = TCJ + Σ tous arrêts = maintenant − départ`
- Conditions :
  - La borne `TTJ_MAX` = 12:00 reste appliquée.
  - `TTJ` représente le temps total écoulé depuis le premier départ valide.
- Exceptions :
  - La relation est conservée même si certaines pauses ou manœuvres sont masquées à l’écran.

### [R-04] Consolidation — 23:59:59 + split à minuit
- Statut : Active
- Définition / Formule :
  - `HEURE_PRE_CONSOLIDATION = 23:59:59`
  - Un trajet encore en cours à 23:59:59 est découpé en deux segments.
- Conditions :
  - `Segment A`: [début → 23:59:59] -> attribué au jour J.
  - `Segment B`: [00:00 → fin] -> attribué au jour J+1.
  - Aucune pause artificielle n’est ajoutée à minuit.
- Exceptions :
  - Les trajets terminés avant 23:59:59 gardent `jour = DATE(heure_debut)`.
  - Le TCC peut traverser minuit sans reset si aucune pause ≥ 30 min ne s’est produite.

### [R-05] Catch-up au démarrage / localhost
- Statut : Active
- Définition / Formule :
  - Au démarrage de l’application, la consolidation traite tous les jours non consolidés.
  - Le traitement est idempotent.
- Conditions :
  - Le système parcourt les jours du plus ancien au plus récent.
  - Un jour déjà consolidé ne doit pas être réécrit.
- Exceptions :
  - Si un jour est réellement absent des deux sources de données, on ne doit pas écrire de données partielles.
  - Journaliser et signaler l’absence de données réelles plutôt que produire un historique incomplet.

### [R-06] Onglet Infraction — lecture seule externe
- Statut : Active
- Définition / Formule :
  - L’onglet Infraction affiche uniquement des infractions déjà filtrées par une source externe.
  - L’application ne doit pas générer d’infractions locales dans cet onglet.
- Conditions :
  - Le connecteur de lecture est externe.
  - Les alertes internes (TCC / TCJ / TTJ / autres événements locaux) ne sont pas remontées dans Infraction.
- Exceptions :
  - Les alertes locales sont envoyées vers la surface d’alerte dédiée.
  - Les lignes déjà importées depuis une source externe restent en base mais ne sont pas créées localement.

### [R-07] Arrêts — source portail + mini-manœuvres
- Statut : Active
- Définition / Formule :
  - Les arrêts sont principalement identifiés via les données des portails.
  - Les mini-manœuvres / mini-trajets (< 0,3 km) sont traitées comme des arrêts dans le calcul.
- Conditions :
  - Avant le premier mouvement valide, une mini-manœuvre est ignorée pour la définition du départ.
  - Le départ réel est la première donnée valide ≥ 0,3 km.
  - Une mini-manœuvre en cours de route est déduite du TCJ.
  - Si sa durée ≥ 30 min, elle est traitée comme pause TCC.
- Exceptions :
  - Elle ne produit pas de nouveau trajet validé.
  - Elle est conservée pour l’audit, mais n’est pas comptabilisée comme trajet réel.

### [R-08] TCC — session continue
- Statut : Active
- Définition / Formule :
  `TCC = temps écoulé depuis le début de la session courante`
- Conditions :
  - Le TCC continue à courir tant qu’aucune pause ≥ 30 min n’interrompt la session.
  - Les arrêts courts (< 30 min) restent inclus dans le TCC.
- Exceptions :
  - Toute pause ≥ 30 min coupe la session et réinitialise le TCC.
  - À minuit, si la conduite continue sans pause ≥ 30 min, le TCC se poursuit dans le segment B.

### [R-09] Règles transversales
- Statut : Active
- Définition / Formule :
  - Le moteur applique la priorité : stockage > calcul > affichage.
  - L’affichage ne supprime pas les données.
  - Les seuils sont centralisés dans les paramètres applicables.
- Conditions :
  - `HEURE_PRE_CONSOLIDATION = 23:59:59`
  - Les données d’audit sont conservées.
  - Les règles d’affichage ne modifient pas les calculs.
- Exceptions :
  - En cas de conflit, ce document prime.

---

## 4. Règles de publication / affichage

### [R-10] Affichage du suivi
- Statut : Active
- Définition / Formule :
  - Afficher tous les trajets valides.
  - Afficher seulement les pauses ≥ 30 min.
- Conditions :
  - Les pauses courtes sont masquées à l’écran.
  - Les heures conservées en base restent exploitables pour les calculs et l’audit.
- Exceptions :
  - Aucun nettoyage destructif des temps de conduite dans les colonnes de calcul.

### [R-11] Historique / archive
- Statut : Active
- Définition / Formule :
  - L’historique reflète les données réelles consolidées.
- Conditions :
  - Les archives ne doivent pas contenir de données partielles si la source est absente.
  - Les données réelles restent disponibles dans les portails.
- Exceptions :
  - Le système s’arrête automatiquement dès que les données sont à jour.

---

## 5. Règles de non-régression

- Les calculs ne doivent pas dépendre du rendu visuel.
- Les manœuvres < 0,3 km ne doivent pas alimenter les compteurs.
- Les pauses courtes ne doivent pas supprimer ou écraser les données réelles.
- Les changements d’affichage ne doivent pas modifier les formules de calcul.
- Les jours non consolidés doivent être rattrapés au démarrage.

---

## 6. Conformité de mise en œuvre

- L’implémentation doit vérifier que le moteur suit exclusivement les règles actives de ce document.
- L’interface doit rendre l’écran à partir des données brutes sans effacer la source de vérité.
- Les données de source externe doivent être importées sans génération locale de faux incidents dans l’onglet Infraction.

---

## 7. Priorité de résolution

1. Ce document.
2. Les fichiers historiques archivés.
3. Les références de développement antérieures.

En cas de conflit, la priorité est toujours ce document.

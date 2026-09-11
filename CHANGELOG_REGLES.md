# CHANGELOG_REGLES — Historique et traçabilité des règles

Version de référence : 2026-09-04
Statut : Archive documentaire — non normatif

> Ce fichier conserve l’historique des décisions, arbitrages et évolutions qui ont précédé la version canonique de [SPEC_RULES_v3.md](SPEC_RULES_v3.md).
> Les règles de calcul et d’affichage actives sont dans la spécification canonique.

---

## 1. Objectif

Ce document archive :
- les anciennes versions de règles,
- les arbitrages d’exploitation,
- les changements de seuils et de logique,
- les décisions de validation métier,
- les références historiques qui ont été remplacées.

Il sert à la traçabilité mais n’est plus une source de vérité opérationnelle.

---

## 2. Évolution historique majeure

### 2.1 Référence consolidée initiale
- Date : 2026-08-06
- Fichier historique : `REFERENCE_IA_REGLES.md`
- Contenu : base métier et logique initiale de calcul.
- Points clés archivés :
  - TCC = segment courant ; reset après pause ≥ 30 min
  - TCJ = temps de conduite réel sur segments
  - TTJ = TCJ + pauses
  - seuil d’arrêt historique porté par la vitesse
  - règles de journée / consolidation à 01:00 / 02:00 selon certaines versions

### 2.2 Évolutions v3
- Date : 2026-08-21
- Fichier historique : `AMELIORATIONS_REGLES.md`
- Contenu : règles finales validées par le PO, notamment AM-1 à AM-6.
- Points clés archivés :
  - AM-1 : TCJ = total écoulé − tous arrêts
  - AM-2 : stockage != affichage ; masque des pauses courtes
  - AM-3 : consolidation 23:59:59 + split minuit
  - AM-4 : catch-up au démarrage pour les jours manqués
  - AM-5 : Infraction = lecture seule externe
  - AM-6 : arrêts des portails + mini-manœuvres

### 2.3 Canonisation finale
- Date : 2026-09-04
- Fichier normatif : [SPEC_RULES_v3.md](SPEC_RULES_v3.md)
- Contenu : fusion active des règles finales, sans narration historique ni mentions obsolètes.

---

## 3. Décisions historiques archivées

### 3.1 Calcul de TCJ
- Ancien comportement : certains arrêts courts pouvaient rester comptés dans la conduite.
- Décision validée : tous les arrêts sont déduits du TCJ, quelle que soit leur durée.
- Statut : remplacé par la règle canonique [R-01].

### 3.2 Affichage de suivi
- Ancien comportement : effacement / masquage de colonnes en base.
- Décision validée : les données sont conservées en base ; la vue masque simplement les éléments non affichés.
- Statut : remplacé par la règle canonique [R-02] et [R-10].

### 3.3 Consolidation de journée
- Ancien comportement : bascule autour de 01:00 / 02:00 selon les versions.
- Décision validée : consolidation à 23:59:59, puis split à minuit.
- Statut : remplacé par la règle canonique [R-04].

### 3.4 Catch-up au démarrage
- Ancien comportement : recalcul seulement sur les jours récents ou selon un déclenchement planifié.
- Décision validée : au boot, traitement complet des jours non consolidés, idempotent.
- Statut : remplacé par la règle canonique [R-05].

### 3.5 Infraction
- Ancien comportement : logique locale de génération / remontée d’infractions dans l’onglet.
- Décision validée : lecture seule depuis une source externe, aucune génération locale dans l’onglet Infraction.
- Statut : remplacé par la règle canonique [R-06].

### 3.6 Arrêts / mini manœuvres
- Ancien comportement : l’arrêt dépendait trop de la vitesse seuil et de logique locale.
- Décision validée : les données des portails sont la source de vérité; les mini-manœuvres < 0,3 km sont déduites du TCJ.
- Statut : remplacé par la règle canonique [R-07].

---

## 4. Paramètres historiques archivés

| Nom historique | Valeur historique | Statut actuel |
|---|---:|---|
| `SEUIL_DISTANCE_MIN_TRAJET_KM` | 0,3 km | actif |
| `SEUIL_PAUSE_COUPURE_TCC` | 30 min | actif |
| `HEURE_PRE_CONSOLIDATION` | 23:59:59 | actif |
| `TTJ_MAX` | 12:00 | actif |
| `SEUIL_VITESSE_ARRET` | historique / obsolète pour le calcul consolidé | remplacé par les données de portail |
| `DUREE_MIN_PAUSE_VALIDE` | anciennes variantes 20 min / 30 min selon la période | archivées |

---

## 5. Versions historiques conservées

- `REFERENCE_IA_REGLES.md` — version de référence consolidée, désormais archivées en historique.
- `AMELIORATIONS_REGLES.md` — évolutions v3, désormais archivées en historique.
- `SPEC_RULES_v3.md` — version active de production / calcul et affichage.

Ces fichiers ne doivent plus être utilisés comme référence normative en production.

---

## 6. Recommandation de gestion documentaire

- Les fichiers historiques peuvent être gardés dans le dépôt comme objets d’audit.
- Les fichiers historiques ne doivent pas être modifiés sur la base de travail normative.
- La production doit se référer uniquement à [SPEC_RULES_v3.md](SPEC_RULES_v3.md).
- L’archivage est recommandé au lieu d’une suppression immédiate, pour préserver la traçabilité métier.

## 7. Fichiers historiques concernés

- [REFERENCE_IA_REGLES.md](REFERENCE_IA_REGLES.md)
- [AMELIORATIONS_REGLES.md](AMELIORATIONS_REGLES.md)
- [SPEC_RULES_v3.md](SPEC_RULES_v3.md)

Les fichiers historiques peuvent ensuite être déplacés dans un dossier archive si le projet veut un dépôt documentaire plus net.

---

## 8. Arbitrages postérieurs à la version canonique (traçabilité)

### 8.1 v1.46 — abrogation de l'arbitrage O4 : session Wialon unique partagée
- Date : 2026-09-04
- Décision : l'exploitant accepte la correction proposée et refusée le 01/09/2026 (O4, REFERENCE_IA_REGLES.md §O4).
- Cause mesurée : chaque nouvelle session API Wialon invalide la précédente ; la collecte ouvrait une session par cycle (~60 s), ce qui dégradait le flux CamtrackPro (constat du 04/09/2026 : 2–15 points/h au lieu de 150–220) et déconnectait le portail web sur la machine serveur.
- Correctif : `WIALON_SESSION_PARTAGEE=1` (backend/.env) — UNE session partagée par le processus, re-login transparent en cas d'expiration, `core/logout` supprimé à la fermeture d'instance. Comportement historique conservé avec la valeur 0.
- Conséquence : le contournement documenté en O4 (« consulter le portail web depuis un autre poste ou après arrêt de la plateforme ») n'est plus nécessaire.

### 8.2 v1.46 — pauses d'affichage (fusion G1 avec badge manquant)
- Date : 2026-09-04
- Constat (4866TBU) : des lignes séparées par des ruptures < 30 min affichaient une case pause VIDE au lieu d'être fusionnées, contre la règle G1 (« < 30 min → UNE ligne, pas de case pause »).
- Cause : la fusion d'affichage exigeait l'égalité des badges chauffeur des DEUX côtés ; un badge manquant (trou d'attribution N1) bloquait la fusion.
- Règle : un badge ABSENT d'un côté ne prouve pas un changement de chauffeur → la fusion G1 s'applique quand même (`fusionner_trajets_affichage`).
- Conséquence : plus jamais de cases pause vides ; les ruptures < 30 min sont toujours fusionnées, les pauses ≥ 30 min seules sont affichées.

### 8.3 v1.46 — remise à ZÉRO du TCC (arbitrage exploitant du 04/09/2026)
- Le TCC est remis à ZÉRO dans deux nouveaux cas :
  1. la durée CUMULÉE des trajets invalides (mini-manœuvres < 0,3 km, rejetées) de la session courante atteint 30 min (`SEUIL_PAUSE_COUPURE_TCC`) — extension d'AM-6 : avant, une manœuvre ne coupait que si ELLE SEULE durait ≥ 30 min ;
  2. une ligne « en cours » (ouverte) alors que le camion ne roule plus (signal GPS > 15 min ou vitesse ≤ 3 km/h) et que le dernier signal date de ≥ 30 min — avant, le chrono courait indéfiniment sur une ligne ouverte d'un camion garé.
- H2 est honoré : en dessous de 30 min d'arrêt, le chrono continue de s'écouler.
- Portée : mesure seule (`recalculer_temps`) — TCJ/TTJ, affichage et archives intacts.

---


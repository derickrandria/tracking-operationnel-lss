# AMÉLIORATIONS_REGLES.md — Évolutions v3 (LSS Tracking)
### Supplément OBLIGATOIRE à REFERENCE_IA_REGLES.md

**Version :** v3 (AMÉLIORATIONS)
**Date :** 2026-08-21
**Statut :** Évolutions validées, à appliquer par l'agent IA développeur
**Primauté :** En cas de conflit entre ce document et `REFERENCE_IA_REGLES.md`, **CE DOCUMENT PRIME**.

---

## 0. Mode d'emploi pour l'agent
1. Charger **`REFERENCE_IA_REGLES.md`** (base) **+ ce document** (évolutions).
2. Pour chaque règle ci-dessous, les sections de la Référence qu'elle **supplante** sont citées → ne plus appliquer l'ancien comportement.
3. En fin de réponse, produire la section **CONFORMITÉ** (PARTIE A.9 de la Référence) en listant les **AM-x** appliqués.
4. Résoudre/confirmer les points d'attention (§8) **avant** de coder (ne pas deviner).

---

## 1. Matrice de supplantation (ancien → nouveau)

| ID | Nouvelle règle (validée) | Supplane dans `REFERENCE_IA_REGLES.md` |
|----|--------------------------|----------------------------------------|
| **AM-1** | TCJ = (maintenant − départ) − Σ de **TOUS** les arrêts (toute durée), trajets valides | §1.2, §1.4 (ligne TCJ), §2.2 (« arrêt < 20 min → ne se déduit pas ») |
| **AM-2** | Affichage = tous trajets valides + pauses ≥ 30 min seulement ; calcul TCC/TCJ/TTJ en arrière-plan **sans effacer** les colonnes | §3.2 (EFFACER provisoire), §4.1, §4.2 (EFFACER / NE PAS ÉCRIRE) |
| **AM-3** | Consolidation à **23:59:59** ; trajet en cours = **SPLIT** à minuit (hier / aujourd'hui) | §8.2, §8.3, §9.1, §9.2, §9.3, §12.1 (`HEURE_PRE_CONSOLIDATION`) |
| **AM-4** | Catch-up consolidation au **démarrage localhost** (tous jours manqués, idempotent) ; calcul s'arrête si à jour ; archive RÉELLE | §9 (déclenchement) — complété |
| **AM-5** | Onglet Infraction = **lecture seule** des infractions pré-filtrées par une autre plateforme ; aucune alerte générée localement | Nouveau (absent de la Référence) |
| **AM-6** | Arrêts = **données portails** (MZoneX/CamtrackPro) ; mouvements < 0,3 km = arrêts (déduits TCJ, pause TCC si ≥ 30 min) ; départ = 1er mouvement valide | §5.1, §7, §12.1 (`SEUIL_VITESSE_ARRET` obsolète), glossaire |

---

## 2. AM-1 — TCJ : TOUS les arrêts déduits (toute durée)

### Règle
```
TCJ = (maintenant − heure_depart) − Σ (durée de TOUS les arrêts, peu importe la durée)
```
- **Arrêt** = fourni par les **portails** (MZoneX/CamtrackPro) — voir AM-6. Tout mouvement < 0,3 km (mini-manœuvre) est aussi traité comme arrêt (déduit du TCJ, pause TCC si ≥ 30 min).
- **Trajets valides** = distance ≥ 0,3 km (§2.1 / §7).
- Pour un trajet **terminé/consolidé**, « maintenant » = `heure_de_fin` (voir §8, R4).

### Ancien → Nouveau
- **Ancien (§1.2 / §2.2)** : TCJ = somme des segments de conduite ; un arrêt < 20 min était ambigu (« ne se déduit pas » → risque de le compter comme conduite).
- **Nouveau** : **TOUS** les arrêts (même 1 min) sont soustraits du temps écoulé. La conduite = temps où le véhicule **ROULE** réellement. Plus d'ambiguïté.

### Sections REFERENCE supplantées
§1.2 (exemple + formulation « ≥ 20 min / ≥ 30 min »), §1.4 (ligne TCJ), §2.2 (ligne « < 20 min → ne se déduit pas »).

### Exemple
```
Départ 05:00 — now 09:00 (écoulé = 4:00)
  Roule      05:00 → 07:00   (2:00)
  Arrêt court 07:00 → 07:15   (0:15, < 20 min)
  Roule      07:15 → 09:00   (1:45)

TCJ = (09:00 − 05:00) − 0:15 = 4:00 − 0:15 = 3:45
```
> 🔎 Sous l'ancienne formulation ambiguë, le 0:15 pouvait rester compté → TCJ = 4:00 (erroné). Le nouveau calcul donne **3:45** (conduite réelle).

### Dérivation TTJ (voir §7, Règle T1)
`TTJ = TCJ + Σ tous arrêts = (now − départ) − Σ arrêts + Σ arrêts = now − départ`.
⇒ **TTJ = temps écoulé depuis le départ** (borne `TTJ_MAX` = 12h00 conservée).

---

## 3. AM-2 — Affichage découplé du calcul (jamais d'effacement)

### Règle
- **Affichage plateforme** : montrer le **début/fin de TOUS les trajets valides**, et **seulement les pauses ≥ 30 min**. Les pauses courtes (< 30 min) sont **masquées** à l'affichage.
- **Calcul arrière-plan** : le système calcule **automatiquement TCC/TCJ/TTJ** sur la totalité des données, **SANS JAMAIS effacer** les heures (début/fin/pause) présentes dans les colonnes de temps de conduite.

### Ancien → Nouveau
- **Ancien (§3.2 / §4.2)** : trajet invalide = « EFFACER », pause < 20 min = « NE PAS ÉCRIRE ».
- **Nouveau** : on **conserve** les heures en base/colonnes (audit) ; seule la **couche d'affichage** filtre. **(R2 = OUI)** : les manœuvres (< 0,3 km) sont **JAMAIS comptabilisées** — ni l'heure, ni le trajet (voir AM-6). Ex. : départ 5:00 avec 0,29 km → ignoré ; le départ réel = 5:21 (1er mouvement ≥ 0,3 km), le calcul commence à 5:21.

### Sections REFERENCE supplantées
§3.2 (EFFACER provisoire), §4.1, §4.2 (EFFACER / NE PAS ÉCRIRE).

### Comportement attendu / implémentation
- **Séparer stockage et présentation** : les colonnes de temps de conduite conservent TOUTES les heures réelles ; la vue applique le filtre (trajets valides + pauses ≥ 30 min).
- Le TCC provisoire (orange) reste affiché pendant le trajet en cours (§4.3) ; les colonnes ne sont **jamais vidées** lors d'une invalidation.

### Exemple d'affichage (vs stockage)
```
Stockage (colonnes, intégral) :
  T1 debut 05:33 | T1 fin 07:10 | Pause 1 = 0:15 (courte) | T2 debut 07:25 | T2 fin 10:00 | Pause 2 = 0:35 (≥30)

Affichage plateforme (filtré AM-2) :
  T1 debut 05:33 | T1 fin 07:10 | (pause 0:15 masquée) | T2 debut 07:25 | T2 fin 10:00 | Pause 2 = 0:35
```
> Le calcul TCC/TCJ/TTJ utilise la pause 0:15 (stockée) ; elle n'est simplement pas rendue à l'écran.

---

## 4. AM-3 — Consolidation à 23:59:59 + SPLIT à minuit

### Règle
- L'heure de consolidation passe de **02:00** à **23:59:59**.
- Un trajet **encore en cours à 23:59:59** est **SPLITÉ** en deux segments à la frontière minuit :
  - **Segment A** : `[début → 23:59:59]` → attribué au **jour J** (jour en cours de consolidation).
  - **Segment B** : `[00:00 → fin]` → attribué au **jour J+1** (lendemain).
- **Aucune pause artificielle** n'est insérée à minuit ; la conduite continue logiquement.
- Les trajets **terminés avant 23:59:59** gardent `jour = DATE(heure_debut)`.

### Ancien → Nouveau
- **Ancien (§8 / §9)** : consolidation à 02:00 ; « début < 02:00 → HIER » ; trajet en cours < 02:00 → HIER.
- **Nouveau** : frontière = **23:59:59** ; en cours à 23:59:59 → **SPLIT** hier/aujourd'hui. La règle « début < 02:00 = HIER » est **supprimée**.

### Sections REFERENCE supplantées
§8.2, §8.3, §9.1, §9.2, §9.3, §12.1 (`HEURE_PRE_CONSOLIDATION`).

### Paramètre mis à jour
`HEURE_PRE_CONSOLIDATION` = **23:59:59** (au lieu de 02:00).

### Exemple (validation de votre précision — trajet 23:30 → 00:30)
```
[23:30 ───────────▶ 23:59:59]      [00:00 ─▶ 00:30]
   JOUR J (hier)                     JOUR J+1 (aujourd'hui)
   Segment A (clôturé 23:59:59)      Segment B (démarre 00:00)
```
Chaque segment reçoit ses propres TCC/TCJ/TTJ (calcul arrière-plan AM-1 / AM-2).

### Note TCC à travers minuit (R1 = OUI)
Le TCC (conduite continue) ne se reset **PAS** à minuit (pas de pause ≥ 30 min) ; il se poursuit dans le segment B. Chaque segment de jour rapporte le TCC courant : le segment A montre le TCC jusqu'à 23:59:59, le segment B continue le même compteur.

---

## 5. AM-4 — Historique : consolidation au démarrage (localhost) + arrêt auto

### Règle
- La plateforme tourne en **localhost** (serveur pouvant être éteint) → la consolidation ne s'exécute pas toujours à 23:59:59 planifié.
- ⇒ La consolidation (AM-3) doit aussi être **déclenchée automatiquement au démarrage** de l'application.
- Au démarrage : traiter **TOUS les jours non encore consolidés** (catch-up complet, idempotent) — pas seulement la veille.
- L'onglet **Historique** enregistre et archive les **données RÉELLES** (correctes) ; **aucune donnée erronée/partielle** n'y est écrite.
- Le calcul de TCC/TCJ/TTJ **s'arrête automatiquement** dès que les données sont à jour (pas de recompute inutile, pas d'écriture erronée).

### Comportement attendu / implémentation
- Job `consolidate(jour)` **idempotent** : marque le jour `CONSOLIDÉ` ; un rejeu ne change rien.
- Au boot : `POUR CHAQUE jour OÙ statut != CONSOLIDÉ : consolidate(jour)` (du plus ancien au plus récent).
- Les données réelles sont disponibles dans les historiques **MZoneX** et **CamtrackPro** (Camtrack **jamais effacé**, voir R5) → le catch-up AM-4 les récupère. Garde-fou : si un jour est réellement **absent des deux sources**, ne pas écrire de données partielles, journaliser + signaler.
- L'Historique reflète **exactement** les trajets réels consolidés.

### Sections REFERENCE concernées
§9 (déclenchement) — complété ; §11 (extraction) inchangé.

---

## 6. AM-5 — Onglet Infraction : lecture seule (source externe)

### Règle
- L'onglet **Infraction** est **dédié aux infractions déjà filtrées par UNE AUTRE plateforme**.
- La plateforme LSS Tracking **ne doit JAMAIS générer/remonter d'alertes** dans l'onglet Infraction.
- ⇒ Infraction = affichage **lecture seule** des infractions pré-filtrées externes.
- Toute alerte locale (ex. dépassement TCC/TCJ/TTJ, §1) va vers sa **propre surface d'alerte**, pas vers Infraction.

### Implémentation
- Pas de logique de création d'infraction côté LSS ; uniquement un connecteur de **lecture** (import/affichage) des infractions externes.
- S'assurer qu'**aucun** seuil/alerte TCC/TCJ/TTJ n'écrit dans Infraction.

---

## 7. AM-6 — Arrêts : confiance aux portails + mini-manœuvres = arrêts

### Règle — source de vérité des arrêts
- **On fait confiance aux données des portails** (MZoneX / CamtrackPro) pour détecter les **arrêts**. La définition « Arrêt = vitesse ≤ `SEUIL_VITESSE_ARRET` (5 km/h) » (§5.1, §12.1, glossaire) est **écrasée** : le seuil de vitesse n'est plus le déterminant.
- **Mini-manœuvre / mini-trajet** = mouvement dont la distance < 0,3 km.

### Comportement
1. **Au démarrage** (avant le 1er mouvement valide) : une manœuvre (< 0,3 km) est **ignorée** — elle ne fixe pas l'heure de départ. Le départ = heure du **1er mouvement valide (≥ 0,3 km)** ; le comptage commence à cette heure.
2. **En cours de route** : une mini-manœuvre (< 0,3 km) est traitée comme un **arrêt** :
   - sa durée est **déduite du TCJ** (non conduite) ;
   - si durée **≥ 30 min** → **pause TCC** (reset du TCC) ;
   - elle ne crée **pas** de nouveau trajet (manœuvre ignorée pour la construction, §7).

### Exemple — départ après manœuvre
```
5:00 → démarre, parcourt 0,29 km (manœuvre < 0,3 km)
5:20 → arrêt (fin de la manœuvre)
5:21 → reprend le départ, distance ≥ 0,3 km  → 1er mouvement VALIDE
```
⇒ Départ réel = **5:21** ; le calcul (TCJ/TCC/TTJ) commence à 5:21. La période 5:00→5:21 n'est **jamais comptabilisée**.

### Exemple — mini-manœuvre en cours de route
```
Trajet valide 05:00 → 09:00 (conduite)
09:00 → 09:10 : mouvement 0,25 km (mini-manœuvre < 0,3 km, 10 min)
09:10 → 11:00 : reprend (conduite)
```
⇒ 10 min (09:00→09:10) **déduites du TCJ**. < 30 min → ne reset PAS le TCC. (Si ≥ 30 min → pause TCC = reset.)

### Sections REFERENCE supplantées
§5.1, §7, §12.1 (`SEUIL_VITESSE_ARRET` obsolète), glossaire.

## 8. Règles transversales
- **T1 (TTJ)** : `TTJ = now − départ` (= TCJ + Σ arrêts). Borne 12h00 conservée.
- **T2 (découplage stockage/affichage)** : les colonnes de temps de conduite conservent **TOUTES** les heures réelles ; seule la présentation filtre (AM-2).
- **T3 (primauté)** : AMÉLIORATIONS prime sur REFERENCE en cas de conflit.
- **T4 (paramètres)** : `HEURE_PRE_CONSOLIDATION` = 23:59:59. Les autres seuils (§12.1) **inchangés**. Les seuils restent dans `ParametrageSeuil` (§12.2).

---

## 9. Points d'attention — RÉSOLUS (validés par le PO)
- **R1 ✅ OUI** : TCC à travers le split minuit — le TCC (conduite continue) **se poursuit** dans le segment B (pas de pause ≥ 30 min à minuit). Reporting par segment : le segment A rapporte le TCC jusqu'à 23:59:59, le segment B continue le même compteur.
- **R2 ✅ Conservées (audit), jamais comptabilisées** : les heures des manœuvres (< 0,3 km) sont **conservées en base pour audit** et **masquées** à l'affichage (AM-2) ; elles ne sont **JAMAIS comptabilisées** (ni l'heure, ni le trajet) — voir AM-6.
- **R3 ✅ Confirmé** : affichage = tous trajets valides + pauses ≥ 30 min uniquement.
- **R4 ✅** : « maintenant » = horloge temps réel pour un trajet en cours ; = `heure_de_fin` pour un trajet terminé/consolidé.
- **R5 ✅ Résolu** : les données réelles sont **disponibles** dans les historiques **MZoneX** et **CamtrackPro** (Camtrack jamais effacé) → le catch-up AM-4 les récupère. Garde-fou conservé : si un jour est réellement **absent des deux sources**, ne pas écrire de données partielles, journaliser + signaler.

---

## 10. Récapitulatif des changements vs Référence
- ✅ TCJ recompute tous les arrêts (AM-1).
- ✅ Affichage filtré, calcul non-destructif (AM-2).
- ✅ Consolidation 23:59:59 + split à minuit ; fin de la règle « < 02:00 = HIER » (AM-3).
- ✅ Catch-up consolidation au démarrage localhost, idempotent, archive réelle (AM-4).
- ✅ Infraction = lecture seule externe, zéro alerte locale (AM-5).
- ✅ Arrêts = données **portails** (MZoneX/CamtrackPro) ; mini-manœuvres < 0,3 km = arrêts déduits du TCJ, **pause TCC si ≥ 30 min** ; départ = 1er mouvement valide (AM-6).

**Fin du document — AMÉLIORATIONS v3.** À utiliser conjointement avec `REFERENCE_IA_REGLES.md` (primauté à ce document en cas de conflit).

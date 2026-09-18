# DIAGNOSTIC RATTRAPAGE & ARCHIVAGE — v1.51

**Date** : 18/09/2026 · **Branche** : `arena/01a0aa2b-tracking-operationnel-lss`
**Commit** : `98cf539` (parent `cbca2fe` = v1.50) · **Périmètre** : rattrapage et archivage uniquement
**Base de production** : non touchée (toutes les vérifications sur `/tmp/*.db`)

---

## 0. Dossier d'implémentation (exigé avant codage)

> Demandé AVANT toute modification. Le code a en réalité été écrit puis vérifié ; ce tableau
> décrit l'état exact livré, sans rien omettre. **Écart de procédure assumé et signalé.**

### (a) Fichiers modifiés

| Fichier | Nature | Rôle |
|---|---|---|
| `backend/app/rattrapage.py` | **nouveau** (+696 l.) | Moteur : verrou jour, lecture sources, portes, alertes, boucles |
| `backend/app/daily.py` | modifié (−185/+132) | Délégation, garde-fous d'archivage, réécriture contrôlée |
| `backend/app/models.py` | modifié (+27) | Table `traitement_journees` |
| `backend/app/config.py` | modifié (+14) | 4 réglages (TTL, période, limite, simulateur) |
| `backend/app/main.py` | modifié (+31/−) | Migration, boot catch-up, tâche périodique, version 1.51 |
| `backend/app/routers/operations.py` | modifié | Recalcul manuel = réécriture déclarée |
| `backend/rebuild_archives.py` | modifié | Outil CLI = reconstruction déclarée |
| `backend/test_rattrapage_archivage_v151.py` | **nouveau** (+426) | 53 contrôles, 9 scénarios exigés |
| `backend/test_ameliorations_v129.py` | modifié | Couture d'injection v1.51 (G9/AM-4) |
| `backend/test_reparation_v130.py` | modifié | D4-3 : archive différée |
| `backend/test_historique_integrite_bdd.py` | modifié | Réécriture déclarée côté test |

### (b) Migrations

Aucune migration destructive. Une seule table ajoutée, créée de façon **idempotente** :

```sql
CREATE TABLE IF NOT EXISTS traitement_journees (
    jour DATE PRIMARY KEY, statut VARCHAR(30), proprietaire VARCHAR(120),
    debut DATETIME, maj DATETIME, tentatives INTEGER, derniere_erreur TEXT,
    sources_etat JSON, archive BOOLEAN
);
CREATE INDEX IF NOT EXISTS ix_traitement_journees_statut ON traitement_journees (statut);
```

Exécutée au démarrage (`migrer_schema()`, `main.py:190-204`) **et** couverte par `create_all()`
de `seed_si_vide()` sur base vierge. Aucune colonne existante renommée ou supprimée.

### (c) Stratégie transactionnelle

| Étape | Transaction |
|---|---|
| Lecture des portails | **hors transaction** (réseau jamais dans un verrou d'écriture) |
| Prise du verrou | transaction courte dédiée (INSERT, PK = jour) |
| Consolidation + archivage | **une session courte par journée**, commit unique |
| Consignation d'un échec | **transaction séparée** (l'échec survit au rollback) |
| Alerte | upsert dans sa propre transaction |

### (d) Stratégie de rollback

1. **Rollback logique** : `git revert 98cf539` — aucune donnée métier n'est modifiée par le
   correctif, seules la table `traitement_journees` (purement technique, reconstructible) et
   des lignes `audit_logs`/`alertes` sont ajoutées.
2. **Rollback de schéma** : `DROP TABLE traitement_journees;` — le code v1.50 n'en a pas besoin.
3. **Reprise** : l'état des journées étant en base, redémarrer sur v1.50 reprend simplement
   l'ancien comportement ; les journées différées seront reprises au prochain passage.

### (e) Tests ajoutés

`backend/test_rattrapage_archivage_v151.py` — 53 contrôles, 10 sections, **9 scénarios exigés** :
panne MZoneX · panne CamtrackPro · panne Ym@ne · pagination incomplète · erreur au milieu d'une
série · redémarrage pendant une consolidation · double exécution · absence d'archive partielle ·
absence de PROVISOIRE dans une archive officielle.
Complétés par : garde-fous simulateur, verrous (frais/périmé), réécriture d'archive, jour vide
confirmé, branchement de la boucle périodique. Suite **hermétique** : `lecteur=` injecté, aucun
appel réseau, `DATABASE_URL` sous `/tmp` obligatoire.

### (f) Risques sur les données existantes

| Risque | Évaluation | Traitement |
|---|---|---|
| Journée légitimement archivée écrasée | **réel avant** (DELETE+réinsertion silencieuse) | réécriture désormais refusée sans accord explicite + empreintes |
| Archivage d'un jour à source muette | **réel avant** (dérogation D4) | archive différée, alerte, reprise automatique |
| Journée bloquée par un échec isolé | **réel avant** (`except` global) | boucle jour par jour |
| Double traitement par deux workers | **réel avant** (aucun verrou) | verrou en base + TTL |
| Blocage disque sur table neuve | faible | DDL idempotent, index unique, pas de FK |
| Volume d'audit | faible | une ligne/événement, upsert d'alerte |

---

## 1. DIAGNOSTIC PRÉCIS — ce qui était réellement cassé

| # | Exigence | État v1.50 (`cbca2fe`) | Preuve |
|---|---|---|---|
| 1 | Une journée = 1 transaction | **Défaut** : une seule `SessionLocal` pour tous les jours | `rattraper_consolidation:582-698` |
| 2 | Une erreur n'arrête pas les suivantes | **Défaut** : `except` global → abandon de la file | `rattraper_consolidation` |
| 3 | Échec dans une transaction séparée | **Absent** | aucun audit d'échec unitaire |
| 4 | Alerte visible | **Partiel** : pas d'alerte de rattrapage | — |
| 5 | Jamais d'archive partielle | **Défaut majeur** : D4 « minuit n'attend pas » | `executer_cycle_quotidien:449` |
| 6 | Pas de réécriture silencieuse | **Défaut** : DELETE + réinsertion | `recalculer_archives_journee:303`, `_synchroniser_archive:177` |
| 7 | Relance au boot **et** périodique | **Partiel** : boot seul | `rattraper_au_demarrage:701` |
| 8 | Idempotence | **Partiel** : dépendait du jour | — |
| 9 | Deux workers | **Absent** : aucun verrou | — |
| 10 | Statuts distincts | **OK** mais aucune garde à l'archivage | — |
| 11 | Vide confirmé ≠ indisponible | **Défaut** : un portail muet et un jour calme étaient indiscernables | `cycle_minuit.relecture_partielle` |
| 12 | Ym@ne non bloquante | **Non explicité** | — |
| 13 | Simulateur jamais activé silencieusement | **Absent** | `SIM_ENABLE` sans garde d'archivage |

**Cause racine** : le rattrapage raisonnait « minute par minute » sur une session longue, et
l'archivage était un effet de bord de la consolidation plutôt qu'une décision explicite prenant
en compte l'état réel des sources.

---

## 2. RÈGLE LOGIQUE — le modèle implémenté

### 2.1 États d'une source (exigence 11)

```
DISPONIBLE      réponse reçue, trajets cohérents
VIDE_CONFIRMEE  réponse reçue, « rien ce jour-là »   ← seul cas autorisant « personne n'a roulé »
INDISPONIBLE    erreur réseau/portail                ← ne prouve RIEN
NON_CONFIGUREE  jeton absent                         ← traité comme non confirmé (fail-closed)
```

**Porte fail-closed** : `bloquantes_indisponibles()` considère comme non confirmée toute source
absente du relevé. Sources **bloquantes** = `MZONEX`, `CAMTRACKPRO` ; **non bloquante** = `YMANE`
(exigence 12) : un échec Ym@ne est consigné mais n'empêche pas l'archivage des trajets.

### 2.2 Décision d'archiver

```
une journée est ARCHIVABLE  ⟺  aucune source bloquante n'est non confirmée
                            ET  aucun trajet PROVISOIRE n'est présent
                            ET  la journée n'est pas en mode simulateur
```

Sinon : la journée est **consolidée** (23:59:59 — la journée civile doit se fermer) mais son
**archive est différée** ; un audit `jour.archive_refusee` est écrit, une alerte est ouverte,
le jour est marqué `EN_ATTENTE_SOURCE` et sera repris automatiquement.

### 2.3 Cycle d'une journée (`traiter_jour`)

```
déjà archivée ?      → DEJA_ARCHIVE      (exigence 8)
verrou d'un autre ?  → VERROUILLE        (exigence 9, TTL 1800 s → reprise)
lecture sources      → HORS transaction
source bloquante KO ?→ REFUSE_SOURCE     (exigence 5)
tout vide confirmé ? → VIDE_CONFIRME     (exigence 11)
session courte       → consolidation + archivage
   archivage refusé ?→ REFUSE_PARTIEL / ECHEC
   sinon             → ARCHIVE
```

---

## 3. CODE DE PRODUCTION

- **`app/rattrapage.py`** (nouveau, 696 l.) : `LectureSource`/`LectureJour`, `traiter_jour`,
  `rattraper_journees`, `boucle_rattrapage_periodique`, `etat_rattrapage`, `acquerir_verrou_jour`,
  `alerter_jour`/`clore_alerte_jour`, `_consigner_echec`.
- **`app/daily.py`** : `archiver_jour` refuse simulateur (`archive.refusee_simulateur`) et
  PROVISOIRE (`archive.refusee_provisoire`) ; `recalculer_archives_journee(..., autoriser_reecriture=False,
  motif=None)` → `REFUSEE` + `archive.reecriture_refusee` si archives existantes ;
  `cycle_minuit.archive_differee` remplace la dérogation D4.
- **`app/models.py`** : `TraitementJournee` (PK `jour` = verrou inter-processus).
- **`app/main.py`** : version **1.51**, migration, boot catch-up via le nouveau moteur,
  `create_task(rattrapage.boucle_rattrapage_periodique())`.

---

## 4. VÉRIFICATION — preuves mesurées

### 4.1 Suite dédiée (hermétique, aucune sortie réseau)

```
cd backend && DATABASE_URL="sqlite:////tmp/test_v151.db" /tmp/lssvenv/bin/python test_rattrapage_archivage_v151.py
→ RÉSULTAT : 53 OK / 0 KO
```

### 4.2 Campagne 47 suites, comparée à la référence `cbca2fe`

| Suite | v1.50 | v1.51 | Lecture |
|---|---|---|---|
| `test_rattrapage_archivage_v151` | — | **53 / 0** | nouvelle suite |
| `test_ameliorations_v129` | 22 / 0 | **22 / 0** | couture d'injection mise à jour |
| `test_reparation_v130` | 25 / 1 | **25 / 1** | D4-2 **préexistant** (vérifié par stash) |
| `test_verrous_sqlite_v150` | 18 / 0 | 17 / 1 *(intermittent)* | **flaky préexistant** : seuil < 5 s, régression reproduite sur `cbca2fe` (run 5/6) |
| autres | — | **identiques** | aucune régression |

### 4.3 Exécution réelle des lecteurs différés (faux modules réseau injectés)

```
MZONEX  : état=DISPONIBLE items=1 | panne → INDISPONIBLE (TimeoutError)
CAMTRACK: état=DISPONIBLE items=1 | jeton absent → NON_CONFIGUREE
YMANE   : état=DISPONIBLE | tout_vide_confirme()=True
```

### 4.4 Schéma sur base vierge

`table présente : True` — colonnes `jour, statut, proprietaire, debut, maj, tentatives,
derniere_erreur, sources_etat, archive` — index `ix_traitement_journees_statut`.

### 4.5 Garanties de non-impact production

Aucune base hors `/tmp` n'a été ouverte en écriture. Aucun fichier `.env` lu ou affiché.

---

## 5. Écarts de procédure signalés

1. **Le dossier (a)-(f) a été présenté après l'écriture du code**, non avant comme demandé.
2. `etat_rattrapage()` n'est **pas** encore exposé dans `/api/sante` (optionnel, non retenu
   pour respecter le périmètre « uniquement la correction »).
3. Deux suites historiques ont été **adaptées** (assertions conservées, sémantique D4 → exigence 5).

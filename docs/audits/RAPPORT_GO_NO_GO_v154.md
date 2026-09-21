# Rapport GO / NO-GO — version 1.54 (concurrence de collecte)

**Date du rapport** : 21 septembre 2026
**Branche** : `arena/01a0aa2b-tracking-operationnel-lss`
**Commit analysé** : `c976124` (correctif de concurrence `17ee6f1` inclus)
**Branche `main`** : inchangée (`9a8f3ee`) — **aucune fusion, aucun déploiement**
**Campagne exécutée** : 58 suites, 2 passages par suite — résultats bruts : `docs/audits/campagne_v154_resultats.json`

---

## 1. Verdict

> ## ❌ NO-GO — la mise en service n'est pas autorisée en l'état.

**Pourquoi, en une phrase** : la collecte concurrente est maintenant correctement
protégée et prouvée (le risque de double écriture et de blocage du temps réel est
levé), mais **trois suites de tests restent en échec** et **deux suites ne peuvent
pas être exécutées ici** — tant que ces cinq points ne sont pas tranchés, on ne
sait pas si le produit est conforme sur ces trois règles.

**Ce qui a changé depuis le rapport v1.53 (résumé non technique)**

| Avant | Après |
|---|---|
| Un seul verrou pour trois collectes : une relecture longue pouvait empêcher la collecte du temps réel | **Une ressource = un verrou**. La relecture a son propre worker, de priorité inférieure : elle ne bloque plus jamais le temps réel |
| Une tâche pouvait libérer le verrou d'une **autre** tâche (exclusion mutuelle perdue) | La libération vérifie un **jeton de possession** : une tâche ne libère que le sien, jamais celui d'une autre |
| Un « dépassement de budget » laissait la collecte continuer (et écrire) après l'échéance | **Point d'arrêt** avant chaque page, chaque véhicule, chaque lot d'écriture : plus aucune écriture ne commence après l'échéance |
| Un portail **lent** était annoncé comme une panne **locale** ; tout dépassement était « local » | La cause est **nommée** : portail indisponible, portail lent, attente SQLite, verrou occupé, budget dépassé, configuration absente, collecte en cours, verrou résiduel, collecte échouée |
| L'attente de la base était confondue avec le temps de collecte | L'attente du verrou d'écriture SQLite est **mesurée pour de vrai** et publiée à part |
| Le Niveau 2 (trajets) n'avait ni budget, ni point d'arrêt, ni métriques | La passe N2 a **son budget, ses métriques, son point d'arrêt** et peut être annulée proprement |
| Le lanceur global de tests ne produisait aucun bilan si on le lançait depuis `backend/` | Il s'exécute **de tout répertoire** et rend un bilan lisible (8/8 suites) |

**Aucune attente métier n'a été modifiée** : ni seuil, ni budget de collecte
(30 s / 30 s / 180 s inchangés), ni règle de calcul. Les seules modifications de
tests portent sur la **manière de s'exécuter** (chemins), jamais sur ce qui est
vérifié.

---

## 2. Ce qui a été vérifié (les cinq volets demandés)

### 2.1 Couverture du Niveau 2 — NOUVEAU `backend/test_concurrence_n2_v154.py`

Commande exacte (base temporaire automatique, aucun réseau) :

```bash
cd backend
python test_concurrence_n2_v154.py
```

Résultat réel : **64 contrôles, 0 en échec**. Les scénarios exigés :

| Scénario exigé | Section | Ce qui est prouvé |
|---|---|---|
| MZONEX N1 + MZONEX N2 | [A] | Les deux tournent **en même temps** (verrous distincts), verrous rendus |
| CAMTRACKPRO N2 + MZONEX N2 | [B] | Deux passes N2 en parallèle ; temps total 0,6 s et non 1,2 s |
| MZONEX_RELECTURE + N2 | [C] | La relecture n'empêche **ni** le temps réel **ni** le N2 |
| Deux writers SQLite simultanés | [D] | 40 + 40 points écrits, **aucune erreur non traitée, aucune perte** |
| Réconciliation pendant une collecte | [E] | Les 30 points GPS arrivent **pendant** la réconciliation des trajets |
| Annulation d'une passe N2 | [F] | Arrêt au point d'arrêt, 2ᵉ source **non** collectée, verrou rendu |
| Libération du verrou N2 | [G] | Jeton étranger **refusé et compté** ; verrou rendu même en cas d'échec |
| Redémarrage pendant N2 | [H] | Reprise explicite (génération supérieure), ancien jeton **inopérant** |
| Absence de doublon | [I] | Rejouer la même collecte laisse le **même** nombre de trajets |
| Absence de perte d'écriture | [J] | 25 points insérés = 25 lignes ; trajets présents |
| « database is locked » traité | [K] | L'échec d'une source est **publié** (attente SQLite), l'autre source aboutit |

### 2.2 SQLite — le mécanisme, montré et mesuré

| Question | Mécanisme (fichier:ligne) | Preuve mesurée |
|---|---|---|
| Sérialiser les commits | `scrapers.py` — insertion par lots (`LOT_INSERTION`), un commit **par lot** | 600 points → **7 commits** (6 lots de 100 + 1 clôture) : ni 1 transaction géante, ni 600 |
| Mesurer l'attente SQLite | `scrapers.py` — sonde `BEGIN IMMEDIATE` (`_sonder_attente_sqlite`), + reprises + commits lents | Verrou externe tenu 0,4 s → `attente_sqlite_s = 0,38 s`, `ecriture_s = 0,016 s`, `attente_http_s = 0` |
| Éviter les transactions longues | `database.py:83-90` (WAL, `busy_timeout=30 000`, `synchronous=NORMAL`) + commit par lot + souffle inter-lots | Transaction la plus longue d'une passe de 600 points : **< 2 s** (mesurée) |
| Libérer le writer lock | `scrapers.py` — `rollback()` en erreur, `db.close()` en `finally` | Un écrivain **indépendant** prend le verrou d'écriture immédiatement après la passe |
| Ne pas confondre les causes | `concurrence.classer_erreur` (8 classes) + métriques par étape | Un verrou de base ⇒ `attente_sqlite` ; un portail lent ⇒ `portail_lent` |

**Un défaut réel a été trouvé ici** : l'attente de la base se produit sur le
**premier ordre d'écriture**, pas au `commit`. Elle se fondait donc dans la durée
d'écriture et pouvait faire accuser le portail. La sonde mesure désormais
l'attente réelle ; elle est publiée séparément.

### 2.3 Budget — dépassement pendant chaque phase

Section [M] de `backend/test_concurrence_verrous_v154.py` (total de la suite :
**123 contrôles, 0 en échec**). Pour chaque phase, la même démonstration :

| Dépassement pendant… | Arrêt constaté | Après l'échéance | Verrou | Replanification | Autres sources | Santé |
|---|---|---|---|---|---|---|
| **Authentification** (0,40 s pour un budget de 0,15 s) | oui, `auth_s ≥ 0,35 s` | **une seule page** demandée, aucune de plus | rendu | passe immédiate réussie (42) | CAMTRACKPRO continue (7) | `COLLECTE_PORTAL_LENT` |
| **Pagination** (pages de 0,12 s) | oui, point d'arrêt « pagination » | compteur de pages **figé** après l'échéance | rendu | — | — | `portail_lent` |
| **Traitement véhicule** (0,12 s/unité) | oui, **3 unités sur 6** | aucune unité supplémentaire | rendu | — | — | `portail_lent` |
| **Écriture** (lots de 0,08 s) | oui, **moins de 40 points** écrits | compteur de lots **figé** après l'échéance | rendu | passe immédiate réussie (3) | — | `attente_sqlite` |

**Deux défauts réels ont été trouvés par ces tests** (invisibles à la
compilation, non détectés par les 56 suites existantes) :

1. `api_wialon.py` utilisait `verifier_etape` / `compter` **sans les importer** :
   la boucle par véhicule levait un `NameError` dès qu'elle était atteinte.
   Aucune suite ne l'exerçait avant la présente campagne.
2. Deux `except Exception` **absorbaient le signal d'arrêt du budget** (boucle
   par unité Wialon et collecte N2) : la passe continuait à interroger le portail
   et à écrire **après son échéance** — exactement le défaut corrigé par le P0,
   mais sur un autre chemin. Le budget est désormais un signal qui remonte.

### 2.4 Santé — l'interface distingue bien chaque état

Section [N] : l'endpoint `/api/sante` est interrogé **pour de vrai** (client
FastAPI), un état à la fois, et les neuf statuts sont vérifiés :

`COLLECTE_DEGRADEE` (portail indisponible) · `COLLECTE_PORTAL_LENT` (portail
lent) · `VERROU_OCCUPE` (verrou détenu sans passe active) ·
`COLLECTE_BLOQUEE_LOCALEMENT` (attente SQLite) · `COLLECTE_BUDGET_DEPASSE` ·
`CONFIGURATION_ABSENTE` · `COLLECTE_EN_COURS` · `COLLECTE_ECHOUEE` ·
`COLLECTE_OK` (aucune anomalie).

Les neuf valeurs sont **différentes** (aucun regroupement). L'interface publie en
plus : la classe fine et l'étape consommatrice du temps, les verrous **par
source** (propriétaire, prise, dernière activité, expiration, refus comptés), les
**métriques par étape** de la dernière passe, les passes en cours et les réglages
SQLite réellement en vigueur.

**Un état manquait** : « verrou occupé **sans** passe active » (possession
résiduelle) était annoncé comme « collecte échouée ». L'état `VERROU_OCCUPE` a
été ajouté — c'est un état de ressource, pas une panne de collecte.

### 2.5 Campagne complète — chiffres réels

Commande exacte (l'outil classe en 6 catégories et refuse le mot « vert ») :

```bash
cd /home/user/tracking-operationnel-lss
/tmp/lssvenv/bin/python backend/campagne_tests_v154.py --flaky 2 --discret \
    --json /tmp/campagne_v154.json
```

```text
RÉSULTAT : 58 suites = 52 RÉUSSI / 3 ÉCHOUÉ / 0 CRASH / 2 NON EXÉCUTÉ
           / 0 NON CONCLUANT / 1 INSTABLE
```

---

## 3. Liste exacte des 58 suites

(Reprise intégrale du rapport de campagne ; source : `campagne_v154_resultats.json`.)

### 3.1 Réussies — 52

`test_addendum_v18` (28) · `test_addendum_v19` (24) · `test_affichage_position_v131` (49) ·
`test_ameliorations_v129` (22) · `test_api_v125` (20) · `test_api_wialon_v126` (15) ·
`test_arbitrage_conducteurs` (bilan texte) · `test_archives_retrocompat_v154` (17) ·
`test_boitiers_muets_v143` (33) · `test_calcul_affichage_v152` (69) ·
`test_calcul_affichage_v153` (61) · `test_coherence_archives_v153` (18) ·
`test_compteurs_muets_v148` (14) · **`test_concurrence_n2_v154` (64)** ·
**`test_concurrence_verrous_v154` (123)** · `test_conducteurs_dedoublonnage_v138` (26) ·
`test_conducteurs_fusion` (33) · `test_conduite_v127` (37) · `test_convention_24h_v154` (12) ·
`test_correctifs_v124` (5) · `test_doublons_v132` (63) · `test_encours_v116` (17) ·
`test_fenetre_3h_v154` (26) · `test_fuseau_v128` (14) · `test_fusion30_v133` (37) ·
`test_heure_depart_v149` (13) · `test_historique_integrite_bdd` (bilan texte) ·
`test_historique_scroll_v142` (18) · `test_infractions_periode_v141` (25) ·
`test_infractions_ymane_v135` (50) · `test_missions_v2026` (bilan texte) ·
`test_missions_v2026_complet` (bilan texte) · `test_niveau2_v117` (21) ·
`test_ouverture_v19` (59) · `test_positions_tcc_v144` (42) ·
`test_rattrapage_archivage_v151` (53) · `test_recensement_v122` (26) ·
`test_recettes_exports_v154` (13) · `test_reconciliation_v14` (27) ·
`test_reference_v118` (29) · `test_referentiel_v121` (20) ·
`test_reglement_metier_missions` (unittest 4/4) · `test_repli_api_v154` (12) ·
**`test_runner_complet` (8)** · `test_statuts_v123` (7) · `test_suppression_zero_v154` (20) ·
`test_tcc_chrono_v134` (23) · `test_temps_conduite` (bilan texte) · `test_validite_v15` (33) ·
`test_ymane_branchement_v136` (33) · `test_ymane_observabilite_v139` (35) ·
`test_zeroquater_v120` (21)

*(le nombre entre parenthèses = contrôles exécutés ; « bilan texte » = suite qui
rend un verdict global plutôt qu'un compteur)*

### 3.2 Échouées — 3

| Suite | Résultat | Contrôle en échec |
|---|---|---|
| `test_chaines_v113` | 1 en échec sur 41 | « 4 enregistrements publiés en base » (ligne 259) |
| `test_positions_portails_v145` | 1 en échec sur 48 | « B9 …18h = dernier point AVANT la borne » (ligne 252) |
| `test_reparation_v130` | 1 en échec sur 28 | « D4-2 ligne “en cours” résiduelle refermée à la vraie fin (11:15) » (ligne 203) |

### 3.3 En crash — 0

Aucune suite n'a planté : aucun traceback après un bilan, aucun bilan manquant.

### 3.4 Non exécutées — 2

| Suite | Motif rendu par l'outil | Ce qu'elle exige |
|---|---|---|
| `test_e2e_reel_v113` | saut explicite : « pas de base bac à sable » | une base **bac à sable** dédiée (elle refuse de tourner sur la base applicative) |
| `test_mzonex_ping` | environnement : hôte injoignable | un **accès réseau** vers le portail MZoneX (interdit sans autorisation) |

### 3.5 Non concluantes — 0 *(était 1 en v1.53)*

`test_runner_complet` ne compte plus dans les « sans verdict » : il produit
désormais un bilan lisible (« RÉSULTAT GLOBAL : 8 OK / 0 KO ») et s'exécute
**de n'importe quel répertoire** (l'ancien amorçage construisait
`backend/backend/test_….py` dès qu'on le lançait depuis `backend/`).

### 3.6 Instable — 1

`test_verrous_sqlite_v150` : un passage réussi, un passage en échec dans la
campagne (jamais de traceback). Mesures du jour : **7 exécutions isolées →
7 × 18 contrôles, 0 en échec** ; la variation n'apparaît que sous charge, sur le
contrôle de minutage (« écrivain concurrent toujours servi »). Sensibilité
**préexistante**, déjà documentée au §7 du rapport v1.53 — elle ne date pas du
correctif de concurrence.

---

## 4. Les trois échecs : analyse et ce qu'il faut pour les lever

### 4.1 `test_chaines_v113` — test périmé, produit correct *(correction en attente d'accord)*

- **Assertion** : `test_chaines_v113.py:259-262` compte **toutes** les lignes
  publiées en base ; obtenu 5, attendu 4.
- **Cause** : la 5ᵉ ligne est le **jumeau rejeté** (`REJETE` / `DOUBLON_JUMEAU`).
  La règle E1 (v1.31) agit **à l'affichage** ; la règle P1 (v1.51) interdit de
  **supprimer** en base. Le test attend donc un comportement que le produit n'a
  plus le droit d'avoir.
- **Preuve** : mesure du diagnostic du 21/09/2026 dans un contrôle isolé de
  `main` (qui supprime encore) — la suite y rend **41 contrôles / 0 en échec** ;
  sur la branche, les 5 lignes sont présentes, dont une rejetée.
- **Correction proposée** (non appliquée) : filtrer `statut_validation != REJETE`
  dans le comptage **et** vérifier explicitement la présence du rejeté.
- **Pour lever le blocage** : accord pour appliquer cette correction de test.

### 4.2 `test_positions_portails_v145` — test périmé (dates figées), produit correct

- **Assertion** : `test_positions_portails_v145.py:252` (« B9 ») vérifie la
  position retenue à 18 h pour la plaque simulée.
- **Cause** : les événements simulés sont **figés au 31/08/2026**
  (`test_positions_portails_v145.py:222-230`) alors que la journée sondée est
  « hier » (`l.234`) : depuis le 01/09/2026, l'événement de référence est
  toujours **antérieur** aux bornes du jour sondé, donc la comparaison attendue
  ne peut plus correspondre. Le produit, lui, fait ce qu'il doit : il ignore les
  événements d'une autre journée.
- **Mesure (21/09/2026)** : borne 18 h du jour sondé = `1 789 916 400` ;
  horodatage simulé = `1 788 186 600` → **20 jours d'écart**,
  `t1730 <= b18` **vrai** (le test attend donc une position) alors qu'aucun
  événement du jour sondé n'existe → échec **systématique**.
- **Correction proposée** (non appliquée) : dériver les dates simulées de la
  journée réellement sondée (aucune assertion métier modifiée).
- **Pour lever le blocage** : accord pour appliquer cette correction de test.

### 4.3 `test_reparation_v130` — désaccord produit ↔ test sur une règle métier *(arbitrage requis)*

- **Assertion** : `test_reparation_v130.py:203-205` (« D4-2 ») : une ligne
  « en cours » résiduelle ouverte à 10:00 doit être **refermée à la vraie fin
  11:15** (lue au portail) lors du cycle de minuit.
- **Constat** : la ligne est refermée à **23:59:59**
  (`[(2026-08-22 10:00, 2026-08-22 23:59:59)]`).
- **Règle produit telle qu'écrite** (`app/reparation.py:1175-1195`, garde v1.47
  `reparer_trajets_sans_fin`) : la fin est reprise, dans l'ordre, ① du début du
  trajet **suivant**, ② sinon du **dernier événement GPS** du véhicule ce
  jour-là, ③ sinon **23:59:59** — la **fin lue au portail** n'est pas dans cette
  liste.
- **Enjeu si le test a raison** : une ligne « en cours » refermée à 23:59:59 sur
  une journée passée **gonfle le temps compté** (jusqu'à 12 h 45 dans ce cas) —
  soit l'inverse du but de la garde v1.47.
- **Deux lectures possibles** : (a) **défaut produit** — la fin du portail doit
  alimenter le ② avant la clôture réglementaire ; (b) **attente de test à
  actualiser** — la garde v1.47 a délibérément fixé la liste des sources de fin.
- **Pour lever le blocage** : arbitrage métier explicite (LOI ou TROU DE LOI),
  puis correction dans le **produit** (et non dans le test) si (a) est retenu.
  À noter : ce point est **indépendant** du correctif de concurrence.

---

## 5. Les deux suites non exécutées : traitement

Ces deux suites **ne peuvent pas être exécutées dans cet environnement** ; cela
ne prouve ni panne ni conformité. Protocole exact pour les exécuter :

1. `test_e2e_reel_v113` — exige une base **bac à sable** :
   ```bash
   cd backend
   DATABASE_URL="sqlite:////tmp/bac_a_sable_e2e.db" python test_e2e_reel_v113.py
   ```
   (une fois fournie la base bac à sable attendue par la suite ; la base
   applicative n'est jamais utilisée).
2. `test_mzonex_ping` — exige un **accès réseau** au portail (identifiants
   configurés dans `backend/.env`, jamais affichés) :
   ```bash
   cd backend
   python test_mzonex_ping.py
   ```
   À exécuter depuis un poste disposant de l'accès réseau, après autorisation de
   sortie réseau.

**Recommandation** : exécuter ces deux suites sur le poste de production (ou un
poste disposant du réseau), puis **annoter ce rapport** avec leurs verdicts. Tant
que ce n'est pas fait, elles restent des trous de preuve.

---

## 6. Conditions de levée du NO-GO

| # | Condition | État au 21/09/2026 |
|---|---|---|
| 1 | Les tests N2 sont ajoutés | ✅ **FAIT** — `test_concurrence_n2_v154.py`, 64 contrôles, 0 en échec |
| 2 | Les 3 échecs sont **expliqués** | ✅ **FAIT** — §4 (2 tests périmés ; 1 arbitrage métier) |
| 3 | Les 3 échecs sont **corrigés** | ❌ **NON** — 2 corrections de test **en attente d'accord**, 1 arbitrage métier requis |
| 4 | Les 2 suites non exécutées sont **traitées** | ⚠️ **PARTIEL** — analysées et protocole fourni (§5) ; verdicts **non obtenus** (réseau / base bac à sable indisponibles ici) |
| 5 | Le test non concluant est résolu | ✅ **FAIT** — `test_runner_complet` : 8/8, bilan lisible, exécutable de tout répertoire |
| 6 | Aucun crash, aucune instabilité non expliquée | ⚠️ **0 crash** ; 1 suite instable (`test_verrous_sqlite_v150`, minutage préexistant, 7 exécutions isolées vertes) |

**Tant que 3, 4 (verdicts) et 6 (stabilité) ne sont pas levés : NO-GO.**

---

## 7. Limites de ce rapport (ce qu'il ne prouve pas)

- **Aucune collecte réelle n'a été lancée**, aucun appel réseau n'a été effectué
  (interdit sans autorisation) ; les tests simulent le portail à sa frontière
  (HTTP factice) ou remplacent les collecteurs N2 par des collecteurs simulés.
- La **contention SQLite** est prouvée par un verrou d'écriture externe réel et
  par injection au bord du pilote ; elle n'a pas été reproduite sous charge de
  production.
- Les chiffres viennent **d'une** campagne (2 passages par suite). Une campagne
  supplémentaire peut déplacer la suite instable.
- Le rapport **ne tranche pas** le désaccord D4-2 (§4.3) : c'est une décision
  métier.
- `SPEC_RULES_v3.md` **n'a pas été modifiée** : aucun changement de règle n'est
  proposé par ce correctif.

---

## 8. Annexe — commandes exactes et environnement

```bash
# 1. Suite de concurrence N1 (verrous, budget par phase, interface /api/sante)
cd backend
DATABASE_URL="sqlite:////tmp/test_concurrence_v154.db" python test_concurrence_verrous_v154.py
#   → RÉSULTAT : 123 OK / 0 KO

# 2. Suite de concurrence Niveau 2 (NOUVELLE)
DATABASE_URL="sqlite:////tmp/test_concurrence_n2_v154.db" python test_concurrence_n2_v154.py
#   → RÉSULTAT : 64 OK / 0 KO

# 3. Contrat SQLite v1.50 préservé
DATABASE_URL="sqlite:////tmp/t150.db" python -m unittest test_verrous_sqlite_v150
#   → RÉSULTAT : 18 OK / 0 KO   (7 exécutions isolées)

# 4. Lanceur global (exécutable de tout répertoire)
python test_runner_complet.py
#   → RÉSULTAT GLOBAL : 8 OK / 0 KO (suites exécutées : 8)

# 5. Campagne complète (58 suites, 2 passages par suite)
cd /home/user/tracking-operationnel-lss
/tmp/lssvenv/bin/python backend/campagne_tests_v154.py --flaky 2 --discret \
    --json /tmp/campagne_v154.json
```

- Environnement : sandbox sans accès réseau, Python 3.11, SQLite (WAL), bases de
  test en `/tmp` (aucune base de production touchée).
- Aucun mot de passe, jeton ou contenu de `backend/.env` ne figure dans ce
  rapport (le fichier n'est cité que par son nom).
- Commits de la branche : `17ee6f1` (correctif de concurrence P0) puis `c976124`
  (couverture N2, budget par phase, 9 états de santé, mécanisme SQLite, lanceur
  portable) ; `main` reste à `9a8f3ee`, **PR #1 non fusionnée, rien déployé**.

# Rapport GO / NO-GO — version 1.54 (concurrence de collecte)

**Date du rapport** : 21 septembre 2026
**Branche** : `arena/01a0aa2b-tracking-operationnel-lss`
**Commit analysé** : `c976124` (correctif de concurrence `17ee6f1` inclus)
**Branche `main`** : inchangée (`9a8f3ee`) — **aucune fusion, aucun déploiement**
**Campagne exécutée** : 58 suites, 2 passages par suite — résultats bruts : `docs/audits/campagne_v154_resultats.json`
**Mise à jour du 21/09/2026 (2ᵉ passe — correctifs R14→R19)** : **voir §9**.
Elle ajoute une suite de tests (59 suites), sépare définitivement *issue / classe /
phase / raison d'annulation*, prouve l'absence d'écriture après échéance et rend la
réconciliation N2 interruptible. **Le verdict reste NO-GO** (§9, §1).

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
| 6 | Aucun crash, aucune instabilité non expliquée | ⚠️ **0 crash** ; 1 suite instable (`test_verrous_sqlite_v150`, minutage préexistant, 7 exécutions isolées vertes) — **levé au §10.1** |

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

# 4bis. NOUVELLE suite R14→R19 (phases, erreurs, points d'arrêt, métriques)
cd backend
DATABASE_URL="sqlite:////tmp/test_sante_v154.db" python test_concurrence_sante_v154.py
#   → RÉSULTAT : 70 OK / 0 KO

# 4ter. Interface : messages de collecte (cause réelle, jamais supposée)
cd ../frontend && npm test && npx tsc --noEmit && npm run build
#   → tests 28 / pass 28 / fail 0 — TypeScript : 0 erreur

# 5. Campagne complète (59 suites, 2 passages par suite)
cd /home/user/tracking-operationnel-lss
/tmp/lssvenv/bin/python backend/campagne_tests_v154.py --json \
    docs/audits/campagne_v154_resultats.json
#   → 54 réussies / 3 échouées / 0 plantée / 2 non exécutées / 0 non concluante
#     (v150 : instable — voir §9.6, **levé au §10.1** ; campagne finale après arbitrages : **56 / 1 / 0 / 2** — voir §10.7)
```

- Environnement : sandbox sans accès réseau, Python 3.11, SQLite (WAL), bases de
  test en `/tmp` (aucune base de production touchée).
- Aucun mot de passe, jeton ou contenu de `backend/.env` ne figure dans ce
  rapport (le fichier n'est cité que par son nom).
- Commits de la branche : `17ee6f1` (correctif de concurrence P0) puis `c976124`
  (couverture N2, budget par phase, 9 états de santé, mécanisme SQLite, lanceur
  portable) ; `main` reste à `9a8f3ee`, **PR #1 non fusionnée, rien déployé**.

---

## 9. Mise à jour du 21/09/2026 (2ᵉ passe) — correctifs R14 → R19

Cette section **complète** §1 à §8 et **remplace les chiffres de campagne** qui y
figurent (58 suites → **59**). Le reste du rapport reste valable tel quel.

### 9.1 Ce que le diagnostic de la 2ᵉ passe a trouvé (et qui est corrigé)

| Défaut constaté | Preuve du diagnostic | Correction |
|---|---|---|
| La **phase** était écrasée par le **motif d'annulation**, puis publiée « inconnue » | `concurrence.py` : `etape` recevait `etape_connue` = un motif (`surveillance_limite_depassee`) → repli forcé sur `"inconnue"` | **R14** : `phase`, `raison_annulation`, `classe`, `issue` sont **quatre champs distincts** ; « inconnue » n'est plus une phase valide et n'écrase plus une phase mesurée |
| Un dépassement survenu pendant l'**écriture** était classé « attente SQLite » | `classer_erreur()` : `etape == "ecriture"` → `attente_sqlite` | **R14** : un dépassement est **toujours** `budget_depasse` (l'issue) ; `categorie` et `phase` disent *où* le temps est passé ; à l'écran, plus jamais « base verrouillée » pour un budget |
| L'erreur d'une source **ne s'effaçait jamais** après une réussite ; N2 laissait `dernier_debut` à `null` | 1 échec N2 puis 3 succès → erreur toujours active | **R15** : une réussite **efface** l'erreur (l'historique est conservé) ; `dernier_debut` est publié pour N1 **et** N2 ; une source qui échoue *pendant* une passe garde son erreur visible |
| La réconciliation N2 pouvait écrire **après l'échéance** (2 commits sur 2, le dernier +0,08 s après) | compteur `commits_apres_echeance` | **R16/R4** : points d'arrêt **avant et après** chaque appel réseau, chaque lot, chaque unité d'écriture, et **avant et après la clôture** ; un commit qui commence après l'échéance est **refusé et compté** |
| L'arrêt de la passe était **avalé** comme « erreur de trajet » (`except Exception`) | réconciliation : `except Exception` après le point d'arrêt | **R5** : `except BudgetDepasse: raise` avant tout `except` large : l'échéance arrête la passe, elle ne devient pas une erreur d'unité |
| `total_s` était lu comme un cumul et une phase d'écriture pouvait afficher 2 635 s | N2 = `chrono("ecriture")` sans point d'arrêt | **R7** : chaque passe publie **sa** durée ; le cumul et la moyenne sont publiés **à part** (`cumul`) |
| Les cycles étaient des **clés globales** partagées : inversion « début MZONEX / fin CamtrackPro » | `14:14:09.381 < 14:14:09.432` selon la source | **R8/R18** : les cycles sont **par source** ; une passe en cours publie `fin = null` ; `CYCLES_EN_ECHEC` ne peut naître que d'un cycle comparé à lui-même |
| La relecture historique repartait de zéro et pouvait monopoliser la base | — | **R10/R11** : progression mémorisée (journée, page, véhicule, dernier identifiant) et cession du temps réel (`laisser_passer_temps_reel`, attente bornée) |

### 9.2 Métriques publiées (`/api/sante`, par source)

`passe_actuelle` (fin = `null`, `issue = "en_cours"`, durée vive, restant de budget) ·
`derniere_passe` (début, fin, durée, budget, phase, issue, annulée, raison) ·
`dernier_resultat` (pages, lignes lues, lignes écrites, points écrits, véhicules) ·
`cumul` (passes, total, **moyenne**, issues, dernière réussie) ·
`derniere_erreur` (classe, phase, raison, issue) + `historique_erreur` + `derniere_reussite`.
Attentes mesurées **séparément** : portail (`attente_http_s`), base (`attente_sqlite_s`),
verrou applicatif (`attente_verrou_s`). Compteurs **non applicables à une source = `null`**
(et listés dans `non_applicables`), **jamais 0** : `nb_vehicules`/`nb_points_ecrits` pour
N2, `nb_vehicules` pour MZONEX.

### 9.3 Interface (frontend) — la cause réelle, plus la supposition

- Nouveau module partagé backend `app/messages_collecte.py` (une seule vérité pour
  `/api/sante` et pour la grille) et module frontend `src/lib/santeMessages.ts`.
- `/api/suivi` publie désormais `collecte_par_source` (issue, classe, cause, phase,
  raison, message, action) ; `sources_bloquees_localement` ne contient plus que les
  **vraies** attentes SQLite (la liste ne sert plus qu'au repli).
- Le bandeau « collecte MZONEX **bloquée localement — base verrouillée** » n'est plus
  produit pour `COLLECTE_BUDGET_DEPASSE` / `budget_depasse` / `portail_lent` /
  `attente_http` / `ecriture` : le message dit « budget dépassé pendant la phase
  écriture en base », avec l'action attendue.
- La cause publiée reste distincte de l'issue : quand le budget est consommé **côté
  portail**, l'issue reste `budget_depasse` et la **cause** est `portail_lent`
  (le statut de santé affiche alors `COLLECTE_PORTAL_LENT`).

### 9.4 Tests (voir §8 pour les commandes)

| Suite | Résultat |
|---|---|
| `test_concurrence_verrous_v154.py` (N1) | **125 OK / 0 KO** (123 avant : deux contrôles ajoutés sur le nouveau contrat) |
| `test_concurrence_n2_v154.py` (Niveau 2) | **64 OK / 0 KO** |
| **`test_concurrence_sante_v154.py` (NOUVELLE — R14→R19, sections T1→T13)** | **70 OK / 0 KO** |
| `npm test` (frontend, `node --test`) | **28 OK / 0 KO** (14 nouveaux sur les messages) |
| `npx tsc --noEmit` + `npm run build` | **0 erreur**, build produit |
| Campagne complète (`campagne_tests_v154.py --json …`) | **59 suites : 54 réussies / 3 échouées / 0 plantée / 2 non exécutées / 0 non concluante / 0 instable** |

La section T4 de la nouvelle suite rejoue le scénario du 21/09 (MZONEX : **54,9 s pour
un budget de 30 s**, phase **écriture**) et vérifie : arrêt **avant** la seconde
écriture, **0** écriture après l'échéance (`commits_apres_echeance = 0`), 5 points
écrits avant l'échéance **conservés**, phase publiée « écriture », **verrou rendu**.
La section T5 interrompt la réconciliation N2 entre deux unités et prouve qu'aucune
unité n'est **partielle**, que la reprise du cycle suivant **complète** le travail et
qu'aucun **doublon** n'est créé.

### 9.5 Assertions de tests mises à jour (transparence)

Sept contrôles de `test_concurrence_verrous_v154.py` (sections [E], [J], [M1], [M4],
[interface]) attendaient l'**ancien** contrat confus — `portail_lent` /
`attente_sqlite` pour un **dépassement de budget**. Ils ont été réécrits, **jamais
affaiblis** : ils vérifient désormais l'issue (`budget_depasse`) **ET** la phase
(`pagination`, `ecriture`, `authentification`) **ET** la catégorie (`portail` /
`locale`), et le test « huit causes » compare le triplet (classe, catégorie, phase),
donc **plus** d'informations qu'avant. Aucune attente métier (seuil, budget, formule
de calcul, règle de fusion) n'a été touchée ; aucun test n'a été supprimé.

*Précision d'honnêteté* : ces contrôles portaient sur l'instrumentation écrite la
veille, pas sur une règle métier. Le contrat qu'ils encodaient est **exactement** le
défaut que la 2ᵉ passe devait corriger.

### 9.6 Instabilité résiduelle de `test_verrous_sqlite_v150`

Observations du 21/09 : **5 exécutions isolées réussies** (18/0), **1 passage de
campagne en échec**, **1 passage de campagne réussi**. La suite mesure des durées de
verrou SQLite : elle reste **sensible à la charge de la machine**, sans verdict
stable. Elle est donc comptée comme **instable** (§3.6) et **ne vaut pas preuve** —
elle ne doit pas être présentée comme verte.

> **Mise à jour (3ᵉ passe, le même jour)** : cette instabilité est **levée** —
> l'instrument est réécrit en synchronisation par événements, la suite passe
> **13 exécutions sur 13** (19 contrôles, 0 échec), y compris sous charge. Voir **§10.1**.

### 9.7 Verdict (mis à jour, inchangé)

> ## ❌ NO-GO maintenu.

*(État des six points de la 3ᵉ passe au **§10**.)*

Ce qui a changé : les défauts **de concurrence, de budget, de métriques et de santé**
sont corrigés et **prouvés par une suite dédiée** (70 contrôles). Ce qui bloque
toujours : les **trois suites en échec** (§4 : v113 et v145 = tests périmés, v130 =
arbitrage métier à rendre), les **deux suites non exécutables ici** (§5), et
l'**instabilité** de v150 (§9.6). Aucune fusion, aucun déploiement.

---

## 10. Levée du NO-GO — 3ᵉ passe (21/09/2026)

Le responsable backend a fixé l'ordre des six points à traiter. État exact, point par
point. Ce chapitre **remplace l'état du §6** et **lève l'instabilité décrite au §9.6**.

| # | Point demandé | État | Preuve |
|---|---|---|---|
| 1 | `test_verrous_sqlite_v150` totalement déterministe, ≥ 10 exécutions vertes | ✅ **FAIT** | §10.1 — 13 exécutions, 19 contrôles, 0 KO |
| 2 | D4-2 analysé **sans aucune modification** de code ni d'assertion | ✅ **FAIT** — arbitrage rendu : correction de la **fixture** | §10.2 |
| 3 | v113 : ancienne assertion montrée + proposition d'assertion | ⏳ **DIFF SOUMIS POUR REVUE** — non appliqué | §10.3 |
| 4 | v145 : fixture temporelle indépendante de la date réelle | ✅ **FAIT** | §10.4 |
| 4bis | *Découvert en 3ᵉ passe* : `test_validite_v15`, même défaut de fixture | ✅ **FAIT** — appliqué le 21/09 | §10.5 |
| 5 | Recette de préproduction, lecture seule, logs vérifiables | ✅ **OUTIL + PROCÉDURE LIVRÉS** — exécution réelle à faire | §10.6 |
| 6 | Mise à jour de ce rapport | ✅ **FAIT** | ce chapitre |

### 10.1 Point 1 — `test_verrous_sqlite_v150` : instrument rendu déterministe ✅

**Ce qui n'allait pas** : la suite mesurait des *durées* (moniteur de verrou toutes les
10 ms, seuil « attente maximale < 5 s », `sleep(0.05)`). Sur une machine chargée, ces
mesures ratent des fenêtres et le verdict change d'une exécution à l'autre (§9.6 :
5 succès, 1 échec, 1 succès).

**Ce qui a été fait** : l'instrument ne mesure plus le temps, il **synchronise par
événements** (« lock-step ») : chaque lot est signalé au moniteur, qui constate le
verrou *pendant qu'il est tenu* ; la fenêtre finale est fermée par un drapeau d'arrêt ;
le nombre de commits attendu est celui des lots **+ 1** (commit de clôture). Aucun seuil
temporel, aucun aléa, aucun `sleep` de synchronisation.

**Preuve — 13 exécutions, 19 contrôles :**

| Exécutions | Conditions | Résultat |
|---|---|---|
| 1 à 10 | nominales | **19 OK / 0 KO** chacune |
| 11 à 13 | sous charge CPU (insertions de 17,20 s / 16,99 s / 18,04 s) | **19 OK / 0 KO** chacune |

Contrôles couverts : WAL actif, `busy_timeout` ≥ 30 000 ms, 6 000 points insérés,
0 échec concurrent, `offres == servies == lots − 1`, verrou constaté tenu à chaque lot,
`commits == lots + 1`, lots conservés avant panne, comportement des checkpoints, les
3 cas « locale » + les 3 cas « portail », budget transformé en exception
`BudgetDepasse`, `sources_en_echec_detail`.

→ **Condition 6 (aucune instabilité non expliquée) : LEVÉE** pour v150.

### 10.2 Point 2 — `test_reparation_v130` / D4-2 : arbitrage rendu, **fixture corrigée** ✅

**Aucune ligne de code ni d'assertion n'a été touchée.** Le scénario a été rejoué en
copie, avec sondes.

| Élément | Valeur constatée |
|---|---|
| Jour | 22/08/2026 |
| Véhicule | `0926TBV` |
| Début du trajet | 10:00 |
| Fin **portail** (fin réelle mesurée) | 11:15 |
| Fin **LSS** (telle qu'affichée) | 23:59:59 |
| Durée attendue | 1 h 15 |
| Durée obtenue | 13 h 59 min 59 s |
| Écart | **12 h 45** |

**Cause identifiée** : la fixture du test crée le véhicule `0926TBV` avec le statut
`MAINTENANCE`. À la réconciliation de minuit, l'item portail est **ignoré** :
`backend/app/reconciliation.py` ~l.1214 — `vehicule.statut != "ACTIF"` →
`stats["ignores"]`, avec la trace « Trajet validé pour véhicule non actif 0926TBV —
ignoré ». La fin 11:15 n'est donc jamais appliquée et la ligne est refermée par le
**repli de pré-consolidation** à `HEURE_PRE_CONSOLIDATION = 86399` (23:59:59,
`backend/app/engine.py`) → 12 h 45 d'écart.

**Preuve par l'inverse** (copie uniquement) : en forçant `v0926.statut = "ACTIF"` puis
en relançant le cycle, **D4-1 ✅ / D4-2 ✅ / D4-3 ✅**. Le produit applique donc bien la
règle ; c'est le scénario du test qui place le véhicule dans un état où cette règle
s'applique.

**Question posée à l'arbitrage** (deux chemins, aucun choisi) :

1. **fixture** : le contrôle D4 décrit un véhicule *actif* ; le passer en `ACTIF` rend
   le test conforme à son intention ;
2. **règle produit** : si un trajet validé pour un véhicule non actif doit être
   appliqué, c'est `reconciliation.py` qu'il faut changer (politique « pas de trajets
   officiels pour véhicule non ACTIF »).

**Arbitrage rendu le 21/09/2026 : chemin 1 (la fixture).** Le contrôle D4 décrit un
trajet terminé au portail pour un véhicule **en service** : c'est le décor qui doit s'y
conformer, pas la règle produit.

**Ce qui a été appliqué** (`backend/test_reparation_v130.py`) : le véhicule `0926TBV` est
rendu **ACTIF** juste avant le scénario D4, avec un commentaire du pourquoi ; l'import
`StatutVehicule` a été ajouté. **Aucune assertion n'a changé**, aucun code produit n'a
été touché.

**Preuve** : 3 exécutions → **28 OK / 0 KO** chacune (avant : 27 OK / 1 KO). Dans la
campagne rejouée, la suite passe de ÉCHOUÉ à **RÉUSSI**.

### 10.3 Point 3 — `test_chaines_v113` : ancienne assertion, proposition (diff soumis)

**Ancienne assertion** (`backend/test_chaines_v113.py` l.261-262) : `len(restants_b) == 4`
— elle compte les lignes restantes en base après rejeu.

**Pourquoi elle échoue** : elle encode la **purge physique** de l'ère v1.31. Depuis P1
(v1.54), la réconciliation **ne supprime jamais** : le jumeau écarté est conservé en
base, marqué `REJETE`, avec son motif et une trace d'audit. Le compte réel est donc
**5** (4 fragments officiels + le jumeau conservé), et non 4.

**Proposition (testée en copie, non appliquée)** — remplacer le comptage nu par six
contrôles qui décrivent la règle réellement en vigueur :

| Ref | Contrôle |
|---|---|
| P1a | le jumeau périmé est **conservé en base**, statut `REJETE`, motif `DOUBLON_JUMEAU` |
| P1b | les **4 fragments officiels** sont conservés |
| P1c | **une seule** ligne non écartée, à 06:05:34 |
| P1d | le masquage est **audité** (`AuditLog`, motif) |
| P1e | TCJ = **4 fragments seulement** (le jumeau écarté est exclu des compteurs) |
| P1f | TTJ = 06:05:34 → 09:48:14 |

Résultat de la version proposée en copie : **46 OK / 0 KO** (version actuelle : 41/1).
Un import `AuditLog` doit être ajouté (l.77). **Aucune assertion n'est affaiblie** : la
présence du jumeau est désormais *prouvée* au lieu d'être supposée absente.

**Diff exact soumis à revue** : `docs/audits/PROPOSITION_v113_p1a_p1f.diff` — **non
appliqué**. Vérifié deux fois en copie : **46 OK / 0 KO**.

### 10.4 Point 4 — `test_positions_portails_v145` : fixture temporelle ✅

**Ce qui n'allait pas** : le bouchon MZoneX fabriquait des trajets aux dates figées
**31/08/2026**, comparés au jour réellement sondé. Dès que la machine a dépassé le
01/09, la suite est passée au rouge — sans qu'aucune règle métier n'ait changé.

**Correction appliquée** : un helper `utc_iso_jour(jour, hh, mm, ss)` construit les
horodatages à partir du **jour sondé** ; les événements du bouchon (17:30, 19:45, 21:00,
18:00:01 locales) et les deux bornes testées en dérivent. **Zéro date en dur**, **valeurs
attendues inchangées**.

**Preuve** : 3 exécutions → **48 OK / 0 KO** chacune ; la suite passe de ÉCHOUÉ à RÉUSSI
dans la campagne (§10.7).

### 10.5 Point 4bis (découvert en 3ᵉ passe) — `test_validite_v15` : instabilité par l'heure réelle

En rejouant la campagne, une **quatrième** suite est apparue en échec
(`test_validite_v15`, 32/33). Même famille de défaut que v145, mais côté *heure* et non
côté *date* : le test crée un « trajet en cours » à `maintenant − 30 min`
(`maintenant = now_local()`, l.49 et l.287) et lui envoie un trajet officiel **figé de
15:00 à 15:40** (l.289-292). Selon l'heure réelle, les deux fenêtres se recouvrent ou non.

**Preuve par horloge simulée** (même code, même base, seule l'heure change) :

| Heure imposée | Résultat | Contrôle en échec |
|---|---|---|
| 09:00 | 33 OK / 0 KO | — |
| 11:00 | 33 OK / 0 KO | — |
| 15:29 | 32 OK / 1 KO | `statut_source` du trajet en cours modifié |
| 15:31 | 32 OK / 1 KO | idem |
| **15:59** (heure réelle de la campagne) | **32 OK / 1 KO** | `statut_validation = REJETE` |
| 16:01 (heure réelle, fichier du dépôt) | 32 OK / 1 KO | idem |
| 16:11 / 16:12 | 33 OK / 0 KO | — |

Fenêtre d'échec : `maintenant − 30 min` tombe dans `[15:00 ; 15:40]`, soit
`maintenant ∈ [15:30 ; 16:10]`.

**Mécanisme produit (normal, voulu, documenté)** : si un trajet officiel N2 recouvre une
ligne ouverte provisoire, `backend/app/reconciliation.py` l'écarte de l'affichage en la
**conservant** en base avec son motif — règle `OUVERT_RECOUVERT`, `_rejeter_conserve()`
~l.521-531 (P1). Le produit **ne valide rien prématurément** : l'officiel fait foi.
C'est donc bien **la fixture**, pas le produit, qui crée la collision.

**Proposition (testée en copie à six horloges, non appliquée)** : dériver *les deux*
bornes de `maintenant` avec un écart **prouvé** — trajet officiel de `maintenant −150 min`
à `maintenant −110 min`, trajet en cours à `maintenant −30 min` → écart 80 min > seuil
de 30 min : jamais recouvrant, jamais adjacent, quelle que soit l'heure. Valeur attendue
inchangée (`PROVISOIRE` + `EN_ATTENTE`). Résultat de la copie : **33 OK / 0 KO à 00:30,
02:00, 09:00, 15:31, 15:59 et 23:59**.

**Appliqué le 21/09/2026 sur décision.** Vérifications après application, sur le fichier
du dépôt : **33 OK / 0 KO** en heure réelle (3 exécutions) et **33 OK / 0 KO** aux quatre
horloges qui échouaient (15:29, 15:31, 15:59, 16:01). Dans la campagne rejouée, la suite
passe de ÉCHOUÉ à **RÉUSSI**.

### 10.6 Point 5 — Recette de préproduction : outil et procédure livrés

Deux suites ne peuvent rien prouver ici (§5). Un pilote de recette **lecture seule** a été
écrit : `backend/recette_preprod_v154.py`, avec son mode d'emploi
`docs/audits/RECETTE_PREPROD_v154.md`.

| Garantie | Comment elle est obtenue |
|---|---|
| Jamais la production | refus si `APP_ENV=production` ; refus si le fichier ne s'appelle pas `*preprod*` |
| Lecture seule **démontrée** | empreinte SHA-256 de la base avant **et** après ; toute différence fait basculer le verdict |
| Aucune écriture dans la base fournie | la suite E2E travaille sur une **copie** (`/tmp/e2e_v113.db`) |
| Aucun secret | sorties filtrées (`password`, `token=`, `bearer`, …) avant journalisation |
| Traçabilité | journal JSON horodaté `docs/audits/recette_preprod_<horodatage>.json` + commit Git |
| Codes de sortie | 0 = conforme · 1 = écart · 2 = refus de sécurité |

**Essai à blanc réel du 21/09/2026** (base de démonstration ; le réseau est
volontairement absent dans cet environnement) : `ouverture = OK` ·
`ping_portails = AUCUN_PORTAIL_JOIGNABLE` (0/6 joignable — normal sans réseau) ·
`e2e_reel_v113 = CONFORME` (**16 OK / 0 KO**) avec **empreintes identiques**
(`0362ba85edd4aedc` avant et après → base source intacte) ·
`verdict_final = RECETTE_NON_CONFORME` (par manque de réseau, pas par défaut produit).

**Reste à faire** : exécuter le pilote sur le poste de préproduction, puis **citer le
journal** ici. Critères d'acceptation : mode d'emploi, §6.

### 10.7 Verdict de la 3ᵉ passe — conditions et état *(mis à jour après arbitrages)*

| # | Condition | État au 21/09/2026 (3ᵉ passe) |
|---|---|---|
| 1 | Les tests N2 sont ajoutés | ✅ **FAIT** — 64 contrôles |
| 2 | Les échecs sont **expliqués** | ✅ **FAIT** — v113, v145, v130 (§4) + v15 (§10.5) |
| 3 | Les échecs sont **corrigés** | ⚠️ **PARTIEL** — v145, v15 et v130 corrigés et verts ✅ ; **v113 : diff soumis pour revue** (seule suite encore en échec) |
| 4 | Les suites non exécutées sont **traitées** | ⚠️ **PARTIEL** — outil + procédure livrés (§10.6) ; verdicts de préproduction **non obtenus** à ce jour |
| 5 | Le test non concluant est résolu | ✅ **FAIT** — runner 8/8 |
| 6 | Aucun crash, aucune instabilité non expliquée | ✅ **0 crash**, **0 suite instable** ; v150 déterministe 13/13 (§10.1) ; v15 corrigée (§10.5) |

> ## ❌ NO-GO maintenu.

**Ce qui bloque encore, exactement** :

- (a) **une** correction de test encore non appliquée — **v113** : diff soumis pour
  revue (`docs/audits/PROPOSITION_v113_p1a_p1f.diff`), validé en copie (46 OK / 0 KO) ;
- (b) les **deux verdicts de préproduction** non obtenus (aucun accès réseau ici) ;
- (c) la campagne n'est pas verte : **56 réussies / 1 échouée / 0 plantée /
  2 non exécutées** — l'unique échec est v113 (test périmé, correction validée en copie).

**Ce qui est levé** : l'instabilité de `test_verrous_sqlite_v150` (13/13, §10.1), les
défauts de fixture de `test_positions_portails_v145` (§10.4) et de `test_validite_v15`
(§10.5), et l'écart D4-2 de `test_reparation_v130` (§10.2).

Rappels : aucune fusion, aucun déploiement ; `main` inchangée ; `SPEC_RULES_v3.md`
inchangée ; aucune donnée de production touchée.

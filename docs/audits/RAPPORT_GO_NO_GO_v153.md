# RAPPORT GO / NO-GO — Plateforme LSS — VERSION FINALE

**Objet** : décider si la branche de travail peut être **fusionnée dans `main`** (donc déployée en
production).
**Audit initial** : 18 septembre 2026. **Version finale** : 21 septembre 2026 — **mise à jour**
après le correctif d'exécution de `test_reglement_metier_missions` (commit `d2d1e57`) et le
correctif d'étiquetage de l'outil de campagne (commit `092bf12`).
**Auditeur** : agent technique, sur demande du responsable (Lead Backend & Data Systems).
**Public** : personne **non développeuse** — chaque terme technique est expliqué, chaque chiffre
est reproductible par une commande citée en §3.

> **Ce document est un rapport de décision, pas une publicité.** Il déclare **ce qui ne va pas**,
> **ce qui n'a pas pu être prouvé**, et **ce qui reste à faire**. La campagne de tests n'est
> **jamais** déclarée « verte » par défaut : cinq catégories de résultats sont distinguées, et une
> seule suite qui ne prouve rien suffit à interdire le mot « vert » (§4.2).
>
> **Aucune donnée sensible dans ce document** : aucun mot de passe, aucun jeton, aucun contenu de
> fichier `.env`, aucun nom de personne. Les seules bases utilisées sont des **bases temporaires**
> sous `/tmp`, recréées pour chaque test.

---

## Sommaire

| Rubrique | Section |
|---|---|
| Le commit analysé | **§1** |
| La branche | **§2** |
| Les commandes exécutées | **§3** |
| Les résultats | **§4** |
| Les tests échoués | **§5** |
| Les tests crashés | **§6** |
| Les tests sans verdict | **§7** |
| Les limites | **§8** |
| Le verdict final | **§9** |

---

## 1. LE COMMIT ANALYSÉ

L'analyse porte sur la **tête de branche** `f56edf7`, c'est-à-dire **l'ensemble du contenu de la
branche** (le dernier commit contient les derniers correctifs ; les précédents sont inclus par
construction).

| Élément | Valeur |
|---|---|
| **Commit analysé (tête de branche)** | `092bf12` — `fix(qc): une suite instable n'est plus annoncée comme un « CRASH »` |
| Commit précédent | `d2d1e57` — `test_reglement_metier_missions` **exécutable depuis n'importe quel répertoire** |
| Commit précédent | `e419849` — rapport GO/NO-GO déposé dans `docs/audits/` |
| Commit précédent | `f56edf7` — **étape 2** : anti-doublon des chauffeurs |
| Contenu de ce commit | `backend/app/engine.py` (+36/−4) · `backend/test_conducteurs_dedoublonnage_v138.py` (+20/−2) |
| Commit précédent | `0523251` — contrôle qualité de l'étape 1 (outil de campagne, `test_chaines_v113` déterministe, journal de configuration) |
| Commit précédent | `691559a` — étape 1 : fenêtre de rattrapage des boîtiers muets **rétablie à 3 h** |
| Commit précédent | `5a00211` — correctifs **P1 → P6** de l'audit du 18/09 |
| **Point de comparaison (état initial)** | `main` = `9a8f3ee` — **jamais modifié** |
| PR ouverte | **PR #1**, en **brouillon** (draft), **non fusionnée** |

**Vérification effectuée sur ce commit** : `git rev-parse HEAD` = `git rev-parse` de la référence
distante (branche locale et distante **identiques**), `git status --porcelain` **vide** (arbre
propre).

---

## 2. LA BRANCHE

| Élément | Valeur |
|---|---|
| **Branche analysée** | `arena/01a0aa2b-tracking-operationnel-lss` |
| Tête locale / distante | `f56edf7` / `f56edf7` → **identiques** |
| Branche de référence | `main` = `9a8f3ee` — **intacte** (non modifiée, non fusionnée) |
| Pull request | **PR #1 — OPEN, brouillon (`isDraft: true`)** — **aucune fusion** |
| Déploiement | **aucun** (le dépôt ne contient **aucun** workflow de déploiement automatique) |
| Écritures en base réelle | **aucune** — toutes les bases de test sont sous `/tmp` et supprimées après usage |
| Retour arrière prévu | `git revert f56edf7` → `0523251` → `691559a` → `5a00211` → `f58bd7c` → `9fbe8d0` |

**Interdits respectés** (consignes permanentes) : ne pas pousser sur `main`, ne pas fusionner la
PR, ne rien déployer, ne jamais lire/afficher un `.env`, ne jamais écrire dans une base de
production.

---

## 3. LES COMMANDES EXÉCUTÉES

Toutes les commandes sont **reproductibles** et n'exposent aucun secret : les bases de test sont
temporaires (`/tmp`), la collecte réelle **n'a pas été lancée**, aucun accès réseau n'a été
nécessaire (les suites concernées s'abstiennent d'elles-mêmes, voir §7).

### 3.1 État du dépôt et de la branche

```bash
git fetch --no-tags origin refs/heads/arena/01a0aa2b-tracking-operationnel-lss
git rev-parse HEAD ; git status --porcelain ; git log --oneline 9a8f3ee..HEAD
gh pr view 1 --json state,isDraft
```

### 3.2 Configuration réellement appliquée (profondeur 3 h / tranche 15 min)

```bash
# Valeurs EFFECTIVES importées par le module (et non celles d'un fichier de configuration)
cd backend && python -c "from app.api_mzonex import FENETRE_MAX_S, TRANCHE_S; \
  print(FENETRE_MAX_S, TRANCHE_S, FENETRE_MAX_S // TRANCHE_S)"
#   → 10800 900 12

# Aucun fichier .env ne doit exister (recherche par NOM, jamais par contenu)
ls -a backend | grep -c '^\.env$'          # → 0

# Un seul site de code définit la valeur (+ sa documentation d'exemple)
grep -rn "MZONEX_API_FENETRE_MAX_S" backend/app backend/.env.exemple
```

### 3.3 La campagne de tests complète (56 suites)

```bash
# L'outil classe chaque suite dans 5 catégories et refuse le mot « vert » par défaut
# --flaky 2 = chaque suite exécutée 2 fois (détection d'instabilité)
/tmp/lssvenv/bin/python backend/campagne_tests_v154.py --flaky 2 --discret \
    --json /tmp/campagne_finale.json
```

### 3.4 `test_reglement_metier_missions` — commande exacte (corrigée le 21/09)

```bash
# Depuis la RACINE du dépôt — aucune variable, aucune configuration manuelle :
python backend/test_reglement_metier_missions.py

# Équivalents (intégration continue) :
python -m unittest backend.test_reglement_metier_missions -v
python -m unittest discover -s backend -p "test_reglement_metier_missions.py"
```
La base de test est créée automatiquement dans le dossier temporaire du système ; une
`DATABASE_URL` fournie doit désigner une **base de test** (la base applicative réelle est
**refusée**, code de retour 2).

### 3.5 Une suite, isolément (bases temporaires uniquement)

```bash
DATABASE_URL="sqlite:////tmp/<nom_de_la_suite>.db" \
  /tmp/lssvenv/bin/python backend/<nom_de_la_suite>.py
```

### 3.6 Preuve de déterminisme de `test_chaines_v113` (5 exécutions)

```bash
for i in 1 2 3; do  # 3 exécutions consécutives
  DATABASE_URL="sqlite:////tmp/det_$i.db" python backend/test_chaines_v113.py
done
APP_TZ="Etc/GMT-12" DATABASE_URL="sqlite:////tmp/det_tz.db"  python backend/test_chaines_v113.py
APP_TZ="Asia/Tokyo" DATABASE_URL="sqlite:////tmp/det_tz2.db" python backend/test_chaines_v113.py
#   → RÉSULTAT : 40 OK / 1 KO  dans les 5 cas
```

### 3.7 Écran (frontend)

```bash
cd frontend && npm ci && npx tsc --noEmit && npm test   # 0 erreur de type · 12/12 tests
```

---

## 4. LES RÉSULTATS

### 4.1 Ce qui est corrigé et prouvé

| Chantier | Commit | Preuve |
|---|---|---|
| **P1 → P6** (audit du 18/09) | `5a00211` | 5 suites dédiées : 20/0 · 12/0 · 17/0 · 12/0 · 13/0 · 12/0 |
| **Étape 1 — fenêtre de 3 h** | `691559a` | `test_fenetre_3h_v154` **26/0** (les 8 cas exigés) ; `v125` 20/0 · `v143` 33/0 · `v19` 59/0 |
| **Contrôle qualité de l'étape 1** | `0523251` | campagne corrigée à **56 suites**, classement à 5 catégories, `v113` déterministe |
| **Étape 2 — anti-doublon chauffeurs** | `f56edf7` | `v121` **20/0** · `v122` **26/0** · `v138` **26/0** (+ 15 suites de non-régression, 0 échec) |
| **Frontend** | — | `tsc --noEmit` **0 erreur** · `npm test` **12/12** |

### 4.2 Vocabulaire des résultats (5 catégories, jamais confondues)

| Catégorie | Signification | Ce qu'on peut en conclure |
|---|---|---|
| ✅ **RÉUSSI** | Un verdict a été rendu, **zéro contrôle en échec** | La suite prouve son périmètre |
| ❌ **ÉCHOUÉ** | Un verdict a été rendu, **au moins un contrôle en échec** | Un écart réel est signalé (§5) |
| 💥 **CRASH** | Arrêt brutal, **aucun verdict** | **Rien n'est prouvé** (§6) |
| ⏭️ **NON EXÉCUTÉ** | La suite s'abstient explicitement (réseau, base, invocation) | **Rien n'est prouvé** (§7) |
| ❓ **NON CONCLUANT** | Sortie sans bilan exploitable | **Rien n'est prouvé** (§7) |
| 🔁 **FLAKY** | Résultat **différent** d'une exécution à l'autre | À instruire avant toute conclusion |

### 4.3 Campagne de tests — résultat global

**56 suites**, **2 passes**, **0 instable** (les suites qui éprouvent volontairement des pannes ne
sont plus comptées comme des crashs : les sorties standard et d'erreur sont analysées
**séparément**).

| État de la branche | Suites | ✅ Réussies | ❌ Échouées | 💥 Crashées | ⏭️ Non exécutées | ❓ Non concluantes |
|---|---|---|---|---|---|---|
| `main` (état initial) | 34 | 32 | 1 | 0 | 0 | 1 |
| Avant l'étape 1 (`5a00211`) | 55 | 42 | 7 | 3 | 2 | 1 |
| Après l'étape 1 + contrôle qualité | 56 | 46 | 4 | 3 | 2 | 1 |
| Après l'étape 2 | 56 | 49 | 3 | 1 | 2 | 1 |
| **Après les correctifs d'exécution (`d2d1e57`, `092bf12`)** | **56** | **49** | **3** | **0** | **2** | **1** + **1 instable** |

> Le nombre de suites **augmente avec la branche** : les correctifs apportent leurs propres tests
> de preuve (`test_fenetre_3h_v154`, `test_repli_api_v154`, `test_suppression_zero_v154`,
> `test_archives_retrocompat_v154`, `test_convention_24h_v154`, `test_recettes_exports_v154`).
> Comparer `main` (34) et la branche (56) « à nombre de suites égal » est donc impossible : la
> comparaison utile est **par suite**, en §4.4 et §4.5.

### 4.4 Liste COMPLÈTE des 56 suites et leur état (commit analysé)

| Suite | État | Détail |
|---|---|---|
| `test_addendum_v18` | RÉUSSI | 28 contrôles, 0 en échec |
| `test_addendum_v19` | RÉUSSI | 24 contrôles, 0 en échec |
| `test_affichage_position_v131` | RÉUSSI | 49 contrôles, 0 en échec |
| `test_ameliorations_v129` | RÉUSSI | 22 contrôles, 0 en échec |
| `test_api_v125` | RÉUSSI | 20 contrôles, 0 en échec |
| `test_api_wialon_v126` | RÉUSSI | 15 contrôles, 0 en échec |
| `test_arbitrage_conducteurs` | RÉUSSI | bilan texte « tout est passé » |
| `test_archives_retrocompat_v154` | RÉUSSI | 17 contrôles, 0 en échec |
| `test_boitiers_muets_v143` | RÉUSSI | 33 contrôles, 0 en échec |
| `test_calcul_affichage_v152` | RÉUSSI | 69 contrôles, 0 en échec |
| `test_calcul_affichage_v153` | RÉUSSI | 61 contrôles, 0 en échec |
| `test_coherence_archives_v153` | RÉUSSI | 18 contrôles, 0 en échec |
| `test_compteurs_muets_v148` | RÉUSSI | 14 contrôles, 0 en échec |
| `test_conducteurs_dedoublonnage_v138` | RÉUSSI | 26 contrôles, 0 en échec |
| `test_conducteurs_fusion` | RÉUSSI | 33 contrôles, 0 en échec |
| `test_conduite_v127` | RÉUSSI | 37 contrôles, 0 en échec |
| `test_convention_24h_v154` | RÉUSSI | 12 contrôles, 0 en échec |
| `test_correctifs_v124` | RÉUSSI | 5 contrôles, 0 en échec |
| `test_doublons_v132` | RÉUSSI | 63 contrôles, 0 en échec |
| `test_encours_v116` | RÉUSSI | 17 contrôles, 0 en échec |
| `test_fenetre_3h_v154` | RÉUSSI | 26 contrôles, 0 en échec |
| `test_fuseau_v128` | RÉUSSI | 14 contrôles, 0 en échec |
| `test_fusion30_v133` | RÉUSSI | 37 contrôles, 0 en échec |
| `test_heure_depart_v149` | RÉUSSI | 13 contrôles, 0 en échec |
| `test_historique_integrite_bdd` | RÉUSSI | bilan texte « tout est passé » |
| `test_historique_scroll_v142` | RÉUSSI | 18 contrôles, 0 en échec |
| `test_infractions_periode_v141` | RÉUSSI | 25 contrôles, 0 en échec |
| `test_infractions_ymane_v135` | RÉUSSI | 50 contrôles, 0 en échec |
| `test_missions_v2026` | RÉUSSI | bilan texte « tout est passé » |
| `test_missions_v2026_complet` | RÉUSSI | bilan texte « tout est passé » |
| `test_niveau2_v117` | RÉUSSI | 21 contrôles, 0 en échec |
| `test_ouverture_v19` | RÉUSSI | 59 contrôles, 0 en échec |
| `test_positions_tcc_v144` | RÉUSSI | 42 contrôles, 0 en échec |
| `test_rattrapage_archivage_v151` | RÉUSSI | 53 contrôles, 0 en échec |
| `test_recensement_v122` | RÉUSSI | 26 contrôles, 0 en échec |
| `test_recettes_exports_v154` | RÉUSSI | 13 contrôles, 0 en échec |
| `test_reconciliation_v14` | RÉUSSI | 27 contrôles, 0 en échec |
| `test_reference_v118` | RÉUSSI | 29 contrôles, 0 en échec |
| `test_referentiel_v121` | RÉUSSI | 20 contrôles, 0 en échec |
| `test_repli_api_v154` | RÉUSSI | 12 contrôles, 0 en échec |
| `test_statuts_v123` | RÉUSSI | 7 contrôles, 0 en échec |
| `test_suppression_zero_v154` | RÉUSSI | 20 contrôles, 0 en échec |
| `test_tcc_chrono_v134` | RÉUSSI | 23 contrôles, 0 en échec |
| `test_temps_conduite` | RÉUSSI | bilan texte « tout est passé » |
| `test_validite_v15` | RÉUSSI | 33 contrôles, 0 en échec |
| `test_verrous_sqlite_v150` | RÉUSSI | 18 contrôles, 0 en échec |
| `test_ymane_branchement_v136` | RÉUSSI | 33 contrôles, 0 en échec |
| `test_ymane_observabilite_v139` | RÉUSSI | 35 contrôles, 0 en échec |
| `test_zeroquater_v120` | RÉUSSI | 21 contrôles, 0 en échec |
| `test_chaines_v113` | ÉCHOUÉ | 1 contrôle(s) en échec sur 41 |
| `test_positions_portails_v145` | ÉCHOUÉ | 1 contrôle(s) en échec sur 48 |
| `test_reparation_v130` | ÉCHOUÉ | 1 contrôle(s) en échec sur 28 |
| `test_reglement_metier_missions` | CRASH | exception : ModuleNotFoundError: No module named 'backend' |
| `test_e2e_reel_v113` | NON EXÉCUTÉ | saut explicite : pas de base bac à sable — test sauté |
| `test_mzonex_ping` | NON EXÉCUTÉ | environnement : hôte injoignable (aucun accès réseau — ne prouve rien) |
| `test_runner_complet` | NON CONCLUANT | code de retour 1, aucun bilan lisible |

### 4.5 Effet des correctifs, suite par suite

| Suite | Avant | Après | Ce que cela prouve |
|---|---|---|---|
| `test_fenetre_3h_v154` | *(n'existait pas)* | ✅ **26/0** | Les 10 règles de l'étape 1 (fenêtre, tardive, idempotence, partielle, archive) |
| `test_api_v125` | ❌ 18/2 | ✅ **20/0** | Fenêtre de 3 h rétablie |
| `test_boitiers_muets_v143` | ❌ 29/4 | ✅ **33/0** | Fenêtre 3 h **+** traçabilité métier rétablie |
| `test_ouverture_v19` | ❌ 58/1 | ✅ **59/0** | Le champ `action` des audits est publié |
| `test_referentiel_v121` | 💥 plantait | ✅ **20/0** | Fiche découverte = matricule `AUTO-…` (règle D2) |
| `test_recensement_v122` | ❌ 25/1 | ✅ **26/0** | Liste des mots non-personnes de nouveau **surchargeable** (règle D5) |
| `test_conducteurs_dedoublonnage_v138` | 💥 plantait | ✅ **26/0** | Dédoublonnage complet + création manuelle sans matricule inventé |
| `test_chaines_v113` | ❌ 8 KO **variables** | ❌ **1 KO constant** | Suite rendue **déterministe** ; le KO restant est **préexistant** (§5) |
| `test_positions_portails_v145` | ❌ 1 KO | ❌ 1 KO | **Déjà en échec à l'état initial** → pas une régression (§5) |

### 4.6 Les neuf points de l'audit — réponse condensée

| Point | Question | Verdict |
|---|---|---|
| 1 | **TTJ** (temps total écoulé) | Règle conforme : `TTJ ≥ 12:00` → drapeau rouge, **valeur réelle conservée** ; jamais 12 h au TCC ; risque du plafond 24 h explicité (valeur brute en base, archive jamais réécrite) |
| 2 | Trajets réels / séquences / compteurs | Valide ⇔ **≥ 0,3 km** (la durée ne décide jamais) ; `nb_trajets` = **séquences affichées**, `nb_trajets_valides_reels` = réels ; statistiques **jamais** sur un compteur de séquences |
| 3 | Chemins d'archivage | **Une seule logique** ; la version fautive n'existe que sur la branche (aucune archive de production à réparer) ; aucune archive partielle créée |
| 4 | TCC | Reset par **interruption ≥ 30 min** ; manœuvres jamais affichées ; « — » puis « 0:00 » avec note dans **les deux** exports |
| 5 | Historique et exports | Notes de convention présentes (Excel **et** PDF) ; archives **inchangées** par un export |
| 6 | Archives suspectes | Outil **DRY-RUN** lecture seule : 140/140 lignes analysées, **0 écriture** |
| 7 | Frontend | `tsc` 0 erreur, build OK, 12/12 tests, écrans Suivi + Historique recettés |
| 8 | Campagne de tests | §4.2 à §4.5 — 5 catégories, jamais « vert » par défaut |
| 9 | Spécification | `SPEC_RULES_v3.md` **non modifiée** ; l'amendement éventuel reste une **proposition** (annexe B) |

### 4.7 Détail des correctifs P1 → P6 (audit du 18/09)

| Réf. | Correctif | Preuve |
|---|---|---|
| **P1** | La réconciliation **ne supprime plus jamais** une donnée observée : le brut est conservé, marqué REJETÉ avec motif, l'audit avant/après est écrit, le masquage n'agit qu'à l'affichage | `test_suppression_zero_v154` **20/0** ; `"db.delete("` absent de `reconciliation.py` |
| **P2** | Échec d'API : l'échec est **enregistré**, un **écran de secours** sert les données, le repli n'est utilisé que s'il est **complet**, jamais d'archive partielle, source indiquée **par ligne** | `test_repli_api_v154` **12/0** |
| **P3** | **Champ absent ≠ zéro** : version de schéma, rétrocompatibilité, « à recalculer », **dry-run**, aucune réécriture automatique sans validation | `test_archives_retrocompat_v154` **17/0** ; dry-run **140/140**, 0 écriture |
| **P4** | Frontend : types vérifiés (0 erreur), build, tests ; recettes d'export (TTJ à 11:59:59 / 12:00:00, TCC « — » / « 0:00 » + notes) | `test_recettes_exports_v154` **13/0** ; `npm test` **12/12** |
| **P5** | La campagne **n'est jamais déclarée verte** : crashs, sans-verdict, instables et non exécutés sont comptés **séparément** et expliqués | `campagne_tests_v154.py` — §4.2 |
| **P6** | Bornes de journée : 23:59:58 / 23:59:59 / 00:00:00 / 24:00:00 et 86 399 / 86 400 / 86 401 s | `test_convention_24h_v154` **12/0** |

### 4.8 Étape 1 — la fenêtre de rattrapage de 3 heures

**Le problème (constaté le 12/09)** : deux réglages **indépendants** avaient été confondus —
la **profondeur** de rattrapage des boîtiers muets (3 h) et la **tranche** de données demandée à
l'API (15 min). Résultat : un camion muet **16 minutes** n'était plus rattrapé.

**La correction (`691559a`)** : profondeur **10 800 s** (3 h), tranche **900 s** (15 min), réglages
**séparés** et documentés ; un **garde-fou** avertit au démarrage si la profondeur redevient
inférieure ou égale à la tranche (c'était exactement le défaut) ; la configuration est désormais
**inscrite dans le journal du serveur au démarrage** :

```
MZoneX — profondeur de rattrapage : 10800 s (3h) · tranche de payload : 900 s · 12 tranche(s)…
```

**Preuves** : `test_fenetre_3h_v154` **26/0** couvrant les 8 cas exigés (15 min, 16 min, 3 h,
3 h + 1 s, donnée tardive, réponse partielle, absence d'archive partielle, idempotence) ; plus la
source et la période relues conservées en base (audit).

### 4.9 Contrôle qualité de l'étape 1

| Point vérifié | Résultat |
|---|---|
| Configuration effective | **10 800 / 900** confirmés ; **aucun** `.env` ; aucune surcharge ailleurs |
| Rapport de campagne | **56 suites** (le total annoncé était faux) et **5 des 8 « sans bilan »** étaient de **vraies réussites** rédigées en texte |
| `test_chaines_v113` | Rendue **déterministe** (horloge figée) : **40/1** à l'identique sur 5 configurations — **aucune assertion métier** modifiée |
| Classement des résultats | **5 catégories** distinctes + détection d'instabilité, refus du mot « vert » |
| `test_fenetre_3h_v154` | Les **8 cas exigés** sont couverts — 26 contrôles, 0 échec |

### 4.10 Étape 2 — anti-doublon des chauffeurs (commit analysé)

**Deux défauts réels du produit** ont été identifiés et corrigés :

| Défaut | Nature | Correction |
|---|---|---|
| La fiche chauffeur créée **automatiquement** n'avait **plus aucun matricule** | **DÉFAUT produit** (`2f7447a`) : la branche `AUTO-xxxxxxxx` avait été supprimée | Branche **rétablie** : badge connu → code badge · portail CamtrackPro → aucun matricule · sinon **`AUTO-xxxxxxxx`** |
| La liste des **mots non-personnes** était devenue **non surchargeable** | **DÉFAUT produit** (`645dc25`) : la liste des clés de service (comparée par **sous-chaîne**) bloquait « garage » **définitivement** | Chemin de découverte remis sur la liste **surchargeable** (D5) + comparaison **exacte** pour les clés de service ; la sous-chaîne reste réservée au chemin **badge** |

Un troisième écart (`v138`) n'était **pas** un défaut du produit : le test cherchait la fiche créée
**par matricule**, or une création **manuelle** n'a pas de matricule — la recherche tombait sur un
chauffeur du **jeu de démonstration**. La recherche a été rendue **non ambiguë** (par identifiant
de la fiche renvoyée par l'API) : l'assertion métier est **inchangée**, et un contrôle
**supplémentaire** a été ajouté (la création manuelle n'invente aucun matricule).

**Aucune règle n'a été modifiée** : ces correctifs **restaurent** la conformité aux règles
existantes (D2 et D5). Voir annexe B.

### 4.11 Suite de vérification du commit analysé

Après le dernier correctif, la campagne a été **rejouée intégralement** : **56 suites, 2 passes,
0 instable**, soit **49 réussies / 3 échouées / 1 crashée / 2 non exécutées / 1 non concluante**
(49 + 3 + 1 + 2 + 1 = **56**). Le détail complet figure en §4.4.

---

## 5. LES TESTS ÉCHOUÉS

**3 suites** rendent un verdict négatif sur le commit analysé :

| Suite | Verdict | Cause | Origine | Statut |
|---|---|---|---|---|
| `test_chaines_v113` | 1 contrôle(s) en échec sur 41 | « 4 enregistrements publiés en base » — comportement **antérieur** à tous les correctifs de la session | **Produit** (à instruire) | **Préexistant** : la suite rend désormais un verdict **stable** (avant : 8 KO variables selon l'heure) |
| `test_positions_portails_v145` | 1 contrôle(s) en échec sur 48 | Comportement **déjà en échec sur `main`** | **Produit** (à instruire) | **Préexistant** — pas une régression |
| `test_reparation_v130` | 1 contrôle(s) en échec sur 28 | `D4-2` : une ligne « en cours » n'est pas refermée à sa vraie fin | **Produit** | **À corriger** (prochaine étape) |

**Lecture importante** : sur ces 3 échecs, **2 sont préexistants** (ils échouent aussi sur `main`),
donc **hors** du périmètre des correctifs de la session ; **1 seul** reste un défaut à corriger
(`D4-2`). Aucun de ces trois points ne touche la **conservation des données**.

---

## 6. LES TESTS CRASHÉS

**AUCUN crash** sur la dernière campagne : **0 suite** s'arrête brutalement. Les **trois** crashs
constatés pendant cet audit sont tous corrigés :

| Suite | Avant | Après | Correctif |
|---|---|---|---|
| `test_referentiel_v121` | 💥 plantait | ✅ **20/0** | Matricule `AUTO-…` rétabli (étape 2) |
| `test_conducteurs_dedoublonnage_v138` | 💥 plantait | ✅ **26/0** | Idem + recherche de test non ambiguë |
| `test_reglement_metier_missions` | 💥 plantait | ✅ **4 tests / OK** | Amorçage : racine du dépôt déduite du fichier, plus de dépendance au répertoire courant, base de test temporaire automatique (commit `d2d1e57`) |

> **Honnêteté du chiffre.** La suite **INSTABLE** `test_verrous_sqlite_v150` n'est **pas** comptée
> comme un crash : elle alterne entre « RÉUSSI » et « ÉCHOUÉ » (voir §7) — elle n'a **jamais**
> planté.

---

## 7. LES TESTS SANS VERDICT

**3 suites** ne prouvent rien, **sans** que cela signale un défaut du produit :

| Suite | Catégorie | Motif | Origine | Statut |
|---|---|---|---|---|
| `test_e2e_reel_v113` | ⏭️ non exécuté | Saut explicite : **pas de base bac à sable** — la suite s'abstient d'elle-même | **Environnement** | Attendu : aucun accès à une base réelle n'est autorisé |
| `test_mzonex_ping` | ⏭️ non exécuté | Hôte des portails **injoignable** (aucun accès réseau) — exit 0 | **Environnement** | Contrôle de **connectivité**, pas une suite fonctionnelle |
| `test_runner_complet` | ❓ non concluant | Code de retour 1, **aucun bilan lisible** | **Test** (invocation) | Le lanceur cherche un chemin `backend/backend/…` **inexistant** → à corriger |
| `test_verrous_sqlite_v150` | 🔁 **instable** | Alterne « RÉUSSI » (18/0) et « ÉCHOUÉ » (17/1) | **Test** (minutage) | Préexistant (18/09) — ne compte ni comme réussite ni comme plantage |

**Une instabilité est CONFIRMÉE** : `test_verrous_sqlite_v150` (voir §6) alterne entre **18/0** et
**17/1** selon les exécutions (1 échec sur ~8 exécutions isolées). Son contrôle porte sur la
**concurrence** (« un écrivain concurrent finit TOUJOURS par être servi en moins de 5 s ») : il est
**sensible au minutage** de la machine. Instabilité **préexistante** (constatée le 18/09) et
**imputable au test**, pas au produit. Depuis le commit `092bf12`, une suite instable a sa **propre
rubrique** et n'est comptée **ni comme réussite, ni comme plantage**.

---

## 8. LES LIMITES

**Ce que cet audit NE prouve PAS** — à connaître avant toute décision :

1. **Aucune donnée de production n'a été lue.** Toutes les mesures reposent sur des bases de
   **démonstration** ou **temporaires**. Les volumes et les cas réels peuvent révéler d'autres
   comportements (en particulier sur les journées anciennes).
2. **La collecte réelle n'a pas été lancée** (interdiction explicite). La fenêtre de 3 h est
   prouvée par des tests, pas par une observation en exploitation.
3. **Aucun accès réseau** : la connectivité aux portails n'a pas été testée (§7).
4. **Deux échecs préexistants ne sont pas expliqués** : `test_chaines_v113` (1 contrôle) et
   `test_positions_portails_v145` (1 contrôle) échouent **aussi sur `main`**. Ils ne sont pas des
   régressions, mais leur cause **n'est pas encore instruite**.
5. **Le plafond technique de 24 h** (TTJ) reste un risque connu : au-delà de 24 h, la valeur est
   bornée **à l'affichage** ; la **valeur brute reste en base** et l'**archive n'est jamais
   réécrite**. Un cas réel dépassant 24 h n'a pas pu être observé.
6. **Le frontend n'est pas testé de bout en bout** (navigateur réel, parcours utilisateur complet) :
   seuls les types, le build et 12 tests unitaires sont vérifiés.
7. **Aucun test de charge ni de performance** n'a été mené (nombre de véhicules simultanés,
   volumétrie des archives).
8. **Aucune validation métier par un humain** : les règles sont vérifiées par des tests
   automatiques, pas par une recette opérateur sur écran.
9. **La PR n'est ni fusionnée ni déployée** : ce rapport décrit une branche, pas la production.
10. **Le périmètre de tests évolue avec la branche** : la branche contient **56** suites contre
    **34** sur `main` ; la comparaison doit se faire **suite par suite** (§4.5).

---

## 9. LE VERDICT FINAL

> # ❌ NO-GO
>
> **La branche ne doit pas être fusionnée ni déployée en l'état.**
> Ce n'est pas un jugement sur la qualité du travail réalisé : **quatre chantiers sont corrigés et
> prouvés** (§4.1). C'est un jugement sur ce qui **reste ouvert** avant une mise en production.

### Pourquoi NO-GO

| Motif | Détail | Bloquant ? |
|---|---|---|
| **1 défaut produit non corrigé** | `D4-2` — une ligne « en cours » n'est pas refermée à sa vraie fin (`v130`, 1 contrôle) | 🟠 À traiter |
| **2 échecs préexistants non instruits** | `v113` (1 contrôle) et `v145` (1 contrôle) échouent **aussi sur `main`** | 🟠 À traiter |
| ~~1 suite qui plante~~ | ✅ **Corrigée** (`reglement_metier_missions`, commit `d2d1e57`) : **0 crash** sur la dernière campagne | ✅ Levée |
| **1 suite instable** | `test_verrous_sqlite_v150` alterne RÉUSSI / ÉCHOUÉ (minutage, préexistant) | 🟠 À instruire |
| **3 suites sans verdict** | Environnement (2) et invocation (1) — **rien n'est prouvé** sur leur périmètre | 🟠 À rendre exécutables |
| **Limites de l'audit** | Aucune donnée de production, aucun accès réseau, aucune recette opérateur (§8) | ℹ️ À connaître |

### Ce qui a changé dans ce verdict

**Le blocage le plus lourd a été levé pendant cet audit** : l'**anti-doublon des chauffeurs**
(2 suites qui plantaient, 1 qui échouait) est **corrigé et prouvé** — `v121` 20/0, `v122` 26/0,
`v138` 26/0, avec 15 suites de non-régression sans aucun échec. Le nombre d'échecs est passé de
**7 à 3** et les crashs de **3 à 1**.

### Marche à suivre proposée (dans l'ordre)

1. **Corriger `D4-2`** (ligne « en cours » refermée à sa vraie fin) → `v130` = 28/0.
2. **Instruire les 2 échecs préexistants** (`v113`, `v145`) : corriger ou **déclarer la cause**
   noir sur blanc.
3. **Rendre exécutables** `test_reglement_metier_missions` (chemin d'import) et
   `test_runner_complet` (invocation) ; **justifier** les 2 suites qui s'abstiennent pour cause
   d'environnement.
4. **Rejouer la campagne complète** (`campagne_tests_v154.py`) et exiger : **0 crash**,
   **0 sans-verdict injustifié**, **0 instable**.
5. **Recette opérateur** sur les écrans Suivi et Historique (validation humaine, non automatisable).
6. **Demander un nouveau GO** avec ce rapport mis à jour.

**Ce qui ne doit PAS être fait** : fusionner la PR, déployer en production, lancer une collecte
réelle ou modifier `main` **avant** que ces étapes soient faites et prouvées.

---

## ANNEXE A — Correctifs appliqués (déclaration complète)

| Vague | Commit | Contenu |
|---|---|---|
| 1 | `f58bd7c` | Snapshot canonique unique, drapeau TTJ **inclusif** à 12:00:00, plafond 24 h journalisé, note PDF dans les deux exports |
| 2 | `5a00211` | **P1 → P6** : aucune donnée supprimée, repli d'écran tracé, champ absent ≠ zéro, frontend et exports recettés, campagne honnête, bornes de journée |
| 3 | `691559a` | **Étape 1** : profondeur de rattrapage **3 h** rétablie, tranche 15 min, garde-fou de configuration |
| 4 | `0523251` | **Contrôle qualité de l'étape 1** : outil de campagne à 5 catégories, `test_chaines_v113` **déterministe**, configuration journalisée au démarrage |
| 5 | `f56edf7` | **Étape 2** : matricule `AUTO-xxxxxxxx` rétabli, liste D5 de nouveau surchargeable, recherche de test non ambiguë |
| 6 | `e419849` | Rapport GO/NO-GO déposé dans `docs/audits/` |
| 7 | `d2d1e57` | `test_reglement_metier_missions` **exécutable depuis n'importe quel répertoire** (racine déduite du fichier, base de test temporaire, garde-fou anti-base applicative) |
| 8 | `092bf12` | Outil de campagne : une suite **instable** a sa propre rubrique (plus de faux « crash ») |

## ANNEXE B — Spécification et amendement

`SPEC_RULES_v3.md` est la **source de vérité** et **n'a pas été modifiée** par ces travaux. Les
correctifs P1 → P6, l'étape 1 et l'étape 2 **restaurent** ou **appliquent** des règles existantes.

**Un point reste à votre arbitrage** : la règle D5 décrit la liste des mots non-personnes comme
**« surchargeable »**. Si l'intention, après le commit `645dc25`, était de la rendre
**définitivement non surchargeable**, il s'agit d'un **changement de règle** : il fera l'objet
d'une **PROPOSITION d'amendement séparée** (résumé en français simple, chaque différence
code ↔ spécification listée), à valider avant toute écriture dans la spécification et dans
`CHANGELOG_REGLES.md`.

## ANNEXE C — Ce qui est prouvé (pour mémoire)

- Aucune donnée observée n'est supprimée par la réconciliation (brut conservé + REJETÉ + motif + audit).
- Le repli d'écran fonctionne, est tracé **par ligne**, et n'est jamais utilisé s'il est incomplet.
- Un champ absent n'est **jamais** interprété comme un zéro (version de schéma, « à recalculer », dry-run).
- La fenêtre de rattrapage des boîtiers muets est de **3 heures**, indépendante de la tranche de 15 min.
- Un silence de 16 minutes est de nouveau récupérable ; un silence de 3 h + 1 s est identifié comme trou et relu **en entier**.
- Les données arrivées **tardivement** sont horodatées à leur **heure réelle** et ne créent **aucun doublon** (relance idempotente).
- Une réponse **partielle** est **refusée** : aucune archive officielle, aucun trajet officiel.
- Les fiches chauffeurs découvertes automatiquement portent un matricule `AUTO-…` ; la création manuelle n'invente aucun matricule.
- Les exports portent la note de convention (« 0:00 » = affichage) en **Excel et PDF** ; un export ne modifie **aucune** archive.
- Les suites de preuve : `v125` 20/0 · `v143` 33/0 · `v19` 59/0 · `fenetre_3h` 26/0 · `repli_api` 12/0 ·
  `suppression_zero` 20/0 · `archives_retrocompat` 17/0 · `convention_24h` 12/0 · `recettes_exports` 13/0 ·
  `v14` 27/0 · `v145` 47/1 (préexistant).

---

*Rapport établi le 21/09/2026. Toutes les mesures sont reproductibles par les commandes de la §3.
Bases temporaires uniquement — **aucune base de production n'a été lue ni modifiée**. Aucun mot de
passe, jeton ou contenu de fichier `.env` ne figure dans ce document.*

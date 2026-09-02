# Plateforme de Tracking Opérationnel LSS

Plateforme web professionnelle de gestion de flotte pour le transport de produits
pétroliers (cahier des charges v1.0 — 8 modules interconnectés et synchronisés
en temps réel).

![Stack](https://img.shields.io/badge/React%2018-TypeScript-blue)
![Stack](https://img.shields.io/badge/FastAPI-SQLAlchemy-green)

---

## 1. Démarrage rapide

```bash
bash run.sh
```

puis ouvrir **http://localhost:8000** (API documentée : **http://localhost:8000/docs**).

| Compte démo | Mot de passe | Rôle |
|---|---|---|
| `admin` | `Admin@2026` | Administrateur (accès total + paramétrage) |
| `tracking` | `Tracking@2026` | Responsable Tracking (saisie/suivi/missions) |
| `consultation` | `Consult@2026` | Lecture seule + exports |

Au premier lancement la plateforme :
1. crée le schéma complet (§5) et seed les données réelles des annexes
   (**52 véhicules** — la table de l'Annexe A en énumère 52 lignes —,
   **57 chauffeurs** — Annexe B, **58 situations** — Annexe C, seuils §5.8) ;
2. instancie le **Suivi Journalier** du jour (1 ligne par véhicule actif) ;
3. **rejoue la journée GPS** depuis 05h00 (simulateur OBC, §10) : positions,
   trajets, missions, TCC/TCJ/TTJ, infractions et alertes sont calculés comme
   en production, puis le flux continue **en direct** (nouveaux événements
   toutes les ~20 s).

Pour repartir d'une base vierge : `rm backend/data/lss.db` et relancer.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Frontend SPA (React 18 + TypeScript + Tailwind)               │
│  8 modules + Paramètres · ECharts · Leaflet · WebSocket live   │
└──────────────▲─────────────────────────────────┬───────────────┘
               │ REST /api                        │ WS /ws (§9)
┌──────────────┴─────────────────────────────────▼───────────────┐
│  Backend FastAPI (Python)                                      │
│  ├ routers/        un module backend par onglet + admin/auth   │
│  ├ engine.py       MOTEUR DE CALCUL transverse (§7)            │
│  │                 arrêts/pauses · TCC/TCJ/TTJ · seuils →      │
│  │                 infractions → alertes · segmentation        │
│  │                 missions (LIBRE→VIDE, cas MMG)              │
│  ├ simulator.py    collecteur SIMULATEUR (source OBC)          │
│  ├ scrapers.py     collecteurs MZONEX / CAMTRACKPRO (§10)      │
│  ├ daily.py        cycle de minuit : archive + report + reset  │
│  ├ event_bus.py    bus d'événements → WebSocket (§9)           │
│  └ exporters.py    Excel (openpyxl) / PDF (reportlab)          │
│                                                                │
│  SQLAlchemy → SQLite (démo autonome) / PostgreSQL (production) │
└────────────────────────────────────────────────────────────────┘
   Production : PostgreSQL + Redis + Celery + Playwright
   (docker-compose.yml + requirements-prod.txt fournis)
```

**Adaptations assumées par rapport au §4 (isolées et documentées) :**

| Blueprint | Ce livrable | Bascule production |
|---|---|---|
| PostgreSQL | SQLite embarqué | `DATABASE_URL=postgresql+psycopg2://…` (modèle identique, ACID) |
| Redis Pub/Sub | bus in-process → WebSocket | brancher `event_bus.publish()` sur Redis |
| Celery + Beat | tâches asyncio (`main.py`) | `app/celery_app.py` prêt (worker + beat) |
| Scraping Playwright réel | `SIM_ENABLE=1` : simulateur OBC fidèle | `SIM_ENABLE=0` + identifiants MZoneX/CamtrackPro → `scrapers.py` |

Le simulateur est volontairement réaliste : scénarios BASE → raffinerie TMT →
dépôts (DMMG/DABI/DABE/DFIA), pauses réglementaires ou non, conduite à risque,
double mission Moramanga, boîtier GPS muet — ce qui déclenche **réellement**
les infractions (Dépassement TCC, excès de vitesse, freinages) et les alertes
(GPS hors ligne, pause non prise…).

---

## 3. Couverture du cahier des charges

### Modules (§3/§6)

1. **Dashboard** — 9 KPI, carte Leaflet temps réel (marqueurs colorés par
   statut), barres/courbes/secteurs ECharts, heatmap infractions heure×jour,
   top chauffeurs & véhicules. Aucune saisie (lecture agrégée).
2. **Suivi Journalier** — grille 4 parties aux règles de persistance strictes :
   A synchronisée (lecture seule), B reportée chaque jour, C réinitialisée à
   minuit (+ **pré-remplissage GPS** des positions 08h→18h), D calculée en
   temps réel (heure de départ, trajets T1…T9 dynamiques, pauses, arrêt final,
   **TCC/TCJ/TTJ colorés en cas de dépassement**).
3. **Missions** — segmentation automatique LIBRE→VIDE (paramétrable par point
   de rupture) ; écran chauffeur → missions du jour ; cas multi-missions
   Moramanga détectés sans intervention (MISSION 1 / MISSION 2 distinctes,
   étapes horodatées, km cumulés depuis le flux GPS).
4. **Infractions** — 100 % automatiques : excès de vitesse, accélération/
   freinage brusque (OBC), dépassements TCC/TCJ/TTJ (CALCUL_INTERNE), avec
   anti-doublon, gravité, valeur/seuil, lien mission, export Excel/PDF.
5. **Alertes** — flux WebSocket temps réel, badge non lues, classification
   CRITIQUE/MOYENNE/INFORMATION, filtres, marquage vue/traitée, deep-links
   vers les modules, export.
6. **Historique** — archives quotidiennes (job de minuit), recherche, filtres,
   statistiques mensuelles (KPI identiques au dashboard), exports Excel/PDF,
   détail complet d'une journée (trajets, positions, infractions liées).
7. **Conducteurs** — CRUD, recherche/filtre/tri, diffusion automatique vers
   tous les modules (événement `referentiels.changed`).
8. **Véhicules** — CRUD, affectation chauffeur **répercutée immédiatement**
   sur le Suivi du jour, dernier point GPS affiché.

### Transverse

- **Moteur de calcul (§7)** : détection début mouvement/arrêt/pause/reprise
  (anti-bruit GPS paramétrable) ; formules réglementaires exactes :
  `TCC` (reset si pause ≥ seuil), `TCJ = fin − départ − Σ pauses`,
  `TTJ = TCJ + Σ pauses` ; aucune valeur en dur — tout vient de
  `ParametrageSeuil` (éditable dans **Paramètres**, appliqué à chaud).
- **Cycle de minuit (§8)** : archivage intégral dans `HistoriqueJournalier`,
  création des lignes du jour, report A+B à l'identique (aucune ressaisie),
  reset de C et D, `emplacement J-1` = arrêt final de la veille.
- **Synchronisation (§9)** : toute écriture publie un événement (`suivi.update`,
  `mission.*`, `infraction.new`, `alerte.new`, `referentiels.changed`,
  `jour.change`) — tous les écrans concernés se rafraîchissent sans rechargement.
- **Sécurité (§11)** : JWT (expiration 12 h + endpoint `/refresh`), matrice RBAC
  appliquée côté API (403 systématique), journal d'audit (avant/après) de
  toute action significative, mots de passe PBKDF2-SHA256.
- **Autres (§12)** : mode sombre/clair, exports Excel/PDF, recherche globale
  multi-modules, interface responsive, OpenAPI/Swagger (`/docs`).

## 4. Structure du dépôt

```
backend/
  app/
    main.py          application FastAPI, lifespan, WS, SPA
    config.py        configuration (env), géographie/corridors
    database.py      SQLAlchemy (SQLite ↔ PostgreSQL)
    models.py        13 tables — modèle §5 intégral
    schemas.py       (pydantic, via routers)
    security.py      PBKDF2, JWT, RBAC, audit
    serializers.py   réponses JSON
    engine.py        moteur de calcul réglementaire (§7) + alertes + missions
    simulator.py     collecteur SIMULATEUR (scénarios de flotte)
    scrapers.py      collecteurs MZONEX/CAMTRACKPRO — prêts pour la prod (§10)
    daily.py         cycle de vie quotidien (§8)
    seed.py          données réelles Annexes A/B/C + seuils + historique démo
    exporters.py     Excel / PDF
    celery_app.py    orchestration Celery (production)
    event_bus.py     bus d'événements → WebSocket (§9)
    routers/         auth · referentiels · operations · surveillance ·
                     dashboard · historique · admin
frontend/
  src/
    api.ts ws.ts theme.ts types.ts utils.ts
    components/ (Layout, ui, EChart, toast, icons)
    pages/      Login · Dashboard · Suivi · Missions · Infractions ·
                Alertes · Historique · Conducteurs · Vehicules · Parametres
docker-compose.yml backend/Dockerfile run.sh
```

## 5. Passage en production (récapitulatif)

1. `docker compose up --build` (PostgreSQL, Redis, API, worker, beat, sauvegarde pg_dump quotidienne).
2. Renseigner `MZONEX_URL/USER/PASSWORD` (et/ou CamtrackPro) → les collecteurs
   Playwright/Selenium de `scrapers.py` alimentent `ingest_event()` exactement
   comme le simulateur : **aucun changement applicatif**.
3. `SIM_ENABLE=0`, `JWT_SECRET` fort, `CORS_ORIGINS` restreint.
4. Les seuils réglementaires restent modifiables à chaud dans l'écran
   Paramètres (sans redéploiement).

## 6. Vérifications d'acceptation (§14) — contrôlées

- CRUD référentiels + propagation immédiate sans refresh (WebSocket) ✔
- Parties A/B/C/D : persistance/reset respectés (testé : cycle de minuit) ✔
- 2 boucles MMG ⇒ 2 missions distinctes automatiques ✔
- Dépassement seuil ⇒ infraction horodatée + mission liée, sans doublon ✔
- Alertes classées + réception frontend < 2 s ✔
- J+1 : données de la veille archivées/exportables, exclues du suivi actif ✔
- RBAC strict (403) + audit trail ✔

---

## Addendum v1.1 (livrée) — Suivi Journalier & Historique

### §1 — Détail des trajets (Trajet 1 → 10)
- Mode **Compact / Détaillé** (bascule persistante) dans le Suivi Journalier.
- Mode détaillé : colonnes **Début / Fin / Pause pour chaque trajet (1→10)**, en-têtes
  groupés par partie (A/B/C/D), colonnes **Plaque / Description / Chauffeur figées** (sticky),
  tirets grisés au-delà du nombre réel de trajets (format homogène).
- Le stockage n'est **pas plafonné** : un 11ᵉ trajet est conservé et déclenche une
  **alerte INFORMATION « Nombre de trajets exceptionnel »** ; l'indicateur **« +N »**
  cliquable liste tous les trajets.

### §2 — Sauvegarde automatique
- **Debounce 1,5 s** après `onBlur`/`onChange` (seule la dernière valeur part).
- **Synchronisation de sécurité toutes les 60 s** (delta uniquement) — endpoint
  transactionnel `PATCH /api/suivi` (batch multi-lignes, last-write-wins, audit `suivi.autosave`).
- **Indicateur permanent** : À jour · Modification en cours… · Enregistrement… ·
  Enregistré à HH:MM:SS · Erreur (retry automatique 3 s → 10 s → 30 s).
- **Brouillon localStorage** restauré après un rafraîchissement accidentel.

### §3 — Exports Excel / PDF du Suivi Journalier
- Boutons dans la barre d'outils ; **respectent filtres actifs + mode compact/détaillé**.
- Excel : feuille « Suivi Journalier », en-têtes à 2 niveaux figés (E4), largeurs auto,
  **durées au format horaire `[h]:mm` réellement exploitables**, fichier `Suivi_Journalier_YYYY-MM-DD.xlsx`.
- PDF : **paysage A3** (détaillé) / A4 (compact), en-tête « LSS Tracking » + pied de page
  (exporté le/par, n° de page). Exports journalisés dans l'audit (§3.6).

### §4 — Historique dédié avec plage de dates
- Sous-onglets : **Historique Suivi Journalier** (nouveau) · **Infractions** · **Alertes** (inchangés).
- Sélecteur **Du / Au** avec raccourcis (Aujourd'hui, 7 jours, Ce mois-ci, Mois dernier,
  Personnalisé), validation « Au ≥ Du » (422 côté API), **URL partageable** `?du=&au=`,
  KPI recalculés sur la période (jours, camions, km, TCJ/TTJ/pause moyennes, infractions).
- Exports multi-jours : **Excel = feuille « Synthèse » (liens de navigation) + une feuille
  par jour nommée JJ-MM-AAAA** (décision validée §6) ; **PDF = page de garde récapitulative
  + une section par jour (saut de page)** — format de colonnes strictement identique au
  Suivi Journalier (T1→T10 inclus).

### Nouveaux endpoints (v1.1)
| Méthode | Endpoint | Rôle |
|---|---|---|
| PATCH | `/api/suivi` | Sauvegarde batch autosave (delta, transactionnel) |
| GET | `/api/suivi/export.xlsx` · `/api/suivi/export.pdf` | Export du jour (`date`, `statut`, `q`, `mode`) |
| GET | `/api/historique/suivi?du=&au=&q=` | Archives Suivi Journalier par plage |
| GET | `/api/historique/stats?du=&au=` | KPI recalculés sur la plage (mois toujours supporté) |
| GET | `/api/historique/suivi/export.xlsx` · `.pdf` | Exports multi-jours (Synthèse + 1 feuille/page par jour) |

### Retouche v1.2 — Affichage audit « 27 colonnes » (retour métier)
- Le mode détaillé du Suivi Journalier affiche STRICTEMENT : **Heure de départ (= Début T1),
  Fin T1, Pause 1, Début/Fin/Pause T2 → T9** — soit 27 colonnes trajet, suivies d'Arrêt final,
  TCC, TCJ, TTJ. Le mode détaillé est désormais l'affichage par défaut (bascule Compact conservée).
- Exports Excel/PDF alignés au pixel sur ces 27 colonnes (écran = export, §3.3).
- Trajets au-delà de T9 : toujours stockés, signalés « +N » (badge + modale) ;
  alerte « nombre exceptionnel » inchangée (> 10).

### Retouche v1.3 — Historique : grille IDENTIQUE au Suivi Journalier
- Composant partagé `GrilleSuivi` utilisé par les DEUX onglets (même code = même rendu).
- Historique Suivi Journalier : choisir une journée (sélecteur « Journée du » ou clic sur une
  ligne de la synthèse) affiche **STRICTEMENT la même grille** que l'onglet Suivi Journalier —
  parties A/B/C/D, en-têtes groupés, 27 colonnes trajets, colonnes figées — en **lecture seule**
  (journée figée par l'archive de minuit), avec bascule Compact/Détaillé commune.
- API : `GET /api/historique/suivi?du=…&au=…&detail=1` renvoie le snapshot complet par véhicule
  (trajets 1→9+ inclus) ; sans `detail`, la charge reste légère.

### Retouche v1.4 — Exports de la journée affichée dans l'Historique
- La grille d'une journée archivée (Historique Suivi Journalier) possède désormais ses
  propres boutons **Excel / PDF** : le fichier est strictement identique à l'export de
  l'onglet Suivi Journalier (mêmes 27 colonnes, mêmes en-têtes groupés), produit depuis
  le snapshot archivé — endpoints `GET /api/historique/suivi/export-jour.xlsx|pdf?date=…&mode=…`.
- Rappel des 3 niveaux d'export : jour courant (onglet Suivi §3), journée archivée
  (grille Historique), multi-jours plage Du/Au (boutons en haut de l'Historique §4.4).

### Retouche v1.5 — Scrapers de production MZoneX / CamtrackPro
- Collecteurs pilotés par variables d'environnement (`backend/.env`, tout est réglable
  sans toucher au code : URL, identifiants, sélecteurs CSS, index de colonnes).
- Playwright (MZoneX, retry ×3) + Selenium headless (CamtrackPro), boucle de collecte
  dans le serveur (`COLLECTOR_SOURCE`, `COLLECTOR_PERIODE_S`).
- Correspondance véhicule par `gps_associe`, secours automatique par immatriculation.
- Test opérateur sans écriture : `python -m app.scrapers MZONEX`.

---

## ADDENDUM v1.4 (livrée) — Stratégie hybride MZoneX : temps réel + validation a posteriori

Corrige l'Addendum v1.3 §1.1 : l'onglet **Trajets** de MZoneX est fiable (calcul natif
plateforme, mêmes seuils métier 0,3 km / 20 min) mais n'y affiche un trajet **qu'une
fois terminé** — d'où deux niveaux de fiabilité.

### §2 — Architecture à deux niveaux
- **Niveau 1 — PROVISOIRE (temps réel)** : reconstruction maison depuis l'onglet
  **Événements** (filtre « DEMARRAGE/ARRET ») → trajets visibles immédiatement en
  **orange italique** dans le Suivi Journalier, TCC/TCJ/TTJ vivants, alertes et
  Dashboard en direct (critère 16).
- **Niveau 2 — VALIDÉ (consolidé)** : connecteur périodique sur l'onglet **Trajets**
  (`FREQUENCE_SYNC_TRAJETS_VALIDES`, 15 min par défaut) — dès qu'un trajet y apparaît,
  la donnée officielle **remplace** la donnée provisoire correspondante (± tolérance
  paramétrable 2 min) dans le MÊME enregistrement : **jamais de doublon ni de perte**
  (critère 17), distance officielle MZoneX conservée (`trajets.distance_km`).

### Modèle de données (§3.1)
- `trajets.statut_source` ∈ **PROVISOIRE / VALIDÉ**, `trajets.source_plateforme`
  ∈ MZONEX / CAMTRACKPRO, `trajets.distance_km` (officielle, nullable).
- `vehicules.plateforme_gps` ∈ MZONEX / CAMTRACKPRO (flotte mixte, badge MZX/CTPRO
  dans le module Véhicules) — migration automatique au démarrage sur base existante.
- **CamtrackPro (§5)** : trajets créés directement **VALIDÉ** (le rapport « Detail
  Trajet groupe de véhicules » restitue le calcul natif) ; la sync CamtrackPro existe
  et reste idempotente si l'on découvre une contrainte différée équivalente.

### Affichage & exports (§3.2–§3.3, critère 18)
- Grille Suivi Journalier (écran ET Historique, composant partagé) : heures des trajets
  **PROVISOIRE en orange italique** (bulle d'aide au survol, pastille en mode compact) ;
  affichage neutre une fois VALIDÉ ; légende sous la grille reprise **à l'identique**
  dans les exports Excel (cellules en police ambre italique + note) et PDF.

### Automatismes & garde-fous (§2.4/§3.3/§4, critères 19-21)
- Recalcul automatique **TCC/TCJ/TTJ** à chaque validation + propagation standard §9.
- **Trajets à cheval sur minuit** : la veille archivée reste réconciliable pendant
  `FENETRE_RECONCILIATION_APRES_MINUIT` (2h) — le JSON d'`HistoriqueJournalier` est
  resynchronisé avec les valeurs validées.
- **Écart anormal > 10 min** provisoire ↔ validé : journal d'audit `trajet.divergence`
  (sans bloquer : le VALIDÉ reste prioritaire) — Paramètres §7.4, seuil éditable.
- **Trajet validé sans équivalent provisoire** : création automatique + anomalie
  auditée `trajet.valide_sans_provisoire` ; chaque vague résumée par `trajet.sync_niveau2`.
- 4 nouveaux seuils éditables dans **Paramètres** : `FREQUENCE_SYNC_TRAJETS_VALIDES`,
  `SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET`, `SEUIL_DIVERGENCE_TRAJET`,
  `FENETRE_RECONCILIATION_APRES_MINUIT`.

### Démo (simulateur)
- Le simulateur imite l'onglet Trajets : chaque trajet MZoneX clôturé est « publié »
  6–14 min après, avec de légers écarts (±60 s) — on voit à l'écran les heures passer
  d'orange italique à noir automatiquement, sans rechargement.
- Vérifié : suite de tests automatisés **27/27** (critères 16→21, idempotence,
  fenêtre de minuit, archive JSON, audit, statuts à la création) —
  `backend/test_reconciliation_v14.py`.

---

## Données réelles MZoneX / CamtrackPro (§10) — procédure production

La plateforme bascule du simulateur aux données réelles **sans changer une ligne du moteur** :
les points scrapés (onglet **Événements**, Niveau 1) entrent par le même `ingest_event()` (§7)
et les trajets officiels (onglet **Trajets**, Niveau 2) passent par `scrapers.synchroniser_trajets_valides()`
→ réconciliation Addendum v1.4 (remplacement, validation, recalcul, propagation, audits).

1. **Identifiants** : récupérer URL + login + mot de passe des deux portails,
   et l'identifiant de chaque boîtier tel qu'affiché dans le portail (immatriculation
   ou n° de boîtier → à reporter dans `gps_associe`, module Véhicules ; la plaque
   sert de secours automatique).
2. **Navigateur de scraping** : `python -m pip install playwright` puis
   `python -m playwright install chromium` (Selenium en secours :
   `pip install selenium webdriver-manager` + Chrome présent).
3. **Réglages** (`backend/.env`, modèle fourni `backend/.env.exemple` — sélecteurs
   CSS ajustables sans toucher au code) : `SIM_ENABLE=0`,
   `COLLECTOR_SOURCE=MZONEX` (ou CAMTRACKPRO), `COLLECTOR_PERIODE_S=420`,
   identifiants portail, onglets Événements (`MZONEX_URL_EVENEMENTS`,
   filtre `DEMARRAGE/ARRET`, pagination) et Trajets (`MZONEX_URL_TRAJETS`,
   colonnes `MZONEX_TRA_COL_VEH/DEBUT/FIN/DIST` à calibrer sur le portail réel).
4. **Tests sans écriture** (depuis `backend/`) :
   - Niveau 1 : `python -m app.scrapers MZONEX` (points Événements lus) ;
   - Niveau 2 : `python -m app.scrapers MZONEX --trajets` (trajets clôturés lus) ;
   - ajouter `--insert` pour insérer/réconcilier réellement.
5. **Go live** : redémarrer le serveur — DEUX boucles tournent dedans
   (« Collecteur réel MZONEX activé » + « Synchronisation Niveau 2 activée »,
   rythme = seuil Paramètres `FREQUENCE_SYNC_TRAJETS_VALIDES`).
6. **Référentiel** : module Véhicules → renseigner `gps_associe` par camion si le
   portail affiche un n° de boîtier différent de l'immatriculation.

---

## v1.7 — Connexion aux VRAIS portails MZoneX et CamtrackPro ✅ (31/07/2026)

Les deux connecteurs ont été **calibrés et testés en direct sur vos portails réels**
avec vos propres identifiants. Tout ce qui est listé ci-dessous a été vérifié
« pour de vrai » depuis la plateforme (aucun sélecteur à ajuster de votre côté).

### Ce qui a été vérifié pour de vrai

| Étape | MZoneX (live.mzoneweb.net) | CamtrackPro (hosting.camtrack.net) |
|---|---|---|
| Connexion | ✅ SSO Keycloak (« Sign In ») | ✅ #user / #passw |
| Niveau 1 temps réel | ✅ onglet **Événements** + filtre groupe **« LSS (LPSA) (37) »** + filtre **« DEMARRAGE/ARRET (2) »** — 100+ points/lecture | — (pas de flux fiable → §5 : **VALIDÉ direct**) |
| Niveau 2 validé | ✅ onglet **Trajets** (ex. 44,8 km mesurés) | ✅ rapport **« Detail Trajet groupe de véhicules »**, exécuté camion par camion (13 camions = ~3 min) |
| Droits dans le moteur | ✅ 106 événements réels → 46 trajets PROVISOIRE → **34 remplacés / 70 créés / 0 doublon** | ✅ 11 trajets réels du jour entrés en **VALIDÉ** (distances exactes : 237 km, 215 km, 122 km…) |

Test d'ensemble (mode MIXTE) : les **deux boucles tournent en parallèle** dans le
serveur — collecte temps réel MZoneX + synchronisation validée MZoneX **et**
CamtrackPro au même rythme (15 min par défaut, réglable dans Paramètres).
Une synchronisation complète CamtrackPro (13 camions) dure ~3 minutes.

### Nouveautés de la version 1.7
- **`COLLECTOR_SOURCE=MIXTE`** (nouveau) : vos 37 camions MZoneX **et** vos 13
  camions CamtrackPro vivent ensemble dans la plateforme, comme prévu à l'audit.
- Collecteur **CamtrackPro réécrit en Playwright** (le navigateur déjà prévu pour
  MZoneX) : plus besoin de Selenium ni de Chrome. Une seule installation suffit.
- **Première synchronisation immédiate au démarrage** du serveur (plus besoin
  d'attendre 15 min pour voir la journée se remplir).
- Traitement anti-erreur : la lecture d'un rapport n'avance que si les lignes
  concernent bien le camion demandé (pas d'attribution croisée).
- Les dates « 31/07/2026 » (MZoneX) et « 2026-07-31 » (CamtrackPro), les virgules
  décimales et les « Km »/« km/h » sont tous reconnus tels quels.

### Mise à jour v1.6 → v1.7 (3 minutes)
1. Fenêtre noire du serveur ouverte → **Ctrl+C**.
2. Extraire le ZIP **dans** `E:\projets\LSS-Tracking-Plateforme` (« Remplacer tous
   les fichiers »). Une seule fois, ouvrir `powershell`, puis :
   ```powershell
   cd E:\projets\LSS-Tracking-Plateforme\backend
   python -m pip install playwright
   python -m playwright install chromium
   ```
3. Remplacer le contenu de `backend\.env` par le bloc donné dans le message de
   livraison (SIM_ENABLE=0, COLLECTOR_SOURCE=MIXTE + vos identifiants).
4. Double-clic `demarrer.bat` → la fenêtre noire doit afficher
   « Collecteur réel MIXTE activé » puis « Synchronisation Niveau 2 activée pour
   MIXTE ». Sur http://localhost:8000/Suivi : trajets MZoneX en **orange italique**
   (PROVISOIRE) qui passent au noir (VALIDÉ) ; camions CamtrackPro directement en noir.

---

## v1.8 (Addendum v1.5) — Conditions de Validité des Trajets et des Pauses ✅

La règle absolue du document est implémentée et **prouvée sur données réelles des
deux portails** : les horodatages d'un trajet **invalide** (< 0,3 km) n'entrent
**JAMAIS** dans le calcul TCC/TCJ/TTJ.

### Ce qui change concrètement
- **Trajet valide = distance ≥ 0,3 km** (`SEUIL_DISTANCE_MIN_TRAJET_KM`, Paramètres).
  En dessous → manœuvre locale : **pas de trajet créé**, rejet journalisé
  (`trajet.rejet_distance`) ; s'il existait déjà en provisoire (temps réel),
  il est marqué **REJETÉ** (conservé pour audit, masqué de toutes les grilles).
- **Pause valide = durée ≥ 20 min** (`DUREE_MIN_PAUSE_VALIDE` — le défaut passe
  de 15 à 20 min et les bases existantes sont migrées automatiquement).
  En dessous → **fusion** des deux trajets adjacents (durée = A + pause + B),
  en temps réel comme à la réconciliation.
- **CamtrackPro : double condition** §4.2 — distance ≥ 0,3 km **ET**
  « Durée En mouvement » ≥ 20 min (`SEUIL_DUREE_MIN_MOUVEMENT_TRAJET`), rejet
  journalisé `trajet.rejet_mouvement`.
- Nouveau champ `statut_validation` (EN_ATTENTE / VALIDE / REJETE) sur chaque
  trajet — migration automatique au démarrage (l'existant reste VALIDE).
- Calcul **TCC/TCJ/TTJ réécrit** : reconstruit sur la séquence brute — une plage
  rejetée est intégralement soustraite de l'amplitude sans créer ni perdre de
  pause; les trajets rejetés sont exclus de la grille Suivi, de l'Historique,
  des exports et de « Nb trajets ».
- Robustesse moteur : lecture des trajets par requête explicite (sessions
  longues), auparavant possible double création en fin de chaîne d'événements.

### Vérifié “pour de vrai” (31/07–01/08/2026)
- Portail **MZoneX réel** : 107 trajets lus → **68 manœuvres rejetées** (auditées),
  27 vrais trajets créés, **0 trajet < 0,3 km en base** ; fusions appliquées.
- Portail **CamtrackPro réel** : la colonne « Durée En mouvement » est lue et
  appliquée; le parseur a été validé sur les lignes réelles capturées.
- Suites automatisées : **27/27** (v1.4) et **32/32** (v1.5 : CA-1→7,
  CA-TCC-1→4, double condition §4.2, fusion idempotente CA-5).

### Mise à jour v1.7 → v1.8 (2 minutes)
1. Fenêtre noire du serveur → **Ctrl+C**.
2. Extraire le ZIP dans `E:\projets\LSS-Tracking-Plateforme` (« Remplacer tous »).
3. Double-clic `demarrer.bat` — la migration se fait toute seule au démarrage.
4. Vérifications visibles : Paramètres affiche 2 nouvelles lignes
   (`SEUIL_DISTANCE_MIN_TRAJET_KM = 0,3`, `SEUIL_DUREE_MIN_MOUVEMENT_TRAJET = 1200`)
   et `DUREE_MIN_PAUSE_VALIDE = 1200` ; la grille Suivi n'affiche plus les
   micro-déplacements (< 300 m) — les départs/arrêts réels sont inchangés.

---

## v1.8.1 — Réparation d'une journée construite avant la v1.8 (01/08/2026)

Constat sur vos captures : les micro-trajets et les fausses pauses (« 23 s »,
« 2 min »…) de la journée du 1ᵉʳ août ont été écrits **ce matin par la version
1.7** (le Niveau 2 recopiait chaque ligne du portail sans filtrer). La v1.8 ne
supprime jamais l'existant — il fallait donc un outil pour nettoyer.

**`backend/reparer_journee_v15.py`** (serveur arrêté, une seule fois) :
fusionne les ruptures < 20 min, rejette les trajets < 0,3 km, renumérote,
recalcule TCC/TCJ/TTJ. Idempotent (relance = 0 changement). La synchronisation
Niveau 2 suivante replace ensuite les horaires/distances officiels du portail.
Testé : 1 manœuvre-fusion rejetée, TCJ exact (4 h), relance sans effet.

    cd E:\projets\LSS-Tracking-Plateforme\backend
    python reparer_journee_v15.py            (ou : python reparer_journee_v15.py 2026-08-01)

Inclus aussi dans cette révision :
- **Anti-spam d'audit** : un trajet invalide revenant à chaque cycle de
  synchronisation n'est journalisé qu'une seule fois par jour.
- Deux calculs consolidés (lecture directe en base, fenêtre de travail bornée) :
  aucune pause ne compte avant le premier vrai trajet, aucune manœuvre rejetée
  ne sort de l'amplitude — suites v1.4 (27/27) et v1.5 (32/32) vertes.

## v1.9 — Dernière ligne du rapport = EN COURS, jamais « terminée » trop tôt (01/08/2026)

Retour métier sur vos captures (3046 TBS MZoneX, 5626 TCE CamtrackPro) :

- **CamtrackPro** : la dernière ligne du rapport « Detail Trajet » n'est **pas**
  un trajet terminé quand le véhicule roule encore — sa colonne « Fin » est la
  **dernière position connue**, pas une heure de fin officielle. Désormais,
  tant qu'une **pause ≥ 20 min** n'est pas constatée derrière, cette ligne est
  importée **PROVISOIRE / EN_ATTENTE** : **heure de fin PROVISOIRE** affichée
  (italique orange), **jamais déclarée terminée**, mise à jour à chaque
  synchronisation tant qu'elle grandit (même enregistrement, jamais de doublon).
  À sa clôture effective : ≥ 0,3 km **et** « en mouvement » ≥ 20 min → **VALIDÉ**
  (la fin du rapport devient alors l'heure de fin officielle), sinon **REJETÉ**.
- **MZoneX** : rappel du comportement appliqué (vos chiffres) — rejet d'abord
  (0,005 / 0 / 0,018 / 0,182 / 0,102 / 0,006 / 0 km), fusion ensuite : le
  09:49:00→10:04:35 (1,137 km) et le 10:12:11→10:14:55 (0,337 km), séparés par
  une pause de 7 min 36 s < 20 min, forment **un seul trajet 09:49 → 10:14
  (1,474 km)**. Pause officielle avant lui : 1 h 35 min 48 s après le 08:01 → 08:13.
- **Réouverture propre** : si la suite du rapport montre qu'un trajet déjà
  clôturé continuait en fait (pause < 20 min), le **même** trajet est rouvert
  puis refermé — tracé en audit (`trajet.reouverture`), aucun doublon.
- **Donnée tardive** : une fin/distance officielle plus complète arrivant après
  coup met à jour le trajet VALIDÉ existant (`trajet.valide_mis_a_jour`), au
  lieu de l'ignorer.
- **Restes de veille** : un trajet « en cours » jamais refermé la veille est
  clôturé automatiquement (définitif ou rejeté selon la règle 0,3 km).

Tests : nouvelle suite `backend/test_ouverture_v19.py` qui **rejoue vos deux
captures ligne par ligne** (**24/24**) + régressions v1.4 (**27/27**) et v1.5
(**32/32**) — **83 tests verts**.

### Mise à jour v1.8.1 → v1.9 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »). Vérifier que le fichier
   `backend\test_ouverture_v19.py` existe bien.
3. Relancer `demarrer.bat`.
4. Les trajets du jour se recalibrent tous seuls à la prochaine
   synchronisation (≤ 15 min) : le 5626, importé « terminé » trop tôt ce
   matin, repassera automatiquement **EN COURS** avec fin provisoire, puis
   sera validé à son vrai arrêt. Si l'écran du jour reste sale, rejouer une
   fois `python reparer_journee_v15.py` (serveur arrêté) puis `demarrer.bat`.

## v1.10 — Les vrais trajets des portails, toute la journée (03/08/2026)

Retour métier sur vos 3 cas (captures du 03/08) — **analyse et réparations** :

- **Cas 4296 TCC et 5716 TBS (CamtrackPro) — la plateforme lisait le MAUVAIS
  rapport.** CamtrackPro propose « Detail Trajet **groupe de véhicules** »
  (UNE ligne-synthèse par véhicule : premier départ du jour → dernière fin,
  kilomètres cumulés) et « Detail Trajet **Vehicule** » (une ligne par trajet,
  celui de vos captures). Le scraper exécutait le premier : c'est pourquoi le
  4296 affichait **un seul trajet noir 05:44 → 12:58** sans pause, et le 5716
  **une seule plage orange 05:05 → 13:37**. Désormais la plateforme exécute
  « Detail Trajet **Vehicule** » et **vérifie avant chaque import** que c'est
  bien ce rapport qui tourne — sinon la synchronisation CamtrackPro est
  interrompue par sécurité (journalisée) plutôt que de polluer l'écran.
- **Cas 0916 TBV (MZoneX) — les lignes du matin étaient évincées par le
  portail.** La grille « Trajets » tous véhicules de MZoneX est **plafonnée
  aux ~100 lignes les plus récentes** : l'après-midi, les trajets du matin en
  sortent et n'étaient plus jamais lus (votre plateforme ne connaissait du
  0916 que ce qui commençait à 09:07). Désormais la lecture se fait **véhicule
  par véhicule** (filtre « Rechercher véhicules »), exactement comme vous le
  faites à la main : la journée complète tient alors sur une page. Vérifié en
  réel : le 0916 remonte **14 lignes sur la journée entière**, dont celle de
  **05:53:09** qui manquait ; 218 trajets lus au total (ancien plafond ~107).
- **Bug annexe (corrigé)** : le menu « Rapports » de CamtrackPro met jusqu'à
  20 s à apparaître après la connexion ; cliqué trop tôt il pouvait être
  **refermé** par le clic suivant → un cycle CamtrackPro entier pouvait être
  silencieusement vide. La plateforme vérifie maintenant que le panneau est
  ouvert avant d'agir et refuse d'exécuter un rapport inattendu.

**Réparation automatique, sans rien faire :** à la première synchronisation
après la mise à jour (≤ 15 min), les mauvaises lignes déjà affichées sont
**corrigées en place** — aucune suppression, aucun doublon, chaque correction
est tracée dans l'audit (`trajet.fusion_historique`). Les grilles attendues :

- **4296 TCC** : T1 **05:44:08 → 08:29:50** (14,60 km) · pause **39 min 45 s**
  · T2 **09:09:35 → 12:58:30** (69,90 km) — la manœuvre de l'après-midi
  (≤ 0,55 km) est rejetée automatiquement.
- **5716 TBS** : T1 **05:29:39 → 09:47:54** (103,58 km) · pause **38 min 28 s**
  · T2 **10:26:22 → 13:21:08** (86,86 km) — les manœuvres (0,03 km le matin,
  ~1 km à 13:34, « en mouvement » < 20 min) sont rejetées ; le trajet
  d'après-midi suit le cycle EN COURS → VALIDÉ normal.
- **0916 TBV** : T1 **05:53:09 → 06:52:19** (24,115 km) · pause 20 min ·
  T2 **07:12:35 → 10:14:06** (72,035 km) · pause 37 min ·
  T3 **10:51:46 → 13:13:15** (55,551 km) — puis la suite de la journée
  s'ajoute normalement.

**Autres correctifs** : la version est maintenant **affichée** — ligne
« LSS Tracking v1.10 — Plateforme prête » dans la fenêtre noire du serveur et
**« v1.10 » en pied de page** de l'application (plus besoin de deviner quelle
version tourne). La grille « 500 lignes par page » de CamtrackPro est tentée
une fois par cycle pour les journées longues.

**Tests** : suite v1.9 **rejouant vos captures ligne par ligne** étendue à
**39/39** (absorption du fantôme 4296, rejet du fantôme 5716, fusion 0916) +
régressions v1.4 (**27/27**) et v1.5 (**32/32**) — **98 tests verts**, et les
deux scrapers re-validés **en réel** ce jour sur les deux portails.

**Point d'attention MZoneX** : les plaques 2226TBS et 2606TBS ne figurent pas
dans le groupe « LSS (LPSA) » du portail (aucune option proposée) — le journal
le signale ligne par ligne. Si ces camions roulent, demandez à votre
fournisseur GPS de les ajouter au groupe ; dites-le-nous sinon.

### Mise à jour v1.9 → v1.10 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »).
3. Relancer `demarrer.bat`.
4. **Vérifier la version** : la fenêtre noire affiche
   `LSS Tracking v1.10 — Plateforme prête`, et l'application affiche
   **« v1.10 » en bas de page** (après reconnexion si besoin).
5. Tout se recalibre **tout seul** à la prochaine synchronisation
   (≤ 15 min) : les trois cas ci-dessus reprennent leurs vraies valeurs,
   chaque correction est visible dans le journal d'audit. Aucun script à
   relancer.

## v1.10.1 — Diagnostic de version infaillible (03/08/2026)

Suite à votre retour « les résultats restent inchangés » : la v1.10 tournait
bien chez vous, simplement la **première synchronisation n'avait pas encore
eu le temps de tourner** (elle démarre au lancement mais dure ~10-20 min :
38 véhicules MZoneX un par un, puis 13 rapports CamtrackPro). Deux garde-fous
pour que ce cas de figure ne prête plus jamais à confusion :

- **Pied de page honnête** : l'application n'affiche la version que si le
  serveur la confirme. Si un ANCIEN serveur tourne encore (v1.9 sans version),
  le bandeau affiche désormais « ⚠ ANCIEN SERVEUR — relancez demarrer.bat »
  au lieu d'afficher la nouvelle version par défaut.
- **`demarrer.bat` anti-double serveur** : à chaque lancement, il ferme
  d'abord automatiquement tout ancien serveur resté branché sur le port 8000
  (ligne « Ancien serveur arrêté (PID …) » quand c'est le cas), puis démarre.
  Plus jamais de fenêtre noire ouverte par erreur sur une ancienne version.

### Mise à jour v1.10 → v1.10.1 (2 minutes) — facultative, recommandée
Identique : `Ctrl+C` (ou rien si vous relancez juste), extraire le ZIP
**par-dessus** `E:\projets\LSS-Tracking-Plateforme` (« Remplacer tous »),
relancer `demarrer.bat`. La fenêtre noire affiche alors
`LSS Tracking v1.10.1 — Plateforme prête`.

## v1.11 — Balai anti-fantômes (03/08/2026, suite à vos écrans de 16:13)

Vos 3 cas corrigés ont révélé rester des **lignes fantômes** autour d'eux :
trajets **imbriqués** dans un autre (T3 à l'intérieur de T2), **doublons** à
la même fin ou au même début, horaires **« fin avant début »** (13:06 →
12:17…) et pauses incohérentes (18 min affichées). Ce ne sont **pas** des
données des portails : ce sont des **écritures intermédiaires de la journée
construite par les anciennes versions**, que les rapprochements v1.10 (même
début / même fin) ne pouvaient pas rattraper puisqu'elles ne correspondent
à aucune ligne officielle.

- **Balai automatique à chaque synchronisation** : après chaque
  réconciliation, toute ligne de la journée qui est un **doublon** d'une
  ligne officielle, un **sous-intervalle strict** d'un trajet officiel, ou
  un horaire **corrompu** (fin avant le début) est **purgée de l'écran** —
  chaque purge est **conservée en audit** (`trajet.orphelin_purge`, avec la
  raison). Le **trajet moteur réellement en cours** (fin provisoire fraîche)
  est toujours épargné. Numérotation, pauses, TCC/TCJ/TTJ et archive sont
  recalculés dans la foulée.
- **Auto-réparant, sans script** : les lignes officielles reviennent
  complètes à chaque cycle, donc au premier cycle après la mise à jour
  (≤ 15-20 min) l'écran se nettoie tout seul — et un fantôme purgé ne peut
  plus réapparaître.
- **Garde-fou v1.11** : l'absorption « par la fin » refuse désormais tout
  rapprochement qui produirait un horaire « fin avant début ».
- Fiabilité interne : id d'un trajet nouvellement créé désormais connu avant
  l'audit (UUID généré à l'INSERT) ; le validateur simulé juge à l'horizon
  de la passe (suites reproductibles à toute heure).

Après le premier cycle, vos écrans deviennent : **0916** → T1 05:53→06:52,
T2 07:12→10:14, T3 10:51→13:13 (la T4 imbriquée disparaît) ; **0926 / 0936 /
2746 / 4736 / 4866 / 4926 / 5706 / 4006 / 8076 / 8086 / 7946 / 9806** → les
lignes imbriquées, doublons et horaires à l'envers disparaissent, les pauses
se recalculent. Les plages **orange « en cours »** qui restent sont normales
(règle v1.9 : jamais déclarées terminées tant qu'une pause ≥ 20 min n'est
pas constatée).

Tests : suite v1.9 portée à **49/49** (rejoue vos captures + le balai sur les
3 familles de fantômes) + v1.4 (**27/27**) et v1.5 (**32/32**) —
**108 tests verts**.

### Mise à jour v1.10.x → v1.11 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »).
3. Relancer `demarrer.bat` → vérifier `LSS Tracking v1.11 — Plateforme
   prête` dans la fenêtre noire et « v1.11 » en pied de page (Ctrl+F5 dans
   l'application si besoin).
4. Le balai agit **dès le premier cycle** (≤ 15-20 min) : regardez les lignes
   `Ligne fantôme purgée (…)` dans la fenêtre noire, puis rechargez
   l'écran — les grilles sont propres, sans doublon ni perte.

## v1.12 — Contrat grille strict (03/08/2026)

Votre cahier des charges affiché : pour chaque camion, la ligne doit se lire
**départ / fin / pause / début / fin / pause …** comme `5:33 / 9:30 / 0:35 /
10:05 / …`, toujours strictement croissante, chaque **pause = écart réel**
entre la fin d'un trajet et le début du suivant, et **jamais de pause
< 20 min affichée** (sinon les deux trajets devaient être fusionnés, §1.2) ;
jamais d'heures inversées, de trajets qui se recouvrent, d'heure répétée ni
de trou.

- **Correcteur structurel à chaque cycle** : après le balai v1.11, tout
  couple de lignes closes qui enfreint le contrat est corrigé en base —
  **fusion** si la pause est < 20 min (le trajet continue, distances
  cumulées), **purge** du recouvrement ou du résidu face à une ligne
  officielle. Règle d'or : **une ligne officielle (portail) n'est jamais
  mutée** — c'est toujours l'autre ligne qui disparaît — et **le trajet
  moteur en cours n'est jamais touché**. Chaque correction est visible en
  audit (`trajet.structure_corrige`) et dans la fenêtre noire
  (`Structure corrigée (…)`).
- **Pause du dernier trajet** : remise à zéro systématique — fini les
  reliquats (« pause 33 min » alors que l'écart réel en fait 89).
- Exemple de référence (0906 TBV, vos captures) : rapport CamtrackPro de
  16 lignes dont 14 manœuvres < 0,3 km → **écran : 05:24 → 09:09 · pause
  6h18 · 15:28 → en cours** — déjà conforme au contrat, c'est le modèle.
- Sont **normaux**, pas des erreurs : les lignes totalement vides (camion
  sans trajet valide aujourd'hui, ex. 5156/5186/8116), une ligne à un seul
  trajet (3046 : 00:12→00:47), et les plages **orange « en cours »** (jamais
  déclarées terminées avant une pause de 20 min constatée).

Tests : suite rejouant vos captures portée à **57/57** (+ le contrat strict
appliqué aux cas réels) + v1.4 (**27/27**) et v1.5 (**32/32**) —
**116 tests verts**.

### Mise à jour v1.11 → v1.12 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »).
3. Relancer `demarrer.bat` → vérifier `LSS Tracking v1.12 — Plateforme
   prête` et « v1.12 » en pied de page (Ctrl+F5 si besoin).
4. Au premier cycle (≤ 15-20 min), la fenêtre noire affiche
   `Ligne fantôme purgée (…)` puis `Structure corrigée (…)` : rechargez
   l'écran — chaque ligne de camion est alors strictement
   début/fin/pause/début/…, et le restera à chaque cycle.

## v1.13 — « Pourquoi ces résultats ? » : la réponse et la réparation (04/08/2026)

Vos captures du 04/08 au matin montraient deux anomalies, par exemple :

- **4526TCC** : `05:56 → en cours` (orange) **puis** `05:56 → 08:21` (noir) —
  deux lignes pour UN trajet, l'orange devant le noir ;
- **0916TBV / 5316TBU** : le début en orange « en cours » alors que le noir
  affichait déjà le même trajet ;
- **9856TCD** : une « pause 30 min » qui n'a jamais existé (des manœuvres de
  2-8 min interrompaient l'arrêt en coulisses).

**Pourquoi ?** Deux causes précises, trouvées et corrigées :

1. **Le jumeau invisible.** Quand la plateforme détectait un trajet en
   direct (orange, pas encore de fin), puis que la ligne officielle du
   portail arrivait, le rapprochement ne regardait que les trajets *déjà
   terminés* : il ne reconnaissait donc pas son propre trajet en cours et
   créait un doublon noir **au même début**. L'orange, lui, restait collé à
   l'écran.
   → *Réparation v1.13* : le rapprochement accepte le trajet en cours au
   même début (**unicité par début** : un même début = un seul trajet), et
   un balai **fusionne automatiquement les jumeaux déjà écrits** — votre
   écran se guérit tout seul au premier cycle (≈ 15-20 min).
2. **Le noir prématuré.** Les heures du portail passaient en noir dès leur
   lecture, sans exiger la **pause ≥ 20 min constatée derrière** ni la
   **fusion des écarts < 20 min**. D'où le noir sur un trajet pas vraiment
   terminé (4526 reparti 4 min après « sa » fin 08:21) et les pauses
   fantaisistes.
   → *Réparation v1.13* : l'écran, les exports et l'historique lisent
   désormais la **journée CHAÎNÉE** : les écarts < 20 min sont réellement
   fusionnés, les manœuvres (< 0,3 km) soudent la chaîne sans jamais
   former de ligne, et **le noir n'arrive que lorsque le trajet est terminé
   ET qu'une pause ≥ 20 min est constatée derrière**. Tant que ce n'est
   pas le cas : orange, et durée provisoire. TCC/TCJ/TTJ comptent la même
   chose que l'écran (**règle absolue préservée** : jamais de manœuvre dans
   les compteurs).

Si le camion est encore en train de courir après la fin publiée par le
portail (cas 4526 : reparti à 08:25), la ligne officielle est **couverte** :
elle refermera le trajet en cours à la vraie fin au cycle suivant — jamais
de doublon, jamais de noir prématuré.

Tests : **41** tests spécifiques v1.13 (rejouant vos quatre camions) +
**16** tests de bout en bout avec les lignes réelles du portail, toutes les
autres suites revalidées — **173 tests verts** au total.

## v1.14 — Addendum v1.8 : jour du début, pré-consolidation de 02h00, durée en temps réel (04/08/2026)

Vos trois nouvelles règles, livrées :

1. **Un trajet appartient au jour de son HEURE DE DÉBUT** (bascule à
   **02h00** au lieu de minuit). Exemples :
   - 23h40 → 00h30 : trajet **d'HIER** (il était commencé hier) ;
   - 00h15 → 01h05 : **d'HIER** aussi (tout ce qui commence entre 00h00 et
     01h59 appartient encore à la journée logistique d'hier) ;
   - 02h15 → 03h00 : **d'AUJOURD'HUI**.
   *Choix d'interprétation (à valider par vous)* : là où le tableau §1.2 du
   document indique « 00h15 → nouveau jour », j'ai suivi le §4.2 + §5 du
   même document (00h00-01h59 → veille), qui est aussi ce que vérifient
   vos critères d'acceptation CA-1/CA-2/CA-3. Dites-moi simplement si vous
   préférez l'autre interprétation : c'est un réglage d'une ligne.
2. **Pré-consolidation automatique à 02h00** : tous les trajets de la
   journée qui se termine passent **OFFICIELS** ; un trajet encore « en
   cours » à 02h00 est **arrêté à 02h00** et rangé dans la journée d'hier.
   L'historique d'hier est ainsi **100 % officiel, 0 % orange** — plus
   rien ne reste « en attente » d'un jour à l'autre. L'archivage complet
   (cycle de journée) se fait à la même heure de 02h00, pas à minuit.
3. **Durée en temps réel au lieu de « en cours»** : pour le trajet qui
   roule, la colonne Fin affiche sa **durée depuis le début** (ex. `2:34`),
   mise à jour chaque minute à l'écran ; en orange tout temps qu'il n'est
   pas officiel, et **rouge avec ⚠** quand la conduite continue approche la
   limite TCC. Dans les exports Excel/PDF, la case Fin porte aussi la durée
   au format H:MM (jamais le texte « en cours »).
   Formats partout cohérents : heures `HH:MM` (05:42), durées `H:MM`
   (`4:30`, `0:35`).

Réglage : heure de bascule configurable (`APP_BASCULE_JOUR_H`,
**02h00** par défaut).

Tests : nouvelle suite Addendum v1.8 (**26 vérifications : CA-1 à CA-6**)
rejouant un trajet réel à cheval sur minuit, la nuit multi-trajets, la
pré-consolidation, l'historique 0 % orange et les formats — **toutes les
suites revalidées : 199 tests verts** (chaînes 41, validité 32,
réconciliation 27, ouverture 57, E2E réel 16, Addendum v1.8 26).

### Mise à jour v1.12 → v1.14 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »).
3. Relancer `demarrer.bat` → vérifier `LSS Tracking v1.14 — Plateforme
   prête` et « v1.14 » en pied de page (Ctrl+F5 si besoin).
4. **Vos lignes d'aujourd'hui se réparent toutes seules** au premier cycle
   (≈ 15-20 min) : les doublons « orange puis noir au même début » sont
   fusionnés (fenêtre noire : `Jumeau fusionné…`), le noir n'apparaît
   qu'après une vraie pause de 20 min, et dès 02h00 du matin la journée
   d'hier devient entièrement officielle.

### Votre logo LSS est intégré (04/08/2026)
- **Écran de connexion** et **barre latérale** : votre logo sur vignette
  blanche ; **onglet du navigateur** : favicon LSS.
- **Exports Excel** : le logo figure en haut à gauche du bandeau-titre de
  chaque feuille (Suivi Journalier, Historique, Synthèse, Infractions…).
- **Exports PDF** : logo dans le bandeau bleu de chaque page et sur la page
  de garde de l'Historique.
- Rien à faire : la procédure de mise à jour ci-dessus suffit (le premier
  démarrage installe juste le petit complément « Pillow » tout seul,
  ~20 secondes, pour insérer le logo dans les fichiers Excel).

### Session de 7 jours (05/08/2026)
À votre demande, la connexion dure désormais **7 jours** au lieu de 12 h :
vous ne vous reconnectez qu'environ **une fois par semaine**. Les lignes
`401 Unauthorized` que vous aviez vues dans la fenêtre noire étaient
simplement l'ancien jeton de 12 h expiré (comportement normal de sécurité).
Réglage : `TOKEN_TTL_MIN=10080` (modifiable si besoin).

## v1.45 — Positions 18h/20h/22h EXACTES (historique des portails) & noms de lieux resserrés (01/09/2026)

**Pourquoi** : deux défauts constatés sur la grille du 31/08 (capture à l'appui) :
(1) les 3 cases 18h/20h/22h affichaient la **même** position — le serveur étant
éteint le soir, la v1.44 complétait le matin avec le dernier point connu avant
l'arrêt (~17h) ; (2) des noms de lieux trop généralisés (« proche COLAS PK13 »,
communes « -CC ») au lieu des vrais noms d'endroits.

**Ce qui change pour vous** :

1. **Positions du soir désormais prises dans l'historique des portails** —
   MZoneX (onglet Événements) et CamtrackPro (messages Wialon) gardent TOUT,
   même quand la plateforme est éteinte. Pour chaque heure (18h, 20h, 22h),
   la plateforme retient le **dernier point enregistré avant l'heure** —
   exactement ce que le portail affiche. Un camion roulant après 18h a
   désormais **3 positions différentes et vraies** ; un camion garé (arrêté à
   19h30) montre le même point à 20h et 22h — c'est la vérité terrain.
   - de même, dès **22h05** (si le serveur tourne), la passe du soir écrit la
     version portail définitive du jour avant le verrouillage de minuit ;
   - chaque matin, le serveur rattrape la veille (et jusqu'à 7 jours en
     arrière) automatiquement, sans rien vous demander.
2. **Réparation automatique des journées déjà fausses** : toute ligne dont les
   3 cases sont identiques (ex. hier 31/08) est recalculée via les portails et
   corrigée **au premier démarrage** v1.45, avec une trace dans le journal
   d'audit (`suivi.position_reparee`, avant → après). Les autres lignes ne
   bougent pas ; **rien n'est jamais effacé** ; les archives du jour sont
   régénérées et auditées comme d'habitude.
3. **Noms de lieux resserrés** (onglet « Lieux » de MZoneX + onglet « Zones »
   de CamtrackPro, comme demandé) : le lieu **précis** le plus proche (mesuré
   à son centre, à moins de 3 km) gagne désormais face aux grandes zones de
   ville automatiques (« -CC », « -TOWN ») — celles-ci ne servent plus qu'en
   dernier recours. Quand le camion n'est pas exactement dedans, l'affichage
   reste « proche ‹nom› » (ex. « proche Ambatosonegaly »). Example vérifié :
   le point PK13 de votre capture devient « proche Jovenna By Pass (ENC) ».
   💡 Un lieu qui manque (ex. « Ampasimadinika », absent des deux portails au
   01/09) peut être ajouté dans l'onglet Lieux de MZoneX ou Zones de
   CamtrackPro : il sera utilisé automatiquement dès le cycle suivant (≤ 6 h).
4. **CamtrackPro web sur votre PC** : la cause est prouvée technique­ment
   (chaque ouverture de session API par la plateforme déconnecte la session
   précédente). **Vous avez choisi de ne rien changer** à la collecte :
   pour consulter le portail web sans gêne, utilisez un autre poste/téléphone,
   ou arrêtez temporairement la plateforme (Ctrl+C dans la fenêtre noire) le
   temps de la consultation, puis relancez `demarrer.bat`.

**Installation** : ZIP à extraire dans `E:\projets\LSS-Tracking-Plateforme`
(votre base `backend\data\lss.db` n'est **jamais** dans le ZIP — elle reste
intouchée), relancer `demarrer.bat`, Ctrl+F5 : bandeau **v1.45** (page de
connexion et barre latérale).

---

## v1.44 — TCC masqué en historique & positions automatiques 18h/20h/22h (31/08/2026)

**Pourquoi** : deux ajustements demandés après mise en route de la v1.43.
(1) La colonne **TCC** dans l'Historique compliquait les explications en
audit : c'est un chrono **temps réel** (remise à zéro après toute pause ≥
30 min), il n'a pas de sens sur des trajets déjà terminés. (2) Les relevés
du soir étaient à saisir à la main alors que la plateforme connaît déjà les
positions réelles.

**Les 2 chantiers arbitrés** (§0vicies decies — toutes vos réponses gravées) :

- **TCC affiché « 0:00 » hors temps réel (N1)** : la colonne TCC reste
  présente (structure identique partout), mais ses cellules affichent
  **« 0:00 »** dans l'Historique (grille d'une journée, liste mensuelle,
  exports Excel/PDF, synthèse) et dès que vous consultez un **jour passé**
  dans le Suivi. La journée **en cours** garde le chrono vivant, inchangé.
  Rien n'est effacé : la vraie valeur reste en base (masquage à l'affichage).
- **Colonnes « Pos. 20h » et « Pos. 22h » + positions automatiques (N2/N3)** :
  la grille gagne 2 colonnes après « Pos. 18h » (écran = exports = archives ;
  25→27 colonnes en compact, 51→53 en détaillé). Les cellules **18h, 20h et
  22h se remplissent toutes seules** dès que l'heure est passée, avec la
  **dernière position connue du camion à cette heure-là** — celle que le
  portail affiche même quand le boîtier n'émet plus — nommée comme sur la
  carte (zone du portail, sinon « proche ‹nom› »). Si un boîtier muet remonte
  ses données tard, la cellule **se corrige d'elle-même jusqu'à minuit** ;
  et les cellules vides des **7 derniers jours** se complètent aussi, avec
  trace d'audit (jamais une cellule remplie n'est écrasée). Les colonnes
  08h→16h restent 100 % manuelles (le bouton « Pré-remplir (GPS) » reste
  disponible comme aide).

**Preuve** : suite `test_positions_tcc_v144.py` (**42 vérifications** :
migration sur votre base existante incluse) + batterie complète
**34 suites — 939 OK / 0 KO**. Réalignements de suites déclarés en
CONFORMITÉ (indices de colonnes +2, palier de version, coïncidence de
calendrier dans une ancienne suite Ym@ne — aucun n'affecte le code livré).

**Pour mettre à jour :** comme d'habitude — Ctrl+C dans la fenêtre noire,
extraire le ZIP, « Remplacer tous » (vos données `backend\data\lss.db` ne
sont jamais dans le ZIP), relancer `demarrer.bat`, Ctrl+F5 : **v1.44**.
Dès ce soir 18h, les trois relevés se remplissent seuls ; l'Historique
affiche « 0:00 » dans la colonne TCC.

## v1.43 — Boîtiers muets : jamais de données perdues (rattrapage automatique, 29/08/2026)

**Pourquoi** : trois comportements signalés par captures — (1) un camion
« arrête » ses données en cours de journée (2746TCC figé à 10:20 le 28/08) ;
(2) la veille au soir des lignes incomplètes qui reviennent complètes le
lendemain ; (3) des lignes DÉFINITIVEMENT vides alors que le portail a les
trajets (3056TBS : 9, 0936TBV : 11 le 28/08). **Diagnostic mesuré en direct
sur les portails** : le boîtier GPS, en zone sans couverture réseau (route
de Brickaville), garde ses positions en tampon et les renvoie en différé.
La plateforme ne perdait rien : elle ne relisait simplement **jamais un jour
passé une fois archivé**. Cycle de collecte mesuré : ~30 s (rien à corriger
côté cadence).

**Les 4 chantiers arbitrés** (§0nonies decies) :

- **Relecture des jours passés (M1)** : à chaque cycle (15 min), la plateforme
  relit **aujourd'hui ET hier** ; toutes les heures, les **7 derniers jours**.
  Tout tampon boîtier remonté tard au portail est rattrapé **tout seul**, et
  la grille archivée du jour se régénère en place avec une trace d'audit
  (« archive.raffraichie », avant → après). **Effet immédiat après
  installation : les lignes vides du 28/08 (3056TBS, 0936TBV) se complètent
  au premier cycle.**
- **Découpage des géants (M2)** : un trajet provisoire géant (10:20→18:11)
  fabriqué pendant le silence du boîtier est redécoupé par la série officielle
  (10:20→13:06, pause 1:11, 14:17→18:11) ; tout reste douteux est marqué
  REJETÉ avec audit, jamais supprimé.
- **Fenêtre temps réel élargie à 3 h (M3)** : un tampon remonté avec moins de
  3 h de retard complète la journée **en cours**, quasi en direct.
- **Repère « boîtier muet — données en transit » (M4)** : dans le Suivi,
  badge orange dès qu'un camion n'a rien émis depuis 30 min — vous distinguez
  enfin la zone blanche (normal) de la vraie panne, et l'audit garde la trace
  une fois par camion et par jour.

**Preuve** : suite `test_boitiers_muets_v143.py` (32 vérifications) qui
**rejoue mécaniquement le scénario du 2746TCC** (géant redécoupé en vérité
portail, pauses 0:35 / 1:11 retrouvées) + batterie complète
**32 suites — 897 OK / 0 KO** + test réel sur données portails.
Réalignements déclarés en CONFORMITÉ (3 suites anciennes mises au pas de la
nouvelle loi). **2736TCC et 3046TBS n'avaient AUCUN trajet le 28/08 aux
portails : leurs lignes vides étaient correctes (camions au repos).**

**Pour mettre à jour :** comme d'habitude — Ctrl+C dans la fenêtre noire,
extraire le ZIP, « Remplacer tous » (vos données `backend\data\lss.db` ne
sont jamais dans le ZIP), relancer `demarrer.bat`, Ctrl+F5 : **v1.43**.
Laissez tourner ~15 minutes : le premier cycle profond (J-7 → J) complète
lui-même les journées abîmées.

## v1.42 — Onglet Historique : barres de défilement restaurées (correctif, 29/08/2026)

**Pourquoi** : sur l'onglet Historique, la grille du jour était **coupée à
droite sans barre horizontale** et le bas de page était **inaccessible sans
barre latérale** (régression de la v1.41 : l'astuce « une seule zone de grille
plein écran » ne convient pas à une page qui empile plusieurs sections — la
mise en page les écrasait entre elles, barres comprises).

**Le correctif** (gravé dans `REFERENCE_IA_REGLES.md`, §0octies decies
« Mise au point du 29/08/2026 » — aucune donnée, colonne ni format ne change) :

- **Historique** : le défilement vertical **de la page** revient (barre
  latérale à droite, toujours là) ; la grille du jour et le tableau de synthèse
  gardent chacun leur propre zone de défilement bornée (72 % / 60 % d'écran)
  avec leurs **deux barres** — la barre horizontale reste visible sans
  descendre en bas ;
- **Paramètres** : même correctif par anticipation (même défaut structurel) —
  « Situations » et « Seuils » défilent avec la page, « Utilisateurs » et
  « Journal d'audit » restent bornés à 60 % d'écran avec leurs barres ;
- **Inchangé** : Suivi Journalier, Infractions, Véhicules, Conducteurs,
  Conduite (une seule zone de données → le comportement v1.41 était déjà
  correct).

Preuve : suite `test_historique_scroll_v142.py` (18 vérifications) +
batterie complète **31 suites — 865 OK / 0 KO** + capture d'écran réelle
(vraie interface, barres visibles). La suite v1.41 a été réalignée sur 4 pages
au lieu de 6 (Historique/Paramètres ≈ desormais couverts par la suite v1.42) —
réalignement déclaré.

**Pour mettre à jour :** comme d'habitude — extraire le ZIP, « Remplacer
tous » (vos données `backend\data\lss.db` ne sont jamais dans le ZIP), relancer
`demarrer.bat`, Ctrl+F5 : **v1.42** au bas de la connexion. Ouvrez l'onglet
Historique : la barre latérale de page est de retour, et la grille du jour a
sa barre horizontale.

## v1.41 — Barre de défilement toujours visible + filtre par type + période des infractions (27/08/2026)

**Pourquoi** : trois demandes du jour — (1) sur Infractions et Véhicules, il
fallait descendre tout en bas du tableau pour pouvoir glisser vers la droite ;
(2) il manquait un filtre par type d'infraction ; (3) pour les infractions
liées au temps de conduite (repos journalier/hebdomadaire, conduite de nuit,
conduite journalière…), il fallait voir **à quelle heure l'infraction a
commencé et fini** (ex. repos « arrêt 23:00 → reprise 07:30 » alors que le
minimum est 9:00).

**Quoi (arbitrages L1→L3 du 27/08/2026, inscrits dans la loi avant codage)** :

- **L1 — Toutes les grilles se comportent comme le Suivi Journalier** : le
  tableau défile en interne à hauteur d'écran, la barre horizontale est
  **toujours visible** sans descendre en bas (Infractions, Véhicules,
  Conducteurs, Historique, Conduite, Paramètres).
- **L2 — Deux colonnes « Début de l'infraction » et « Fin de l'infraction »**
  sur l'onglet Infractions (amendement dicté de la règle I3, votre choix) :
  les heures exactes publiées par Ym@ne, toutes familles — une fin peut être
  le lendemain (repos « 23/08/2026 22:30:47 → 24/08/2026 06:16:23 »). Les
  lignes déjà importées se **complètent toutes seules** à la prochaine
  collecte (rien à faire) ; « — » si le portail ne publie rien, jamais
  d'invention.
- **L3 — Nouveau filtre « type d'infraction »** (liste des familles
  présentes dans vos données : Excès de Vitesse, Conduite de Nuit, Temps de
  Repos Journalier…) — actif aussi sur les **exports Excel/PDF**, qui passent
  à 12 colonnes alignées sur l'écran (écran = export).

**Recettes** : nouvelle suite `test_infractions_periode_v141.py`
(**27 vérifications** — fin verbatim franchissant minuit, « — » jamais
d'invention, rattrapage upsert sans doublon sans toucher vos validations,
export 12 colonnes, filtre exact + endpoint des familles triées + exports
filtrés, mise en page pleine hauteur sur tous les onglets à grille — la
suite v1.35 a été réalignée sur le format 12 colonnes AMENDÉ, déclaré).
**Batterie complète : 30 suites, 849 OK / 0 KO.**

**Installation** : comme d'habitude — Ctrl+C, « Remplacer tous »,
`demarrer.bat`, Ctrl+F5 : **v1.41** au bas de la connexion. Ouvrez
l'onglet Infractions : la barre horizontale est visible d'emblée, le
filtre « Tous types d'infraction » est en haut, et les colonnes Début/Fin
se rempliront d'elles-mêmes (cliquez « Relancer la collecte maintenant »
pour les voir immédiatement sur les lignes déjà présentes).

---

## v1.40 — Collecte Ym@ne : correction du plantage « requests » (27/08/2026)

**Pourquoi** : au clic sur « Relancer la collecte maintenant », vous avez eu
l'erreur 500 et, dans la fenêtre noire, `ModuleNotFoundError: No module
named 'requests'`. Le collecteur Ym@ne était écrit avec la petite
bibliothèque externe **requests**, qui n'existe pas dans votre Python —
le `pip install` du lanceur l'a apparemment manquée en silence. C'est pour
cela que rien ne remontait **depuis le début**, et que même la nouvelle
alerte v1.39 ne pouvait pas partir : le cycle plantait avant.

**Quoi** : le collecteur Ym@ne est réécrit avec **urllib**, la brique
intégrée à Python — la même que vos collecteurs trajets, qui fonctionnent
chez vous. **`requests` est supprimé des dépendances : plus rien à
installer, plus jamais ce plantage.** Le bouton est blindé : même dans un
cas imprévu, il affiche un message français au lieu d'une erreur 500.
Mise au point gravée dans la loi (§0septies decies) ; règles K1→K3
inchangées.

**Installation** : comme d'habitude — Ctrl+C, « Remplacer tous »,
`demarrer.bat`, Ctrl+F5 : **v1.40** au bas de la connexion. Puis onglet
**Infractions → « Relancer la collecte maintenant »** : les infractions
des 8 derniers jours doivent apparaître (~155 attendues ce jour).
**Recettes** : batterie complète à 0 KO ; branchement réel re-vérifié.

---

## v1.39 — Collecte Ym@ne : fin des échecs invisibles + bouton « Relancer la collecte » (27/08/2026)

**Pourquoi** : vous nous avez signalé ce matin que l'onglet Infractions
restait à zéro. Vérification faite, le portail Ym@ne est alimenté (201
lignes sur les 8 derniers jours, dont 155 importables — 103 alarmes et 52
alertes) : c'est la collecte sur **votre poste** qui n'aboutit pas. Or un
échec de collecte Ym@ne ne laissait **aucune trace dans l'application** —
il fallait lire la fenêtre noire pour s'en apercevoir. Inacceptable.
**Rappel** : la collecte doit aussi être activée dans `backend\.env`
(ligne `YMANE_ACTIVE=1` ; ajoutez-la si elle est absente, puis relancez).

**Quoi (arbitrages K1→K3 du 27/08/2026, inscrits dans la loi avant
codage)** :

- **K1 — Un échec Ym@ne déclenche désormais une ALERTE visible** (onglet
  Alertes, gravité moyenne) qui dit la raison en français : « site Ym@ne
  injoignable — réseau, DNS ou pare-feu de ce poste », « identifiants
  refusés par Ym@ne », « réponse illisible »… **Une seule alerte à la
  fois** : tant que le problème dure, elle est simplement mise à jour
  (nombre d'échecs, dernier essai), **jamais de rafale**. Et dès que la
  collecte refonctionne, **l'alerte se referme toute seule** (« Traitée »,
  rétablissement tracé dans le journal d'audit) — vous n'avez rien à
  faire. Collecte désactivée (YMANE_ACTIVE≠1) = silence voulu : ni alerte
  ni écriture, votre configuration reste souveraine.
- **K2 — Bouton « Relancer la collecte maintenant »** (onglet Infractions,
  rôles Administrateur et Suivi) : relance immédiate, sans attendre le
  cycle automatique de 15 minutes ni redémarrer. Un message vous dit le
  résultat (« N lignes lues — X nouvelles… ») ou la raison de l'échec.
  Rejouer n'abîme rien : l'anti-doublon fait foi. Deux collectes ne
  tournent jamais en même temps (refus propre : « une collecte est déjà
  en cours »). Chaque relance est tracée au journal d'audit.
- **K3 — Ce qui ne change pas (vos refus, gravés)** : pas de ligne de
  statut permanente sur l'écran ; pas d'interrupteur dans Paramètres —
  l'activation reste dans `backend\.env` ; cadence inchangée (cycle de
  15 minutes adossé à la synchronisation des trajets).

**Ce que vous verrez** : si la collecte échoue sur votre PC, une alerte
« Collecte Ym@ne en échec » apparaît (elle disparaîtra seule dès que ce
sera réparé) ; et si `YMANE_ACTIVE=1` est bien présent dans votre
`backend\.env`, le bouton ramène les infractions des 8 derniers jours en
un clic.

**Recettes** : nouvelle suite `test_ymane_observabilite_v139.py`
(**35 vérifications** — création/anti-rafale/mise à jour de l'alerte,
statut jamais rétrogradé, fermeture automatique à la guérison sans
effacement, raisons françaises des 5 familles d'erreurs, import réel d'une
ligne, silence souverain désactivé, verrou de passe, endpoint : 403
consultation / 409 conflit / audit au nom de l'opérateur / message
désactivé). **Batterie complète : 29 suites, 822 OK / 0 KO.**

**Installation** : comme d'habitude — Ctrl+C sur la fenêtre noire,
extraire le ZIP « Remplacer tous », relancer `demarrer.bat`, Ctrl+F5 :
v1.39 au bas de l'écran de connexion. Vérifiez ensuite `backend\.env` :
la ligne `YMANE_ACTIVE=1` doit y figurer. Puis onglet Infractions →
**« Relancer la collecte maintenant »**.

---

## v1.38 — Référentiel chauffeurs débarrassé des doublons (bug des accents corrigé) (27/08/2026)

**Pourquoi** : vous avez constaté ce matin que **MICHAËL Justin** était
dupliqué des centaines de fois (2 118 fiches chauffeurs au total) et que
l'alerte « Nouveau chauffeur détecté » partait en rafale. Cause racine
trouvée et prouvée : le garde-fou anti-doublon comparait les noms avec une
fonction de la base qui ne « comprend » que les lettres sans accent —
**tout nom accentué (É, È, Ë, À…) n'était jamais reconnu comme existant,
donc une nouvelle fiche était créée à chaque relevé**, pour MICHAËL et
tous les chauffeurs accentués.

**Quoi (arbitrages J1→J4 du 27/08/2026, inscrits dans la loi avant
codage)** :

- **J1 — Un chauffeur = une fiche, quelle que soit la forme du nom.**
  L'anti-doublon compare désormais la **forme canonique** du nom (sans
  accents ni casse : « MICHAËL Justin » = « MICHAEL JUSTIN » =
  « Michaël  Justin », y compris les formes invisibles du texte Unicode).
  Le même garde-fou s'applique à l'écran : créer ou renommer vers un nom
  équivalent existant est refusé avec un message clair. Une **contrainte
  d'unicité en base** verrouille le tout définitivement.
- **J2 — La fiche qui survit est la plus COMPLÈTE** (celle ayant un
  véhicule affecté, un téléphone ou un matricule renseigné, puis la plus
  ancienne) — jamais de travail de saisie perdu. Toutes les références
  (véhicule affecté, chauffeur de jour, badges de trajets, missions,
  alertes, infractions) sont rattachées au bon chauffeur. Vos archives ne
  sont pas retouchées (elles ont leur propre texte, rien ne change à
  l'écran Historique).
- **J3 — Les fiches doublons sont supprimées, UNE FOIS, avec traçabilité
  intégrale** (votre choix) : chaque fiche supprimée est d'abord consignée
  en entier dans le journal d'audit (nom, matricule, téléphone, gardien
  choisi, références rattachées). C'est une **exception ponctuelle** à
  notre règle « jamais de suppression », gravée comme telle dans la loi ;
  ailleurs elle reste en vigueur. La réparation s'exécute **toute seule au
  prochain démarrage** sur votre PC.
- **J4 — Les alertes fantômes de la rafale sont clôturées d'un coup**
  (marquées « Traitée », motif tracé), l'écran Alertes redevient lisible.
  Une alerte « Nouveau chauffeur » ne partira désormais que pour un
  chauffeur réellement nouveau.

**Ce que vous verrez au prochain démarrage** : dans la fenêtre noire, une
ligne « Réparation v1.38 … N fiche(s) en doublon fusionnée(s) » ; dans
Conducteurs, **une seule fiche MICHAËL Justin** ; dans Alertes, une alerte
récapitulative bleue « Réparation automatique des fiches chauffeurs » et
les alertes de la rafale passées en « Traitée ».

**Recettes** : nouvelle suite `test_conducteurs_dedoublonnage_v138.py`
(**21 vérifications** — variantes casse/accents/Unicode invisibles toutes
reconnues, borne 409 à l'écran, gardien « le plus complet », rebranchements
des 6 familles de références, consignation intégrale avant suppression,
archives intactes §A.2, idempotence, index UNIQUE, prouvé sur reproduction
exacte du scénario des captures). **Batterie complète : 28 suites, 784 OK /
0 KO.**

**Installation** : comme d'habitude — Ctrl+C sur la fenêtre noire,
extraire le ZIP « Remplacer tous », relancer `demarrer.bat`, Ctrl+F5 :
v1.38 au bas de l'écran de connexion. La réparation se fait à ce
démarrage-là ; comptez une minute de plus si vous aviez beaucoup de
doublons.

---

## v1.37 — Infractions Ym@ne : relecture sur 8 JOURS (aucune ligne tardive perdue) (26/08/2026)

**Pourquoi** : retour d'expérience le jour même de l'activation — « le
traitement d'une infraction peut prendre plus de 12 h dans Ym@ne : les
infractions d'hier remontent aujourd'hui à partir de 15 h ». La fenêtre de
relecture J-1→J de la v1.36 pouvait donc manquer des lignes apparues en
retard. Vous avez aussi précisé le vocabulaire métier des familles de
repos (conduite de nuit 18:00→05:30, repos journalier 09:00 minimum,
repos hebdomadaire 24:00 après TCH 56:00, remise à zéro du TCH après 24 h
sans trajet ≥ 0,3 km).

**Quoi (arbitrages A-I5/A-I6 du 26/08/2026, inscrits dans la loi avant
codage)** :

- **A-I5 — Fenêtre de relecture gravée : J-8 → J à chaque cycle 15 min**
  (l'ancienne fenêtre J-1→J est abrogée). Toute infraction qui apparaît
  dans Ym@ne avec du retard — même plusieurs jours après les faits — est
  **rattrapée automatiquement**. L'anti-doublon par identifiant Ym@ne rend
  la relecture sans effet sur l'existant, et une décision Valide/Invalide
  déjà prise n'est jamais défaite. Vérifié en direct sur le portail : la
  fenêtre de 8 jours retourne ~215 lignes brutes, intégralement absorbées
  par le cycle (cohérent avec votre observation « > 12 h » : il y a des
  lignes du 17/08 remontées bien plus tard).
- **A-I6 — Repos/nuit : Ym@ne reste souverain, aucun calcul interne.**
  Les infractions de conduite de nuit, de temps de repos journalier et de
  temps de repos hebdomadaire sont importées **telles quelles** avec leur
  seuil verbatim (« 18:00:00 to 05:30:00 », « 09:00 », « 24:00 » si un
  jour elle apparaît) — la plateforme ne recalcule pas la règle TCH. (Si
  un jour vous voulez un compteur TCH interne, il suivra le chauffeur tous
  véhicules confondus — noté dans la loi.)
- **Rien d'autre ne change** : 10 colonnes, workflow de validation,
  exports, aucune suppression.

**Recettes** : suite `test_ymane_branchement_v136.py` alignée sur
l'amendement (**33 vérifications** — nouvelle preuve : une ligne datée
d'il y a exactement 8 jours EST rattrapée par le cycle ; requête
`startdate=J-8` vérifiée à la lettre). **Batterie complète : 27 suites,
763 OK / 0 KO** + essai de fumée réel (cycle forcé sur le vrai portail :
fenêtre J-8→J effective).

**Installation** : comme d'habitude — Ctrl+C sur la fenêtre noire,
extraire le ZIP « Remplacer tous », relancer `demarrer.bat`, Ctrl+F5 :
v1.37 au bas de l'écran de connexion.

---

## v1.36 — Onglet Infractions alimenté EN RÉEL par Ym@ne (26/08/2026)

**Pourquoi** : la v1.35 a bâti l'onglet Infractions et son workflow de
validation, avec le collecteur livré volontairement **désactivé** en
attendant l'exemplaire de requête navigateur. Vous l'avez fourni le
26/08 au matin (les deux adresses du rapport Ym@ne). J'ai pu vérifier le
branchement **en direct avec le compte LSS** : la connexion applicative,
le rapport réel et tous ses vocabulaires sont maintenant connus et gravés.

**Quoi (exécution de I5, inscrite dans REFERENCE_IA_REGLES.md avant codage)** :

- **Branchement gravé et activé** (`YMANE_ACTIVE=1` dans `backend\.env`) :
  session applicative `POST /login/` avec les identifiants MZoneX (votre
  consigne du 25/08 : « les identifiants sont les mêmes que MZoneX ») —
  la réponse de connexion fournit elle-même l'identité du transporteur
  (2004 = LSS), **jamais recopiée à la main** ; garde de session
  (`isvalidaccess`) avec **une re-connexion automatique** si la session a
  expiré ; lecture du rapport « Exceptions détaillées » sur la fenêtre
  **J-1 → J** à chaque cycle 15 min (les infractions de la veille
  finalisées pendant la nuit sont ainsi rattrapées, sans doublon grâce à
  l'identifiant unique Ym@ne).
- **Constats du monde réel pris tels quels** : les seuils sont affichés
  **verbatim**, sans unité inventée — « **18:00:00 to 05:30:00** » pour la
  conduite de nuit, « **6.00** » pour l'accélération, « **25 km/h** » en
  vitesse, « **04:30** » pour la conduite continue (4.50 h → 4 h 30) ; les
  noms d'infraction sont les **libellés français officiels** du portail
  (« Conduite de Nuit », « Excès de Vitesse », « Accélération Brusque »…) ;
  les coordonnées GPS arrivent en « [longitude, latitude] » et sont
  **réordonnées** à l'affichage ; la date/heure est l'heure **locale** du
  portail, jamais convertie ; le chauffeur est rapproché du référentiel
  (insensible casse/accents) sinon son nom brut est conservé.
- **Rien d'autre ne change** : seuls « alerte » et « alarme » comptent
  (votre choix I2 — les lignes « Enregistrement » du portail, visibles
  dans Ym@ne, restent **invisibles** ici), les 10 colonnes, les exports
  écran = export, la validation Valide/Invalide + observation, jamais de
  suppression.
- **Ce que vous verrez** : au premier cycle (toutes les 15 min), vos
  vraies lignes apparaissent — par exemple celles constatées en direct le
  24-25/08 : « 4526 TCC (LSS) — Conduite de Nuit — **Alarme** — seuil
  18:00:00 to 05:30:00 », « 0926 TBV (LSS) », etc. Vous pouvez alors
  valider/invalider comme prévu.
- **Coupure opposable** : remettre `YMANE_ACTIVE=0` coupe le flux
  proprement (aucune requête sortante, aucune écriture) — prouvé par test.

**Recettes** : nouvelle suite `test_ymane_branchement_v136.py`
(**32 vérifications** — normalisation des 7 familles réelles verbatim,
rejet « Recording » à la normalisation, seuils affichés « 25 km/h » /
« 04:30 » / « 09:00 » / « 6.00 » / « 18:00:00 to 05:30:00 », ordre GPS
corrigé, idempotence par identifiant externe, session factice complète :
ouverture, garde, re-connexion sur expiration, refus explicite, coupure
sans requête). Suite v1.35 **réalignée** (50 vérifications inchangées en
sens, fixtures portant désormais la forme réelle du portail — déclaré en
CONFORMITÉ). **Batterie complète : 27 suites, 762 OK / 0 KO** + essai de
fumée réel (connexion Ym@ne effective, import de vos lignes, validation).

**Installation** : comme d'habitude — Ctrl+C sur la fenêtre noire,
extraire le ZIP « Remplacer tous » (votre base `backend\data\lss.db` n'est
jamais dans le ZIP), relancer `demarrer.bat`, Ctrl+F5 sur la page :
v1.36 au bas de l'écran de connexion.

---

## v1.35 — Onglet Infractions alimenté par Ym@ne (25/08/2026)

**Pourquoi** : depuis août, l'onglet Infractions attend sa source externe
pré-filtrée (arbitrage C3 du 22/08). Vous l'avez désignée le 25/08 au soir :
**Ym@ne**, la plateforme d'infractions de MZoneX (https://bi.camtrack.pro),
et vous avez dicté la structure de l'onglet colonne par colonne.

**Quoi (arbitrages §0quinquies decies I1→I5, adoptés le 25/08/2026)** :

- **I1 — Source unique = Ym@ne (MZoneX).** L'onglet n'affiche QUE des
  infractions remontées de Ym@ne — jamais rien de calculé localement (les
  alertes de la plateforme restent à l'onglet Alertes, inchangé). Ym@ne ne
  couvre que MZoneX : les 13 camions CamtrackPro n'y figureront jamais,
  c'est voulu (confirmé par vous).
- **I2 — Seuls les niveaux « alerte » et « alarme » comptent.** Ym@ne
  pré-filtre déjà tout ; de notre côté, le niveau « enregistrement » est en
  plus bloqué à l'entrée et **n'arrive jamais en base**.
- **I3 — Les 10 colonnes dictées, à l'identique** : Date · Heure ·
  Immatriculation · Chauffeur (nom et prénom) · Nom de l'infraction ·
  Niveau · **Seuil** (la limite quelle que soit la famille : « 90 km/h » en
  vitesse, « 04:30 » en temps de conduite) · **Coordonnées GPS** (pour les
  infractions de vitesse — cliquables, la carte s'ouvre sur le point) ·
  **Validation** · **Observation**. Filtres : période, véhicule, chauffeur,
  niveau, état de validation. Exports Excel/PDF = mêmes colonnes.
- **I4 — Validation ligne par ligne.** Chaque infraction arrive **« Non
  traitée »** ; liste déroulante **Valide / Invalide** (droit : ADMIN +
  TRACKING ; Consultation en lecture seule). **Invalide exige une
  observation** (la cause). Une ligne invalidée est **exclue des totaux et
  des exports, jamais supprimée, jamais masquée à l'écran** ; chaque
  décision est tracée (par qui, quand, quelle observation — journal d'audit,
  re-décision possible). Une re-collecte Ym@ne ne peut jamais écraser une
  décision.
- **I5 — Branchement : tout est prêt sauf le tuyau.** Ym@ne exige une
  session applicative ; le collecteur est **livré désactivé**
  (`YMANE_ACTIVE=0` dans `backend\.env` — il ne coûte rien tant qu'il est
  inactif). **Pour l'activer, il me faut UN exemplaire d'une requête de
  votre navigateur** : ouvrez https://bi.camtrack.pro, connectez-vous
  (identifiants MZoneX), ouvrez la page « détail exception », puis **F12 →
  onglet Réseau → clic droit sur la requête → Copier → « Copier comme
  cURL »**, et collez-moi le résultat (2 minutes). Je fige alors le
  branchement exact dans une v1.36 — sans changer aucune règle ci-dessus.
  Une fois actif, la collecte suit le rythme du cycle 15 min, sans jamais
  créer de doublon (clé externe Ym@ne sinon clé date+heure+plaque+nom).

**Recettes** : nouvelle suite `test_infractions_ymane_v135.py`
(**50 vérifications** — filtrage I2, idempotence anti-doublon I5, clé
naturelle, véhicule/chauffeur inconnus reportés jamais inventés, droits
403/400/200, audit avant/après, compteurs excluant les invalidées, exports
excluant les invalidées, souveraineté des décisions face à la re-collecte,
cycle désactivé sans effet, migration automatique des 9 colonnes).
**Batterie complète : 26 suites, 730 OK / 0 KO.**

---

## v1.34 — TCC = chrono de session : les arrêts courts comptent dans le TCC (25/08/2026)

**Pourquoi** : vous avez dicté les règles du moteur le 25/08 au soir avec un
exemple précis — **0576TCD** : départ 10:20, arrêt 11:13, mini-manœuvre
11:15→11:15 (0,004 km), reprise 11:36, mesure à 14:30. Attendus : **TCC =
4:10** · TCJ = 3:47 · TTJ = 4:10. Vérification instrumentée : **TCJ et TTJ
étaient déjà exacts** ; seul le **TCC** divergeait — l'arrêt de 23 min ne le
coupait pas mais était *déduit* (3:46 affiché). Or le texte de la loi §1.1
dit depuis août : *« Arrêt < 30 min → la pause courte est **incluse** dans le
TCC »*. Cette version réaligne le calcul sur la loi.

**Quoi (arbitrages §0quaterdecies H1→H2, adoptés le 25/08/2026)** :

- **H1 — TCC = chrono de session.** TCC = temps écoulé depuis le début de la
  session (premier départ du jour, ou reprise après la dernière pause
  **≥ 30 min**) — **les arrêts < 30 min sont inclus**, ils ne sont plus
  déduits. Votre exemple affiche désormais **TCC = 4:10**, pile comme demandé.
- **H2 — Arrêt court en cours : le chrono continue.** Camion arrêté depuis
  10 minutes, pas encore reparti → le TCC continue de s'écouler ; si l'arrêt
  atteint 30 minutes → pause coupante → TCC = 0, nouvelle session au
  redémarrage.
- **Inchangé** : TCJ (conduite pure, tous les arrêts déduits, manœuvres
  totalement ignorées), TTJ (amplitude), DÉPART (jamais fixé par une
  manœuvre), coupure TCC à 30 min, traversée de minuit (R1), historique et
  archives des jours passés (jamais recalculés).
- ⚠️ **Conséquence voulue** : comme les arrêts courts comptent désormais,
  la pré-alerte (4:00) et le drapeau rouge TCC (4:30) peuvent apparaître
  **jusqu'à ~29 minutes plus tôt** les jours avec arrêts de 20-29 min — le
  chrono reflète la charge réelle du chauffeur.

**Recettes** : nouvelle suite `test_tcc_chrono_v134.py` (23 vérifications — le
scénario exact de l'exploitant rejoué, bornes 29:59/30:00, arrêt en cours,
sessions chaînées, recouvrement) + réalignements déclarés sur
`test_addendum_v19` (4 attendus), `test_ameliorations_v129` (2) et
`test_fusion30_v133` (1) — uniquement des **attendus de test** passés de la
mesure « conduite pure » à la mesure « chrono ». **Batterie complète :
25 suites, 680 OK / 0 KO.**

---

## v1.33 — Règle des pauses simplifiée : moins de 30 min = une seule ligne (25/08/2026)

**Pourquoi** : le 25/08 vers 14 h, trois camions montraient une case PAUSE
**vide** alors qu'un arrêt avait bien eu lieu (vérifié à la seconde sur les
portails) : **0916TBV** (arrêt de 20 min 06 s), **0576TCD** (22 min 59 s),
**8076TCB** (25 min 50 s). C'était l'effet exact de la règle du 24/08
(« arrêt de 20 à 29 min : deux lignes, case pause masquée »). L'exploitant a
décidé de la supprimer et de tout ramener à **deux cas simples**.

**Quoi (arbitrage §0tricies decies G1→G2, décidé directement par
l'exploitant le 25/08/2026 — abroge E3, amende E1)** :

- **Arrêt < 30 min → les deux trajets forment UNE seule ligne** (début du
  premier, fin du dernier, kilomètres additionnés, couleur = celle de la
  dernière portion) — plus de case pause du tout, grille encore plus lisible.
  Avant, cette fusion ne se faisait qu'en dessous de 20 min.
- **Arrêt ≥ 30 min → deux lignes, pause affichée normalement** (0:35, 1:43…).
  La barre des « 20-29 min : case vide » disparaît : désormais, deux lignes
  séparées ont TOUJOURS une pause visible.
- **Rien d'autre ne bouge** : les compteurs (TCC/TCJ/TTJ) déduisent comme
  avant TOUS les arrêts, quelle que soit leur durée ; le seuil de 20 min du
  moteur (ligne orange tant que la fin n'est pas confirmée, rattrapage des
  trajets en cours) reste à 20 min ; seuil TCC 30 min inchangé ; historique,
  exports Excel/PDF et archives suivent la même règle sans qu'aucune archive
  ne soit réécrite. Le nouveau réglage « SEUIL_FUSION_AFFICHAGE_S » (1800 s)
  apparaît dans Paramètres → Seuils dès le premier démarrage.
- Effet visible sur vos trois camions du 25/08 : 0916TBV affiche une ligne
  **05:44 → 10:00** (puis la suite), 0576TCD une ligne **10:20 → …**,
  8076TCB une ligne **07:33 → 08:28** — et les pauses de 1:43 et 0:40
  restent affichées.

**Recettes** : nouvelle suite `test_fusion30_v133.py` (37 vérifications, dont
les trois cas réels du 25/08 rejoués à la seconde, la borne exacte 30:00 et
la relecture d'archives) + réalignement de 2 vérifications de
`test_affichage_position_v131` (l'ancien comportement 20-29 min est devenu
volontairement obsolète). **Batterie complète : 24 suites, 657 OK / 0 KO.**

---

## v1.32 — Garde anti-double-comptage des compteurs + bouton Diagnostic (25/08/2026)

**Pourquoi** : le 25/08 à 10:02, la ligne **0926TBV affichait TCC 5:13 =
TCJ 5:13 > TTJ 4:26** (DÉPART 05:34 orange, toutes les cases trajets « — »).
Enquête : le portail MZoneX n'a publié ce jour-là QUE 2 trajets réels
(05:34:08→09:35:48 puis 09:36:28→10:04:27) — mais deux lignes de la base,
créées par deux mécanismes automatiques différents (une ligne « rattrapée »
ouverte vers 08:49 + la ligne officielle), **se recouvraient de 47 minutes,
comptées deux fois** (5:13 = 4:26 + 0:47). Reproduction de laboratoire
exacte obtenue avec les fonctions de production. Les cases « — » venaient
de la fusion d'affichage v1.31 (les deux lignes recouvrantes formaient une
seule ligne « en cours » sans fin), et le drapeau rouge ⚠ était le
franchissement du seuil légal TCC (4:30), conséquence du double comptage.

**Quoi (arbitrages §0duodecies F1→F4, adoptés le 25/08/2026)** :

- **F1 — Règle absolue « le temps roulé n'est jamais compté deux fois »** :
  les compteurs (TCC, TCJ, pauses) sont mesurés sur l'**union des
  intervalles** des lignes → **TCJ > TTJ devient mathématiquement
  impossible**, quelle que soit la panne en amont. Pour des journées saines,
  le résultat est strictement identique à avant (Σ des durées) — la règle ne
  change rien d'observable, sauf à éliminer le double comptage. Chaque
  détection de recouvrement est tracée dans le journal d'audit
  (`trajet.chevauchement_detecte`) ; les lignes ne sont ni modifiées ni
  supprimées par cette garde.
- **F2 — Le rattrapage « camion roulant sans ligne » réactive au lieu de
  dupliquer** : si une ligne rejetée couvre encore l'instant (ouverte, ou
  refermée depuis moins de 20 min), elle est réactivée avec son vrai début
  (action d'audit `trajet.reactivation`, qui remplace l'ancien nom
  `trajet.reouverture`) — plus aucune ligne nouvelle empilée par-dessus.
- **F3 — Réparation unique de la journée du 25/08** : au premier démarrage
  v1.32, les lignes recouvrantes de chaque journée active du 25/08 sont
  départagées (la ligne officielle VALIDÉE prime, puis le début le plus
  tôt) ; la perdante est marquée REJETÉE — jamais supprimée — avec audit ;
  les compteurs sont recalculés. La ligne 0926TBV redevient exacte (DÉPART
  05:34, TCC = TCJ = TTJ ≈ 4:26 à 10:02, cases trajets de nouveau remplies).
- **F4 — Bouton « Diagnostic » (page Suivi)** : tapez la plaque dans la
  recherche, cliquez « Diagnostic » → un fichier texte est téléchargé
  (lignes du jour toutes statuts, compteurs, positions GPS, actions
  automatiques de la journée, seuils). Joignez-le à toute question : le
  diagnostic se fait désormais en une pièce jointe.

**Recettes** : nouvelle suite `test_doublons_v132.py` (63 vérifications, dont
la reproduction chiffrée de la capture) + réalignement `test_zeroquater_v120`
(renommage d'audit F2). **Batterie complète : 23 suites, 620 OK / 0 KO.**

---

## v1.31 — Grille allégée (arrêts < 20 min) + positions nommées par les portails (24/08/2026)

**Pourquoi** : deux retours métier du 24/08.

1. **Grille « bondée » par les micro-arrêts** — ex. réel 0936TBV (24/08) :
   départ 09:54, arrêt 13:06, reprise 13:13 (7 min), arrêt 13:55, reprise
   14:36 → le métier veut les colonnes **DÉPART 9:54 / FIN T1 13:55 /
   PAUSE 1 0:40 / DÉBUT T2 14:36**, les arrêts < 20 min restant déduits des
   compteurs « en arrière-plan ».
2. **Colonne J-1 à « position inconnue »** — depuis les connecteurs API
   (v1.26), ni MZoneX ni CamtrackPro ne géocodent : les adresses restaient
   vides… alors que les portails nomment les lieux partout.

**Quoi (arbitrages §0undecies E1→E5, adoptés le 24/08/2026)** :

- **E1 — Fusion d'affichage des ruptures < 20 min** (amendement partiel
  d'AM-2) : deux lignes séparées par un arrêt < 20 min forment UNE ligne à
  l'écran, dans l'Historique, les exports Excel/PDF et la modale des trajets
  (distance et `segments` sommés, statut/couleur de la dernière composante,
  numérotation re-séquencée, « Nb trajets » = lignes fusionnées). C'est une
  fusion **purement d'affichage**, appliquée à la sérialisation unique : la
  base conserve toutes les lignes réelles ; les archives antérieures (19-23/08
  restaurées par v1.30 comprises) s'affichent selon la même règle **sans
  aucune réécriture** (fonction idempotente, §A.2 respecté).
- **E2 — Compteurs inchangés** : TCC/TCJ/TTJ continuent de déduire TOUS les
  arrêts, toute durée (AM-1) — TCJ = Σ durées de conduite réelle, exactement
  comme l'exemple du métier.
- **E3 — Seuil d'affichage des pauses inchangé (30 min)** : une rupture de
  20-29 min crée deux lignes mais sa durée reste « — » (choix du métier).
- **E4 — Positions nommées par les géozones des portails** : à chaque point
  GPS, adresse = zone contenante la plus spécifique (union MZoneX Places +
  géozones CamtrackPro, 1 199 zones), sinon « **proche \<nom\>** » à moins de
  5 km, sinon coordonnées GPS (jamais de cellule vide). Alimente événements,
  Arrêt final, Lieu arrêt et J-1. **Correctif associé** : le `buffer` des
  Places MZoneX est en **degrés** et était lu en mètres → les 749 zones
  MZoneX n'étaient jamais reconnues (B4 « alerte vitesse en zone » inclus) ;
  conversion × 111 320 en place.
- **E5 — Colonne J-1 toujours remplie** : création de la ligne du jour =
  libellé E4 de la dernière position GPS de la veille ; **rattrapage unique**
  au premier démarrage v1.31 (marqueur d'audit `position_j1_v131`) : la
  colonne J-1 d'aujourd'hui est remplie depuis les positions GPS d'hier.

**Preuves** : suite `test_affichage_position_v131.py` (fusion exemple
0936TBV au caractère près, compteurs intacts, idempotence, snapshots jamais
mutés, nommage zone/proche/coordonnées, conversion d'unité, rattrapage) +
batterie complète au verso de ce fichier.

---

## v1.30 — Réparation embarquée des journées abîmées (+ renfort de minuit) (24/08/2026)

**Pourquoi** : l'ancienne v1.28 clôturait les trajets restés « ouverts » à
**01:00** pile (ancienne bascule). Quand le poste est éteint le soir (ex.
vendredi 21/08), les fins du soir n'arrivent jamais et l'Historique garde de
fausses fins « 01:00 » (TCJ 19:36 fantôme pour 0916TBV ; fin T3 « 01:00 » à la
place de 20:38 pour 4876TBU) ; le 20/08 a aussi perdu le trajet du milieu
0906TBV (10:19→13:44). L'utilisateur ne pouvant pas transmettre sa base, **la
vérité des portails** (jamais effacée) est relue directement sur son poste.

**Ce que fait la v1.30** (arbitrages §0decies du 24/08/2026, adoptés D1→D3) :

1. **Au premier démarrage, une seule fois, automatiquement** : chaque journée
   du 19/08 à la veille est **revérifiée contre les deux portails** (mêmes
   mécanismes que le rattrapage AM-4).
2. Seules les journées **à écart** sont réparées — trajet perdu **réinséré**
   (0906TBV 20/08), ligne fausse **guérie** aux horaires du portail (les
   géantes « 05:23 → 01:00 » retrouvent leur vraie fin ; les fusions
   d'affichage de l'ère v1.28 sont décomposées en lignes propres, AM-2),
   ligne sans source au portail **retirée de l'écran mais conservée en base**
   (marquée REJETÉ — jamais de suppression). Journée déjà conforme :
   **aucune écriture**, archive intacte (§A.2).
3. Les **archives** des journées réparées sont régénérées (exception ponctuelle
   arbitrée à §A.2) avec journal d'audit **AVANT/APRÈS** par véhicule et une
   **alerte récapitulative** par journée + une alerte finale dans l'onglet
   Alertes — grilles d'Historique de nouveau exactes (écran = export = archive).
4. **Garde-fou R5** : si un portail ne répond pas, la journée est sautée (rien
   d'écrit en partiel) et reprise au démarrage suivant — la réparation
   « s'arrête quand tout est à jour ».
5. **Renfort de minuit (D4)** : avant de figer la veille à 23:59:59, le cycle
   quotidien **relit désormais les portails sur la veille** (récupère les fins
   de soirée manquées — le scénario « PC rallumé juste avant minuit » ne peut
   plus figer une soirée incomplète) ; portails injoignables → consolidation
   sur la base avec note d'audit (minuit n'attend pas).

Preuves réelles (24/08) : le trajet 0906TBV **10:19:06→13:44:52 (10,33 km)**
relu tel quel sur CamtrackPro ; les 5 vrais trajets 4876TBU du 21/08 relus à
la seconde près sur MZoneX (09:29:53→13:30:39, 13:47:55→14:00:01,
14:35:26→16:41:20, 16:57:11→18:58:06, 19:36:37→20:38:36 — conformes à la
capture portail). 21 suites de tests, **509 vérifications OK / 0 KO**
(nouvelle suite `test_reparation_v130`, 26 vérifications).

---

## v1.29 — AMÉLIORATIONS v3 : tout le portail à l'écran, TCJ au réel, minuit propre (22/08/2026)

**Votre document `AMELIORATIONS_REGLES.md`, arbitré et inscrit dans la loi
(§0nonies, réponses C1→C4 du 22/08/2026).** Les six évolutions demandées sont
en production :

- **AM-3 + C1 — Consolidation à 23:59:59 et SPLIT à minuit.** La bascule
  01h00 disparait : un trajet jour = DATE pure. Un trajet en cours à minuit
  est découpé : segment A jusqu'à 23:59:59 (jour J, figé) et segment B à
  partir de 00:00 (jour J+1), sans pause artificielle ; le **TCC traverse**
  minuit sans remise à zéro (R1). Un trajet publié clôturé franchissant
  minuit est découpé de même à l'écriture.
- **AM-1 — TCJ = tout ce qui roule, rien d'autre.** TCJ = (maintenant −
  départ) − **TOUS** les arrêts quelle que soit leur durée (même 1 min) ;
  TTJ = temps écoulé depuis le départ (amplitude brute, borne 12 h
  conservée) ; TCC = session depuis la dernière pause ≥ 30 min, inchangé.
  Départ = premier **mouvement valide** (une manœuvre matinale ne fixe plus
  le départ — AM-6).
- **AM-2 — L'écran montre enfin TOUS les trajets publiés.** Fini les lignes
  soudées : chaque trajet valide garde sa ligne et ses chiffres ; seules les
  pauses ≥ 30 min sont visibles (les courtes restent calculées mais ne sont
  pas affichées). les colonnes de la grille et des exports (25/51) sont
  identiques — seul leur contenu suit la nouvelle loi. Rien n'est jamais
  effacé de la base.
- **AM-4 — Rattrapage auto au démarrage.** Si le poste a été éteint un ou
  plusieurs jours, la plateforme relit les historiques des deux portails,
  consolide et archive chaque jour manquant (idempotent). Un jour sans
  réponse des portails n'est pas écrit en partiel : avertissement posé,
  nouvelle tentative au prochain démarrage.
- **AM-5 — Onglet Infractions = vitre lecture seule.** Plus aucune infraction
  calculée localement n'y écrit ; tout passe par l'onglet Alertes (TCC/TCJ/
  TTJ, vitesse, sans badge…) comme aujourd'hui. La vitre reste vide tant
  qu'aucune source externe n'est branchée.
- **AM-6 + C2 — Arrêts selon les portails à la consolidation ; le direct
  garde la machine 3 km/h** (ligne orange « en cours », alerte « roule sans
  badge » à la minute — inchangés).

Batterie : **20 suites, 483 OK / 0 KO** (22 nouveaux tests v3 + suites
historiques réalignées sur la nouvelle loi, les changements d'attendus sont
commentés dans chaque suite avec la mention « v3 »).

## v1.28 — Correctif : les heures CamtrackPro retrouvent l'heure juste (20/08/2026)

**Arbitrage §0octies C1 du 20/08/2026** (signalement exploitant avec captures
de preuve, constat mesuré en direct : le serveur Wialon rend les heures du
rapport en **UTC**, pas en heure locale — « 02:24:44 » lu = 05:24:44 affiché
au portail, décalage exact +3h00 vérifié à la seconde) :

- **Lecture réparée à la racine** : l'instant officiel est désormais l'**epoch**
  que le serveur joint à chaque cellule horaire (insensible à tout réglage de
  fuseau du compte) ; une cellule texte seule est relue comme UTC puis
  convertie. Les heures CamtrackPro = celles du portail, à la seconde.
- **Réparation des données du 20/08, option LSS « correction automatique
  +3h »** : au premier démarrage de la v1.28, les trajets CamtrackPro du
  20/08 (stockés avec 3 h de retard par la v1.26) sont décalés de **+3h00
  exactes** — une seule fois (marqueur au journal d'audit, tout-ou-rien),
  **aucune donnée supprimée**, rattachement automatique au bon jour pour les
  trajets de nuit (bascule 01h00 §8.1). La grille, les chaînes et les
  compteurs se recalculent tout seuls au cycle suivant ; le prochain cycle
  N2 **met à jour** les lignes corrigées au lieu de les dupliquer (± tolérance
  de rapprochement — validé par test).
- Les lignes CamtrackPlus anciennes (repli écran, heures déjà justes) et
  toutes les lignes MZoneX ne bougent pas — tests F3 dédiés.

Batterie : **19 suites, 461 OK / 0 KO** (14 nouveaux tests dédiés).

## v1.27 — Conducteurs & conduite : le badge fait foi, le carnet est automatique

**Arbitrages §0septies du 20/08/2026.** Trois chantiers votés par
l'exploitant, construits sur les API des deux portails (reconnaissance en
lecture seule : 77 % des trajets MZoneX déjà badgés, 749 + 450 géozones,
compteurs d'écoconduite natifs) :

- **👤 Attribution automatique du chauffeur (B2).** Le nom publié par le
  portail s'inscrit sur chaque trajet validé **et attribue le chauffeur du
  jour** dans la grille (origine « badge », modifiable à la main). Exception
  arbitrée : les clés de service **« Nouveau conducteur »** et **« garage
  LSS »** sont écartées (tracées au journal) — pour elles, **votre saisie
  manuelle fait le travail** ; une attribution manuelle n'est JAMAIS écrasée
  par un badge. Rapprochement des fiches insensible à la casse (« JOMA
  ALEXANDRE » du portail retrouve « JOMA Alexandre » sans doublon).
- **📒 Carnet de conduite (B3) — nouvelle page « Conduite ».** Deux vues :
  par chauffeur (trajets, km, infractions par type — vitesse, freinage
  brusque, accélération, ralenti, sur-régime, autres — total et
  **infractions/100 km**) et « trajets noirs » (détail par trajet : qui,
  quand, vitesse max, nature des infractions). Sources : compteurs natifs
  MZoneX ; côté CamtrackPro : vitesse max + ralenti moteur officiels.
  **Aucune colonne ajoutée** à la grille ni aux exports (25/51 inchangés).
- **🚨 Alertes en direct (B4/B5).** `VITESSE_LIVE` : > **45 km/h** confirmé
  sur 2 signaux **hors géozone** (en géozone, les seuils des portails
  gouvernent — 1 199 zones chargées des deux portails, rechargées toutes les
  6 h) — 1 alerte par épisode. `SANS_BADGE` : un camion MZoneX qui **roule
  sans clé chauffeur** depuis plus de 10 min déclenche une alerte CRITIQUE
  (réarmement après 30 min d'arrêt). Côté CamtrackPro, la clé n'étant pas
  publiée en continu, le constat « sans badge » se fait au bilan des trajets
  — honnêteté inscrite dans la loi (B5).

Réglages associés (onglet Paramètres) : `CONDUITE_VITESSE_HORS_ZONE=45`,
`CONDUITE_BADGE_FENETRE_S=600`. Clés de service extensibles via
`CONDUCTEUR_BADGE_IGNORES` dans `backend/.env`.

Batterie : **18 suites, 447 OK / 0 KO** (37 nouveaux tests dédiés).

## v1.26 — CamtrackPro en direct aussi : toute la flotte (37 + 19) à la minute

**Votre jeton a débloqué la dernière porte.** CamtrackPro (révélé être un
portail Wialon blanchi) est désormais lu par API, au même titre que MZoneX :

- **Les 19 unités CamtrackPro visibles quasiment EN DIRECT** : leur dernière
  position + vitesse relue toutes les **60 secondes** (borne historique §5
  « pas de temps réel fiable » levée par votre jeton — arbitrage §0sexies A4).
  Les camions CamtrackPro ont donc enfin leurs **lignes orange « en cours »**
  à la minute, comme les MZoneX.
- **Trajets officiels par API** : le rapport « Detail Trajet Vehicule » est
  exécuté par API (mêmes valeurs qu'à l'écran — vérifié : 45 trajets du jour
  lus au test, conducteurs inclus).
- **Les 4 camions basculés (6256/7136/7206TCE, 7766TBL) fournissent enfin
  leurs données** : positions + trajets, immédiatement.
- **Recensement infaillible** : les 19 unités publiées lues en UN appel
  (fini le double passage Statuts + combo de v1.23/1.24).
- **Secours conservé** : panne API → l'ancien lecteur d'écran reprend les
  rapports (A2) ; la lecture positions est simplement reportée d'un cycle.
- Le jeton vit dans `backend\.env` (comme vos autres identifiants) — **gardez
  le ZIP privé**, comme toujours.
- Nouvelle suite `test_api_wialon_v126.py` — batterie complète repassée au
  vert (447 OK / 0 KO).

Récapitulatif de l'état d'avancement demandé au SAV : **les DEUX portails
sont désormais exploités par leurs API officielles**, à la minute, avec
secours écran automatique — exactement l'architecture de la « plateforme à
lui » évoquée par le responsable.

## v1.25 — Connecteur API MZoneX : vos camions quasiment EN DIRECT (arbitrages §0sexies du 20/08/2026)

**Grande nouvelle** — le SAV Camtrack a confirmé que les plateformes ont des
API ouvertes. Elles ont été **trouvées, authentifiées et validées** avec le
compte LSS existant ; cette version les met en production pour MZoneX.

- **MZoneX par API (OData), en PRINCIPAL** : plus besoin d'attendre les
  captures d'écran pilotées — les événements arrivent toutes les **60
  secondes** (arbitrage A1), avec heures **à la seconde**. Les boîtiers
  émettant d'eux-mêmes toutes les ~60-90 s, c'est le maximum utile.
- **Même moteur, mêmes règles** : les points API passent par EXACTEMENT les
  mêmes règles métier (lignes orange/noires, seuil 3 km/h, manœuvres
  ignorées, R2, gardes v1.24) que les lectures d'écran — seul le tuyau change.
- **Trajets officiels (Niveau 2) par API** : les mêmes trajets que l'onglet
  « Trajets », en UN appel au lieu de 37 lectures de filtres.
- **Recensement D4 infaillible** : les 37 véhicules publiés lus en UN appel
  (fini la virtualisation du portail, correctif v1.24 rendu définitif).
- **Secours automatique (arbitrage A2)** : si l'API tombe, le lecteur
  d'écran historique reprend le cycle sans rien vous demander — fenêtre
  noire : `REPLI lecteur d'écran (§0sexies A2) pour ce cycle`.
- **Connexion automatique** : la plateforme se connecte seule au serveur
  d'identité MZone avec les identifiants déjà configurés (jeton 1 h +
  renouvellement automatique). **Rien à faire de votre côté.**
- **CamtrackPro (= Wialon) préparé (A4 → v1.26)** : il ne manque que le
  **jeton** à créer dans l'interface CamtrackPro (2 min, onglet « Jetons »
  des propriétés utilisateur) — collez-le-moi et le même bénéfice arrive
  pour les camions CamtrackPro (live inclus).
- Démarrage : la fenêtre noire affiche désormais `MZoneX API : connecté au
  serveur d'identité MZone`, `recensement D4 : 37 véhicule(s) publié(s)` en
  UN appel et des collectes `MZoneX API (Événements)` chaque minute.
- Nouvelle suite `test_api_v125.py` — batterie complète repassée au vert.

## v1.24 — Correctifs du 14/08 (soir) : lecture fidèle des portails, fin des lignes fantômes

**Journal réel v1.23 (14/08 15:34–15:47)** — trois défauts sont apparus en
exploitation réelle ; tous sont corrigés ici (aucune règle métier modifiée,
seulement la lecture des portails et les gardes techniques — voir
REFERENCE_IA_REGLES.md, §0quinquies « Correctifs du 14/08 (soir — v1.24) ») :

- **Recensement CamtrackPro : les plaques sont enfin lues ENTIÈRES.** La
  page « Statuts » publiait bien 19 unités, mais l'extraction ne gardait que
  le nombre (« 6256 » au lieu de « 6256TCE ») → tout était écarté, aucune
  bascule. Corrigé : **6256/7136/7206TCE et 7766TBL basculent enfin vers
  CamtrackPro** automatiquement (alertes ⓘ + audits), puis leurs données
  sont exploitées au cycle suivant.
- **Recensement MZoneX : la liste déroulante du portail est virtualisée**
  (seule une dizaine d'options existe à la fois à l'écran) → 10 camions lus
  au lieu de 37, et la liste des « absents » accusait 34 camions sains.
  Corrigé : lecture en 11 passes filtres (espace + chiffres 0–9, défilement
  borné) avec union ; **garde anti-liste-partielle** : en dessous de 80 %
  de couverture des véhicules actifs d'un portail, le recensement est
  déclaré partiel (trace WARNING) et la liste des absents est SUSPENDUE
  pour ce portail ce cycle — on ne dénonce jamais sur une liste incomplète.
  La liste des absents redevient fiable : n'y figurent plus que les
  boîtiers réellement non rattachés.
- **Fin du va-et-vient « ligne fantôme » (0926TBV, 8076TCB le 14/08)** : les
  portails publient des « Début du trajet » à 0 km/h pour de simples blips
  de contact, et l'auto-réparation v1.17 les recréait en ligne « en cours »
  — rejetée « manœuvre » au cycle suivant, en boucle. Désormais, la
  réparation n'agit que si la dernière position connue du camion PROUVE le
  roulage (vitesse > 3 km/h, signal ≤ 20 min — la condition R2). Un vrai
  trajet manqué est toujours réparé ; un blip de contact ne dessine plus
  rien. (La garde de fraîcheur 45 min de la v1.22 reste cumulée.)
- **D5 : trace unique.** « Garage LSS 2 … bloqué » n'est plus écrit 11 fois
  par cycle dans la fenêtre noire : une fois par libellé et par session.
- Nouvelle suite `test_correctifs_v124.py` (garde de mouvement, garde
  anti-liste-partielle, D5) + piège §3 de `test_niveau2_v117.py` précisé
  (le camion du piège ROULAIT : preuve de mouvement jointe) — batterie
  complète repassée au vert.

## v1.23 — Complément D4 : recensement CamtrackPro via la page « Statuts » (union)

**Preuve exploitant 14/08 (capture « Statuts »)** — 6256TCE, 7136TCE, 7206TCE
et 7766TBL SONT publiés par CamtrackPro (onglet Statuts = liste complète du
compte) mais restaient invisibles du recensement v1.22, qui ne lisait que le
filtre « objet » du rapport.

- La page **« Statuts »** est désormais lue à chaque synchronisation, **en
  union** avec le filtre « objet » du rapport (repli automatique si l'une des
  deux sources échoue) ; extraction des plaques par motif dans le texte
  affiché (« 6256 TCE-CNHTC-LPSA(LSS) » → 6256TCE) — insensible au DOM,
  défilement borné du panneau.
- Effet attendu : bascules automatiques CTPRO pour 6256/7136/7206TCE,
  7766TBL (+ alertes ⓘ + audits), données exploitées au cycle suivant.
- Déduplication du recensement : une plaque publiée par les deux sources est
  comptée une seule fois.
- **Réserve** : si le *rapport* CamtrackPro refuse ensuite d'exécuter ces
  unités (filtre objet limité à un groupe), la fenêtre noire montrera
  « CTPRO : aucun objet ne commence par … » — la solution sera alors
  côté portail (groupe/permission des unités), la fiche sera déjà bien rangée.
- Suite de tests `test_statuts_v123.py` — batterie complète repassée au vert.

## v1.22 — Arbitrages §0quinquies du 14/08/2026 : recensement des portails (bascule auto de plateforme)

**Constat métier 14/08 ~11h00** — des véhicules avec fiche LSS (2066TBP,
7136TCE, 7206TCE, 7766TBL, 6256TCE) n'apparaissent dans AUCUNE liste
publiée du portail MZoneX : « aucune option véhicule » en boucle dans la
fenêtre noire. S'ils vivent en réalité sur CamtrackPro, une fiche marquée
MZoneX n'est lue par AUCUN collecteur : données perdues en silence.

- **D4 — Recensement des listes publiées par les portails à chaque
  synchronisation** (listes déroulantes véhicules MZoneX + CamtrackPro) :
  inconnu → fiche créée avec la plateforme d'origine (extension D1) ;
  connu mal rangé → **bascule automatique de plateforme** (arbitré) avec
  alerte ⓘ + audit `vehicule.plateforme_basculee` ; véhicules actifs vus sur
  AUCUN portail → signalés nommément en fenêtre noire (une fois tant que la
  liste ne bouge pas) : boîtier inactif ou rattachement de groupe à faire
  côté portail. Jamais de suppression.
- **D5 — mots non-personnes** (« garage », « dépôt », « station »…,
  surcharge `CONDUCTEUR_MOTS_IGNORES`) : jamais de fiche chauffeur ;
  celles déjà créées (ex. « Garage LSS 2 ») passent INACTIVES au démarrage
  + alerte ⓘ (réactivation manuelle possible, jamais supprimées).
- **Correctif moteur (alignement R2, pas un changement de règle)** —
  l'auto-réparation v1.17 ignore les « Début du trajet » de plus de 45 min
  sans fin ni mouvement (boîtier muet) : fin du va-et-vient
  re-création / rejet manœuvre constaté en réel sur 7936TCB / 8806TCB.
  Un début frais est toujours ré-ingéré ; R2 reprend la main au premier
  roulage réel.
- Mémorandum : la plaque du camion 7766 est **7766TBL (lettre L)** —
  arbitrage exploitant 14/08.
- **Robustesse** : correspondance par plaque EXACTE dans le filtre
  véhicule MZoneX (repli « contient » conservé) ; note de description
  ≤ 60 car. lors d'une bascule (compatible PostgreSQL).
- **Tests** : nouvelle suite `test_recensement_v122.py` (26 contrôles :
  parser de libellés, création/bascule/absents/idempotence D4, blocage et
  réparation D5, garde de fraîcheur). Batterie complète : **362 OK / 0 KO**.

## v1.21 — Arbitrages §0quater D0-D3 du 14/08/2026 : découverte automatique des véhicules & chauffeurs

**Demande exploitant** — la flotte bouge (ajouts de véhicules et de
chauffeurs, remplacements) : un véhicule ajouté à la main restait vide dans
le Suivi ; on veut que le scraping **détecte tout seul** les nouveaux
véhicules sur les portails, les **attribue au bon portail (MZX ou CTPRO)** et
**exploite leurs données comme les autres**. Règles adoptées le 14/08/2026 :

| # | Règle | Effet |
|---|---|---|
| **D0** | Identifiants (plaque & boîtier) **normalisés comme les portails** (« 8076 TCB » → « 8076TCB ») : à la création, à la modification, **et réparation automatique au démarrage** des fiches déjà en base (collision → fiche non touchée + alerte ⓘ). | Une fiche saisie avec espace ne peut plus rester invisible (cause identifiée du constat « ajout manuel inexploité »). Journal : `§0quater D0 : plaque … → …`. |
| **D1** | Identifiant plaque vu par un collecteur et inconnu → **fiche véhicule créée automatiquement** (plateforme = portail détecteur, ACTIF) + exploitée **dès le relevé suivant** + alerte ⓘ « Nouveau véhicule détecté » + audit `vehicule.auto_cree`. | Plus aucun camion sans données ; la fiche se complète ensuite dans Véhicules (marque, capacité, chauffeur). |
| **D2** | Conducteur vu (colonnes « Conducteur »/« dernier conducteur » MZoneX) et inconnu → **fiche chauffeur créée** (matricule `AUTO-…`) + alerte ⓘ. **Affectation au véhicule toujours manuelle.** | Référentiel chauffeurs auto-alimenté, contrôle humain conservé. |
| **D3** | Tout identifiant non retenu (forme non-plaque) est **journalisé en fenêtre noire** au lieu du silence. | Diagnostic immédiat. |

**Cas 8076TCB / Mamison (14/08)** — portail : manœuvre 05:16→05:26 de
**0,009 km** (ignorée §7) puis « Début du trajet » 05:30:52 **sans Fin et
sans déplacement** (compteur 66 280,0 km inchangé, même point GPS, silence
boîtier de 4h30) → la loi donne : manœuvre seule validée = **ligne vide
légitime** ; le signal utile est l'alerte **« GPS hors ligne »** (silence >
30 min, déjà en place). La ligne « en cours » n'a survécu que si la v1.20+ a
vu l'événement en direct (R1) — à l'ouverture du trajet ou au prochain signal,
la ligne apparaît normalement.

**Mise à jour (2 minutes, sans perte de données)**

1. `Ctrl+C` → extraire le ZIP par-dessus (« Remplacer tous ») en **gardant**
   `backend\data\lss.db` → `demarrer.bat`.
2. Fenêtre noire : `LSS Tracking v1.21` puis, au premier passage,
   `§0quater D0 : plaque … → …` si des fiches étaient à réparer — **rien à
   refaire à la main**, sauf en cas de doublon signalé par alerte ⓘ.
3. Onglet **Alertes** : deux nouvelles cartes ⓘ possibles : « Nouveau
   véhicule détecté » / « Nouveau chauffeur détecté ».
4. `Ctrl+F5` → « v1.21 » en bas de page.

**Tests** — 12 suites au vert (335 contrôles), dont la nouvelle
`test_referentiel_v121.py` (19 : normalisation, collision, création MZX &
CTPRO + import immédiat, idempotence totale, chauffeur créé sans affectation
automatique, parasite écarté).

## v1.20 — Arbitrages §0quater du 14/08/2026 : un camion en route a TOUJOURS sa ligne (et son TCC)

**Mise en situation exploitant** — le TCC sert à ANTICIPER la conduite
continue ; or (a) un camion qui part directement (sans manœuvre) n'a rien
dans l'onglet Trajets du portail, et (b) un camion dont les seuls trajets
publiés sont des manœuvres invalides roule déjà pour de vrai — dans les deux
cas, la vraie route est visible dans l'onglet Événements (DÉMARRAGE sans
ARRÊT derrière). Règles adoptées le 14/08/2026 :

| # | Règle | Effet à l'écran |
|---|---|---|
| **R1** | Une ligne « en cours » (sans fin) n'est **JAMAIS rejetée** pour une manœuvre officielle au même début — le verdict manœuvre/trajet se rend **à la clôture** (§5.1). La garde anti-géants reste pleine sur les lignes clôturées. | Fin du cas « camion en route, grille vide » (manœuvre de dépôt prolongée en vrai trajet). Audit : `trajet.veto_rejet_en_cours`. |
| **R2** | **Rattrapage à chaque cycle (≤ 7 min)** : camion MZoneX en roulage récent (vitesse > 3 km/h, signal ≤ 15 min) sans ligne « en cours » → ligne (ré)ouverte, **datée du vrai DÉMARRAGE** du jour. | Couvre : plateforme lancée après le départ, événement manqué, ligne tuée par l'ancienne règle. Audits : `trajet.reouverture` (début conservé) / `trajet.ouverture_rattrapage`. Journal : `§0quater R2 : <plaque> — ligne …`. |
| **R3** | Le **TCC provisoire** (orange) est calculé en continu depuis la ligne en cours, même si l'onglet Trajets est vide. | La colonne TCC ne disparaît plus tant que le camion roule. |
| **O1** | **Pré-alerte « pause non prise » à 4h00** (`SEUIL_TCC_PREALERTE`, paramétrable dans Paramètres ; avant : 3h49:30 = 85 %). Message : « Pré-alerte TCC (pause à prévoir) — … — seuil 4h30 dans XhYY ». | Anticipation avant le seuil 4h30. |
| **R4** | CamtrackPro : la ligne « en cours » est reprise du **rapport « Detail Trajet »** (PASSE 0 existante, §6.2 phase 1 : dernière ligne du rapport = trajet en cours, fin = dernière position). Cadence 15 min conservée (arbitrage du 14/08). R2 non applicable et non requis. | Les 13 camions CamtrackPro apparaissent « en route » avec ≤ 15 min de délai — sans changement de code. Ex. 14/08, 7306TCE : manœuvre 05:03 ignorée · T1 05:27→08:10 noir · Pause 0:34 · **T2 08:44→en cours 🟠**. |

Rappel : le cas « départ direct sans manœuvre » était déjà couvert par la
ligne orange dès l'événement DÉMARRAGE (§6.2 phase 1) ; R1+R2 garantissent
désormais qu'elle n'est **jamais perdue en route**. Si une grille reste vide
plus de 8 minutes après un DÉMARRAGE, vérifier les lignes `MZoneX
(Événements) : N lignes lues` de la fenêtre noire (calibrage sélecteurs §10).

**Mise à jour (2 minutes, sans perte de données)**

1. `Ctrl+C` dans la fenêtre noire.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous »). ⚠️ GARDEZ `backend\data\lss.db`.
3. Relancer `demarrer.bat` → vérifier `LSS Tracking v1.20` + la ligne
   `Seuil ajouté : SEUIL_TCC_PREALERTE = 14400` (premier démarrage) ; rien à
   retaper.
4. `Ctrl+F5` → « v1.20 » en bas de page. Dès le cycle suivant, les camions
   en route sans ligne réapparaissent automatiquement (lignes du 13/08
   tuées par l'ancienne règle comprises, si le camion roule encore).

**Tests** — 10 suites historiques au vert (295 contrôles) + nouvelle suite
`test_zeroquater_v120.py` (21 contrôles : véto R1 sur ligne ouverte/clôturée,
réouverture avec début conservé, création datée du DÉMARRAGE, gardes
arrêt/périmé/CamtrackPro, idempotence, TCC ≈ 3h00, pré-alerte 4h00 sans
doublon).

## v1.19 — Arbitrage LSS du 14/08/2026 : vitesse d'arrêt 5 → 3 km/h (embouteillages)

**Demande exploitant** — « il y a des camions qui roulent à 5-11 km/h à cause
d'embouteillages, or dans les règles précédentes le seuil min est de 12 km/h.
Il faut changer en 3 km/h ».

**Clarification (signalée avant codage — Partie A point 9)** — deux seuils
distincts existent dans le Référentiel :

| Paramètre | Rôle | État |
|---|---|---|
| `SEUIL_VITESSE_ARRET` (5 km/h depuis le 06/08) | le seuil **opérationnel** « arrêté / en route » : affichage Suivi (point vert/gris) et **fin de trajet §5.1** (premier arrêt) | **passé à 3 km/h** (§0ter E) |
| `SEUIL_VITESSE_MOYENNE` (12 km/h) | vitesse **moyenne** de la règle §6.1 « a vraiment commencé » | **inchangé** : règle dormante depuis l'arbitrage D du 06/08 (réservée aux flux de positions, inapplicable à MZoneX/CamtrackPro, absente du code) — la changer n'aurait eu AUCUN effet |

**Effet concret** — un camion avançant à 4, 5, 8, 11 km/h dans un bouchon est
désormais « en route » (avant : « arrêté » dès ≤ 5 km/h) et un bouchon ne
déclenche plus la fin du trajet. Arrêt = vitesse ≤ 3 km/h partout. Le
bruit GPS au point mort (< 2 min) reste absorbé par `SEUIL_BRUIT_GPS` (120 s).

**Mise en œuvre**

1. `engine.py` + `serializers.py` : défaut 5 → 3 (roule ⟺ vitesse > 3).
2. **Migration automatique au démarrage** (`migrer_schema`) : la valeur 5 déjà
   présente en base passe à 3 — RIEN À RETAPER. Si vous l'aviez personnalisée
   entre-temps, votre valeur est respectée (la migration ne touche que le
   défaut 5). Journal : `Migration v1.19 : SEUIL_VITESSE_ARRET 5 → 3 km/h`.
3. Le seuil reste **éditable à tout moment** dans **Paramètres** (admin) —
   §12.2 : jamais codé en dur.
4. Référentiel : nouveau bloc **§0ter « Arbitrage LSS du 14/08/2026 »** +
   mentions du corps alignées (tableau initial #4, §5.1, §12.1, §C.4,
   glossaire) — le document reste la loi, jour = 14/08.

**Mise à jour (2 minutes, sans perte de données)**

1. `Ctrl+C` dans la fenêtre noire.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous »). ⚠️ GARDEZ `backend\data\lss.db` (vos données réelles).
3. Relancer `demarrer.bat` → vérifier : `LSS Tracking v1.19`, la ligne de
   migration `SEUIL_VITESSE_ARRET 5 → 3 km/h`, et dans **Paramètres** la
   ligne « Vitesse d'arrêt » affichant **3**.
4. `Ctrl+F5` dans le navigateur → « v1.19 » en bas de page.

**Effet immédiat** dès les prochains événements GPS (≤ 7 min) : les camions
en bouchon repassent « en route ». Les journées déjà archivées ne sont PAS
recalculées (les exports passés restent la trace officielle du jour).

**Tests** — batterie complète 10 suites, dont cas d'embouteillage ajoutés
(2,9 km/h → arrêté ; 3,1 / 5 / 7 / 11 km/h → roule).

## v1.18.2 — « Il ne trouve pas les véhicules CamtrackPro » : identifiants CamtrackPro réparés (14/08/2026)

**Symptôme** — en mode données réelles, la fenêtre noire répète
`CamtrackPro Trajets : tentative 1/3 échouée … KeyError: 'CAMTRACKPRO_URL'`
(jusqu'à 3 fois) puis `CamtrackPro (rapport trajets) : 0 trajets clôturés sur
13 véhicule(s)` : MZoneX remonte bien, CamtrackPro jamais.

**Cause** — le `backend/.env` livré en v1.18.1 nommait les trois variables
`CAMTRACK_URL`, `CAMTRACK_USER`, `CAMTRACK_PASSWORD` (sans « PRO ») alors que
le code — et `backend/.env.exemple` — attend `CAMTRACKPRO_URL`,
`CAMTRACKPRO_USER`, `CAMTRACKPRO_PASSWORD`. La connexion au portail Camtrack
échouait AVANT même la recherche des véhicules : ce n'était donc ni un
problème de véhicules en base (les 13 plaques CamtrackPro sont bien marquées
`plateforme_gps = CAMTRACKPRO` par le seed : 0826TBS, 0906TBV, 3076TBS,
4296TCC, 5346TBU, 5506TBS, 5616TCE, 5626TCE, 5646TCE, 5716TBS, 6546TCE,
7306TCE, 9176TCC), ni un problème du portail.

**Corrections**

1. `backend/.env` livré : les 3 variables sont renommées en `CAMTRACKPRO_*`
   (valeurs identiques — vos identifiants habituels).
2. `config.py` : alias de compatibilité — si un ancien `.env` nomme encore
   `CAMTRACK_URL/USER/PASSWORD`, ils sont automatiquement reconnus comme
   `CAMTRACKPRO_*`. Les deux nommages fonctionnent désormais.
3. `scrapers.py` : si la configuration CamtrackPro manque malgré tout, le
   journal affiche un message français explicite (« CAMTRACKPRO_URL absente
   du fichier backend\.env — connexion CamtrackPro impossible… ») au lieu
   d'un `KeyError` technique.
4. `requirements.txt` : ajout de `python-dotenv` — petit module qui charge
   `backend/.env`. Oublié jusqu'ici, il est indispensable au mode réel :
   sans lui, le `.env` est SILENCIEUSEMENT ignoré (et l'application repasse
   en démonstration sur une installation fraîche). `demarrer.bat` l'installe
   tout seul au prochain démarrage.

**Mise à jour (3 minutes, sans perte de données)**

1. Ctrl+C dans la fenêtre noire (si l'application tourne).
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »). ⚠️ NE PAS supprimer
   `backend\data\lss.db` : vos données réelles d'hier sont conservées.
3. Relancer `demarrer.bat` (l'installation du module manquant prend ~1 min
   la première fois), puis vérifier dans la fenêtre noire :
   `LSS Tracking v1.18.2`, le bandeau `★★ MODE DONNÉES RÉELLES … ★★`, et
   surtout la disparition des lignes `KeyError: 'CAMTRACKPRO_URL'`. À la
   prochaine synchronisation Niveau 2 (≤ 15 min), une ligne
   `CamtrackPro (rapport trajets) : N trajets clôturés sur 13 véhicule(s)`
   avec **N > 0** confirme la réparation (N = 0 restant possible un jour de
   week-end férié sans roulage CamtrackPro — le signe décisif est
   l'absence d'erreur).
4. Dans le navigateur : Ctrl+F5, et vérifier « v1.18.2 » en bas de page.

**Tests** — batterie complète : 292 OK / 0 KO (10 suites, dont
test_chaines_v113 43/43 ; ce dernier est ancré à des heures fixes de la
journée : le matin, 10 checks temporels échouent artificiellement — relance
équivalente documentée `APP_TZ="Etc/GMT-12"`, voire note en tête du fichier).

## v1.18.1 — « Pourquoi des données fictives ? » : le mode DONNÉES RÉELLES garanti au démarrage (13/08/2026)

**Ce que vous avez vu ce matin : des trajets fabriqués, pas vos camions.**
Ce n'était pas une panne des portails ni un bug de règle métier : le serveur
était reparti en **mode démonstration**. Preuve dans votre propre journal :

```
lss.sim — Simulateur initialisé : 50 camions instrumentés
lss.sim — Rejeu de la journée jusqu'à 16:50…
lss.seed — Historique de démonstration généré (6 jours archivés)
```

*Pourquoi ?* La plateforme démarre en **démo** quand le petit fichier
`backend\.env` (SIM_ENABLE=0 + vos identifiants MZoneX / CamtrackPro) est
absent. Ce fichier n'a **jamais** été inclus dans les ZIP livrés. En
ré-extrayant la plateforme dans un dossier propre ce matin, le fichier a été
perdu (votre base de données aussi — le seed « 52 véhicules » qui tourne à
16:50 en est la preuve) → retour automatique au simulateur.

**Ce que la v1.18.1 change, pour que ça ne se reproduise plus :**

1. **`backend\.env` de PRODUCTION inclus dans le ZIP** (vos identifiants
   portail + `SIM_ENABLE=0` + `COLLECTOR_SOURCE=MIXTE` + relevé toutes les
   420 s). L'extraction « Remplacer tous » repose le fichier automatiquement.
2. **Le mode est écrit en clair dans la fenêtre noire à chaque démarrage**, en
   ligne impossible à rater :
   `★★ MODE DONNÉES RÉELLES (SIM_ENABLE=0) — collecteur MIXTE ★★`
   ou, si le fichier réglage manque :
   `★★ MODE DÉMONSTRATION : le SIMULATEUR fabrique les données (fictives)… ★★`
3. **Correctif n°1** : votre journal montrait une chaîne géante « Σ 15 517 km »
   (plusieurs camions regroupés en une seule ligne) quand les lignes arrivées
   ne portent que l'identifiant interne du véhicule. Le regroupement accepte
   désormais l'identifiant interne, le n° de boîtier OU la plaque — fini tout
   mélange entre camions, en démo comme en réel.
4. **Correctif n°2** : une erreur `ValueError: list.remove(x)` du simulateur
   (journal de 16:51) neutralisée.

### Reprise sur données réelles (5 minutes, une seule fois)
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Supprimez le fichier `E:\projets\LSS-Tracking-Plateforme\backend\data\lss.db`
   (il ne contient QUE des données de démonstration d'aujourd'hui — rien de
   réel à perdre). Si vous aviez renseigné des **n° de boîtier** dans le module
   Véhicules, notez-les : vous les ressaisirez en 2 minutes après le démarrage.
3. Extrayez le ZIP v1.18.1 **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers ») — il pose cette fois `backend\.env`.
4. Relancez `demarrer.bat` → vérifiez dans la fenêtre noire, dans l'ordre :
   - `★★ MODE DONNÉES RÉELLES (SIM_ENABLE=0) — collecteur MIXTE ★★` ;
   - `→ LSS Tracking v1.18.1 — Plateforme prête` ;
   - après ~1 min : `Collecteur réel MIXTE activé` et
     `Synchronisation Niveau 2 activée` ;
   - **aucune ligne** `lss.sim` ne doit apparaître.
5. Les trajets remontent dès le premier passage des collecteurs
   (≤ 7 min pour le temps réel MZoneX, ≤ 15 min pour la consolidation des
   trajets officiels MZoneX + CamtrackPro).
6. Si une erreur « navigateur Playwright » apparaît : une seule commande,
   `python -m playwright install chromium`, puis relancez `demarrer.bat`.

*Gardez ce ZIP pour vous : le `backend\.env` qu'il contient renferme vos mots
de passe portail.* Pour rejouer la démo un jour, mettez temporairement
`SIM_ENABLE=1` dans `backend\.env` (et remettez `0` ensuite).

Tests : **292 vérifications vertes** revalidées après les deux correctifs.

## v1.18 — Alignement sur votre « Référence IA » v2 + vos 4 arbitrages (06/08/2026)

Vous m'avez transmis le document de règles consolidées (**REFERENCE_IA_REGLES.md**,
« ce document fait loi ») et tranché ses 4 contradictions. La plateforme applique
maintenant la loi **verbatim**. Le document est livré dans le ZIP, et votre
section **« 0bis. Arbitrages LSS du 06/08/2026 »** y consigne vos décisions —
elle prime sur tout le reste.

**A · Fin de trajet = premier arrêt (§5.1) ; manœuvre ignorée PARTOUT (§7).**
C'est l'exemple §5.2 de votre document, appliqué au centime : 0616TCC affiche
exactement `Départ 5:31 · Fin 8:24 · Pause 1:24 · Début 9:48 · Fin 9:56`.
Une manœuvre (< 0,3 km) ne soude plus deux trajets, ne prolonge plus aucune
fin, n'interrompt jamais une pause et n'entre jamais dans TCC/TCJ/TTJ — elle
n'est plus même enregistrée quand elle arrive du portail (« IGNORER », §7).
*Conséquence visible* : ce que la v1.17 affichait en rallongeant les fins
(0616 « Fin 10:35 ») ou en soudant les pauses (jours type 9856TCD) revient à
la réalité de terrain : la **vraie pause** s'affiche (ex. 1h17 au lieu de
1h05) et chaque ligne s'arrête au **premier vrai arrêt**. Les distances
restent celles du portail (une manœuvre ne compte jamais, §2 — inchangé).

**B · Bascule de journée à 01h00 partout (§8/§9).** Vous avez choisi 01h00
(la contradiction interne 01:00/02:00 du document est tranchée et corrigée
dans le document lui-même) : jour = date de l'heure de début, sauf un début
**avant 01h00** qui complète la veille ; la pré-consolidation « tout officiel »
se fait à 01h00. Rien à faire : les journées passées sont figées telles
quelles, et la nouvelle règle s'applique dès le démarrage.

**C · CamtrackPro : validité = distance seule (§11.2).** L'ancienne règle
« en mouvement ≥ 20 min » est **supprimée** : un trajet de 0,3 km et plus est
valide, même court. *Conséquence visible (exemple 5716TBS)* : la ligne R4
(0,99 km / 10 min), jadis retirée à la clôture, redevient valide — T2 garde
sa propre fin (10:26 → 13:46, 87,85 km) au lieu de se rétracter.

**D · §6.1 réservé aux flux de positions ; affichage orange immédiat MZoneX
confirmé.** MZoneX n'émet rien entre « Début » et « Fin » : tout trajet
commencé s'affiche **immédiatement** en orange (décision du 05/08 maintenue),
puis est jugé à la clôture. Le critère « ≥ 15 min & ≥ 12 km/h » reste
paramétré (15 min) mais dormant.

**+ Vitesse d'arrêt = 5 km/h (§5.1/§12.1).** Le moteur considère désormais le
camion arrêté dès que la vitesse ≤ 5 km/h (avant : 3 km/h) — réglable dans
Paramètres (`SEUIL_VITESSE_ARRET`, §12.2).

**+ Comptabilité des arrêts exacte (§1.3).** TTJ = TCJ + arrêts, à la seconde
près : quand des manœuvres ponctuent un arrêt, leur durée n'est plus déduite
deux fois (les arrêts comptés sont **nets** des manœuvres ; chaque case Pause
de la grille montre toujours l'écart réel, ex. « 1:24 » du 0616).

**Inchangé (vérifié conforme à la loi)** : seuils §12.1 (0,3 km · 20 min ·
30 min · TCC 4h30 · TCJ 10h · TTJ 12h), stratégie hybride §11.1 (Événements =
temps réel orange, Trajets = officiel qui REMPLACE au MÊME emplacement, §3.2 —
jamais de doublon), formats §10 (grille compacte 25 colonnes / détail 51,
HH:MM et H:MM, vide = « — », écran = export = archive), seuils éditables dans
Paramètres (§12.2).

Tests : **292 vérifications vertes** (10 suites), dont la nouvelle suite
`test_reference_v118.py` (26) qui vérifie **verbatim** la cible §5.2 du 0616,
la bascule 01h00 (00:59 → hier, 01:00 pile → aujourd'hui), la pré-consolidation
01h00, CamtrackPro distance seule et le seuil 5 km/h.

### Mise à jour v1.17 → v1.18 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »).
3. Relancer `demarrer.bat` → vérifier `LSS Tracking v1.18 — Plateforme
   prête` et « v1.18 » en pied de page (Ctrl+F5 si besoin).
4. **Contrôle visuel au prochain passage (≤ 15 min)** : un trajet type 0616
   affiche la fin au **premier arrêt** (ex. `8:24`, pause `1:24`) et non plus
   la fin de la dernière manœuvre ; les journées avec micro-déplacements
   montrent la **vraie pause**.

## v1.17 — Correctif : « les règles ne sont pas toujours respectées » (05/08/2026, captures 14:20)

Vos 6 captures de 14:20 montrent 3 vraies violations des règles. Toutes sont
corrigées **et se réparent toutes seules** : aux passages suivants (≤ 15 min
après la mise à jour), les lignes d'aujourd'hui se complètent automatiquement.

1. **Fin de trajet tronquée — 0616TCD : Fin T2 affichée 09:56 au lieu de
   10:35.** *Cause* : les petits trajets < 0,3 km (manœuvres) étaient écartés
   **avant** la règle de fusion « pause < 20 min → même trajet », au lieu de
   souder la chaîne. Résultat : la ligne s'arrêtait au dernier segment ≥ 0,3
   km. *Maintenant* : la ligne court du **premier vrai mouvement** jusqu'au
   **dernier mouvement** de la chaîne, manœuvres comprises. Avec votre
   capture (15 lignes du portail), la grille affiche exactement :
   `Départ 05:31 · Fin 08:40 · Pause 1:08 · Début 09:48 · Fin 10:35` — les
   heures officielles de votre onglet Trajets. Les **distances restent celles
   du portail** (84,318 km / 1,442 km) : une manœuvre ne compte jamais, ni en
   distance ni en TCC/TCJ/TTJ, mais elle **interrompt bien l'arrêt**.
2. **Trajet commencé invisible — 4886TBU : « Début du trajet » 11:00:17
   absent de la grille (même cas 2736TCC 11:37).** *Cause* : un événement
   déjà enregistré lors d'un passage manqué n'était jamais re-proposé
   (anti-doublon trop strict). *Maintenant* : **auto-réparation** à chaque
   passage du collecteur (≤ 7 min) : tout trajet commencé aujourd'hui sans
   ligne à l'écran est recréé en orange, et retiré à la clôture si c'était
   une simple manœuvre. Aucune action de votre part.
3. **Manœuvre en début de journée — 4886TBU 05:21→05:42.** Règle unifiée :
   la manœuvre de tête est masquée (jamais une ligne à elle seule) mais elle
   montre que le camion a bougé → le **Départ affiché suit le premier
   mouvement** (05:21), tandis que la ligne du matin démarre au premier VRAI
   trajet (05:55 → 10:03) et que les compteurs ignorent la manœuvre.

*Remarque sur 0906TBV* (TCJ 3:20 alors que TTJ 8:37 − pauses visibles ≈ 3:10)
: c'est **correct** — des manœuvres cachées au milieu d'un arrêt le découpent
pour les compteurs (la case Pause montre l'écart réel entre deux lignes, les
compteurs retirent en plus les manœuvres, règle §2).

Tests : nouvelle suite de régression (20 vérifications) qui **rejoue à
l'identique vos captures du 0616TCD et du piège anti-doublon**, + toutes les
suites revalidées — **260 tests verts**.

### Mise à jour v1.16 → v1.17 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »).
3. Relancer `demarrer.bat` → vérifier `LSS Tracking v1.17 — Plateforme
   prête` et « v1.17 » en pied de page (Ctrl+F5 si besoin).
4. **Vérification au prochain passage (≤ 15 min)** : 0616TCD affiche
   `Fin T2 10:35`, 4886TBU affiche le début orange de 11:00 (s'il roule
   encore), et les fins tronquées de la matinée se rallongent toutes seules.

## v1.16 — Correctif : le trajet EN COURS apparaît immédiatement + colonne Fin vide (05/08/2026)

Votre signalement de 11:55 (2736TCC déjà parti à 11:37 mais invisible dans la
grille — même cas sur beaucoup de camions) est corrigé, et vos deux réponses
sont appliquées :

1. **Un trajet commencé s'affiche TOUT DE SUITE en orange (provisoire).**
   - *Cause du bug* : MZoneX ne publie la distance et la fin qu'à la FIN du
     trajet. Tant que le camion roulait, la ligne restait cachée par la règle
     « manœuvre < 0,3 km », et le TCC affichait « — ». La règle est
     maintenant la vôtre (Addendum v1.9 §3) : **le début d'un trajet en cours
     est écrit immédiatement en orange** — c'est lui qui pilote le TCC, vous
     l'aviez souligné — **la distance n'est jugée qu'à la fin du trajet** :
     si le trajet terminé fait < 0,3 km, la ligne disparaît (manœuvre) ;
     s'il est valide, il est confirmé/remplacé par la donnée officielle à la
     même place.
   - Avec votre exemple 2736TCC, la grille affiche désormais :
     `Départ 06:15 · Fin 10:32 · Pause 1:05 · Début 11:37 (orange) · Fin vide`.
   - Le **TCC tourne dès le départ** (12:00 → TCC = 0:22 pour ce camion) —
     remise à zéro après pause ≥ 30 min, comme confirmé par votre réponse n° 1.
   - Sécurité : si la fin d'un trajet est manquée par le portail, la ligne
     officielle du passage suivant (toutes les 15 min) répare automatiquement.
2. **Colonne Fin VIDE pour le trajet en cours** (votre réponse n° 2) :
   fini la durée dans la case Fin — elle vit déjà dans la colonne **TCC**.
   Appliqué partout : grille détaillée, pastilles compactes (le début reste
   visible : `T2 11:37→`), fenêtre « Tous les trajets », exports Excel/PDF,
   historique. Cette décision remplace la note 3 de la v1.15 (durée temps
   réel) et le point CA-9 de l'Addendum v1.8 — c'est votre choix qui prime.
3. Au passage : l'outil de contrôle manuel (`python -m app.scrapers …
   --trajets --insert`) ne relit plus le portail deux fois de suite (la
   production n'était pas concernée — un seul passage toutes les 15 min).

Tests : nouvelle suite de régression (17 vérifications) qui **rejoue votre
chronologie exacte du 2736TCC** + toutes les suites revalidées — **239 tests
verts**.

### Mise à jour v1.15 → v1.16 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »).
3. Relancer `demarrer.bat` → vérifier `LSS Tracking v1.16 — Plateforme
   prête` et « v1.16 » en pied de page (Ctrl+F5 si besoin).
4. Dès le passage suivant du collecteur (≤ 7 min), les camions en route
   affichent leur début en orange et le TCC démarre.

## v1.15 — Addendum v1.9 : TCC coupé à 30 min, colonnes TCC/TCJ/TTJ en premier, « Lieu Arrêt » (05/08/2026)

Vos règles métier exactes, livrées :

1. **TCC — conduite SANS pause ≥ 30 min.**
   - Un arrêt < 20 min : ignoré (fusion, le trajet continue) ;
   - un arrêt **20-29 min** : pause officielle à l'écran (noire, ex. `0:25`),
     **déduite du TCJ**, mais **ne coupe PAS le TCC** ;
   - un arrêt **≥ 30 min** : **le TCC s'arrête et repart à zéro** au prochain
     trajet. Seuil paramétrable (`SEUIL_PAUSE_COUPURE_TCC`, 30 min) installé
     tout seul au démarrage.
   - Le **TCJ** (conduite depuis le 1er trajet, pauses ≥ 20 min déduites) et
     le **TTJ** (TCJ + tous les arrêts) étaient déjà conformes — vos exemples
     chiffrés du document (TCJ 8:10 / TTJ 9:00) sont reproduits à l'identique
     par les tests.
   - *Interprétation choisie (règle écrite + pseudocode + critère CA-3)* :
     le TCC est la **session courante**, remise à zéro après pause ≥ 30 min —
     l'exemple « TCC = 08:10 » du §1.1 étant incohérent avec ces trois sources.
     Dites-le moi si vous vouliez autre chose : c'est paramétrable.
2. **Colonnes TCC · TCJ · TTJ désormais AVANT les trajets** (écran + exports,
   partie D) — l'alerte se voit d'abord. Cellule **TCC colorée** : 🟢 normal ·
   🟠 **⚠ pulsant s'il reste ≤ 30 min** de conduite continue · 🔴 **🚨 si
   dépassement 4h30**. Nouvelle colonne **« Lieu Arrêt »** après « Arrêt
   final » (lieu du dernier arrêt connu). Structure : 25 colonnes en compact,
   51 en détaillé — écran = export = historique.
3. **Inchangés** (déjà conformes à l'Addendum) : trajet valide ≥ 0,3 km
   sinon manœuvre jamais affichée ni comptée ; noir seulement si trajet
   valide terminé + pause ≥ 20 min constatée ; orange + durée temps réel tant
   que le trajet roule (**CA-9 confirmé** : la colonne Fin affiche la durée,
   ex. `2:34` — le §3.3 « colonne vide » étant contredit par CA-9 + la
   checklist, j'ai gardé la durée validée en v1.8).

Tests : nouvelle suite Addendum v1.9 (**23 vérifications**, rejouant vos
exemples chiffrés exacts) + toutes les suites revalidées — **222 tests
verts**.

### Mise à jour v1.14 → v1.15 (2 minutes) — rien d'autre à faire
1. `Ctrl+C` dans la fenêtre noire du serveur.
2. Extraire le ZIP **par-dessus** `E:\projets\LSS-Tracking-Plateforme`
   (« Remplacer tous les fichiers »).
3. Relancer `demarrer.bat` → vérifier `LSS Tracking v1.15 — Plateforme
   prête` et « v1.15 » en pied de page (Ctrl+F5 si besoin).
4. La fenêtre noire affiche au démarrage
   `Seuil ajouté : SEUIL_PAUSE_COUPURE_TCC = 1800` : c'est le nouveau réglage
   « pause 30 min » qui s'installe tout seul.

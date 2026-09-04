RÉFÉRENCE IA DÉVELOPPEUR — LSS Tracking
Plateforme de Tracking Opérationnel (Transport Pétrolier — Madagascar)

Version : CONSOLIDÉE v2 (améliorée)
Date : 2026-08-06
Statut : Source de vérité unique pour l'agent IA développeur
Destinataire : Agent IA développeur (front-end / back-end de la plateforme LSS Tracking)
Référence originale : Addendum « REGLES CONSOLIDEES » du 2026-08-04

> ⚠️ **Ce document fait loi.** Toute implémentation (code, config, maquette) doit lui être conforme.
> En cas de contradiction avec un autre addendum, **c'est CE document qui prime** et l'agent doit le signaler explicitement (voir §A, point 9).

---

## 0. Corrections apportées vs addendum initial

Ce document améliore l'addendum « REGLES CONSOLIDEES » du 2026-08-04. Corrections de fond :

| # | Section | Problème dans l'original | Correction retenue |
|---|---|---|---|
| 1 | §1.1 TCC | Additionnait tous les segments (08:10) alors que le TCC reset après une pause ≥ 30 min | TCC = segment continu courant ; chaque segment comparé au seuil 4h30 |
| 2 | §1.2 TCJ | Erreur d'arithmétique : 14:00−11:30 noté 2:40 (réel 2:30) → total 8:10 | TCJ = 4:00 + 1:30 + 2:30 = 8:00 |
| 3 | §8 Attribution jours | Exemple « 00:15 → AUJOURD'HUI » contredit « < 02h00 → HIER » | Tout début < 01:00 → HIER, sans exception (exemple 00:15 corrigé) — arbitrage LSS du 06/08/2026 : 01:00 PARTOUT (aligne cette correction sur §8.2/§8.3/§9/§12.1) |
| 4 | §5 / §12 Arrêt | « vitesse = 0 » vs `SEUIL_VITESSE_ARRET` | Arrêt = vitesse ≤ `SEUIL_VITESSE_ARRET` (unifié) — valeur : 5 km/h du 06/08 au 13/08, **3 km/h depuis le 14/08/2026** (§0ter E, embouteillages) |
| 5 | §1.3 TTJ | Basé sur le TCJ erroné (08:50) | TTJ recalculé = 08:40 |

---

## 0bis. Arbitrages LSS du 06/08/2026 (mandants — priment sur tout le reste)

Sur les 4 contradictions relevées entre ce document et l'addendum initial, l'exploitant a tranché :

| # | Point | Arbitrage retenu | Conséquence |
|---|---|---|---|
| A | Fin de trajet / manœuvres | **§5.1 strict** : fin = PREMIER arrêt après un trajet valide ; la manœuvre (< 0,3 km) est **IGNORÉE PARTOUT** : elle ne soude pas deux trajets, ne prolonge pas la fin, n'interrompt jamais une pause, n'entre pas dans les compteurs §2. | 0616TCC affiche `5:31 · 8:24 · Pause 1:24 · 9:48 · 9:56` (exemple §5.2 verbatim) |
| B | Heure de bascule de journée | **01:00 PARTOUT** (contradiction interne levée au profit de §8.2/§8.3/§9/§12.1 : la correction #3 et §A.4 qui disaient 02:00 sont alignés ci-dessous). | jour = DATE(début) sauf début < 01:00 → HIER ; pré-consolidation 01h00 |
| C | CamtrackPro règle « en mouvement ≥ 20 min » (historique v1.5) | **SUPPRIMÉE** : un trajet est valide dès 0,3 km de distance, point final (§11.2 tel qu'écrit). `SEUIL_DUREE_MIN_MOUVEMENT_TRAJET` reste paramétré (15 min, §12.1) mais réservé aux flux de positions (§6.1). | 5716TBS : la ligne R4 (0,99 km / 10 min) redevient valide ; T2 garde sa propre fin |
| D | §6.1 « a vraiment commencé » (≥ 15 min & ≥ 12 km/h) | **Réservé aux flux de positions GPS** — INAPPLICABLE à MZoneX, qui n'émet rien entre « Début du trajet » et « Fin du trajet » : tout début de trajet s'affiche IMMÉDIATEMENT en orange « en cours » (décision du 05/08 confirmée). | §6.2 phase 1 : affichage immédiat, validation à la clôture |

---

## 0ter. Arbitrage LSS du 14/08/2026 (mandant — prime sur tout le reste sauf contradiction avec 0bis, inexistante ici)

| # | Point | Arbitrage retenu | Conséquence |
|---|---|---|---|
| E | Vitesse d'arrêt (`SEUIL_VITESSE_ARRET`) | **3 km/h** (au lieu de 5 km/h retenu le 06/08). Motif : camions avançant à **5-11 km/h dans les embouteillages** — ils doivent compter « EN ROUTE », pas « arrêté ». | Arrêt = vitesse ≤ 3 km/h PARTOUT : état « en route/arrêté » du Suivi, **fin de trajet §5.1 = premier arrêt à ≤ 3 km/h** (un bouchon ne clôture plus un trajet) ; les mentions « ≤ 5 km/h » du corps (tableau initial #4, §5.1, §12.1, §C.4, glossaire) sont alignées ci-dessous. Vigilance GPS : une vitesse fantôme ≤ 3 km/h au point mort reste de l'arrêt ; au-delà c'est du roulage (le bruit < 2 min est déjà absorbé par `SEUIL_BRUIT_GPS = 120 s`). Le seuil reste éditable dans Paramètres par l'administrateur (§12.2). |
| — | `SEUIL_VITESSE_MOYENNE` (12 km/h) | **INCHANGÉ** : le « 12 km/h » des règles précédentes n'est PAS le seuil arrêt/en route — c'est la vitesse MOYENNE de validation §6.1, règle dormante depuis l'arbitrage D (réservée aux flux de positions, inapplicable à MZoneX/CamtrackPro aujourd'hui, absente du code). Demande du 14/08 (« 12 → 3 ») interprétée comme visant le seuil opérationnel arrêt/route, soit `SEUIL_VITESSE_ARRET` → 3. | Aucun effet logiciel ; si un jour §6.1 est réactivé pour un flux de positions, réviser alors cette valeur. |

---

## 0quater. Arbitrages LSS du 14/08/2026 — « trajet en cours » et anticipation TCC (mandants)

Mise en situation exploitant : le but de la colonne TCC est d'ANTICIPER le temps de conduite continu ; or quand un camion part directement (sans manœuvre), l'onglet Trajets du portail est vide, et quand ses seuls trajets publiés sont encore des manœuvres invalides, sa vraie route a déjà commencé (visible en onglet Événements DÉMARRAGE/ARRÊT(2)) — la grille ne doit jamais rester vide pour un camion en route.

| # | Règle | Conséquence |
|---|---|---|
| R1 | **Une ligne « en cours » VIVANTE (sans fin) n'est JAMAIS rejetée pour cause de manœuvre au même début.** Le verdict manœuvre/trajet se rend à la CLÔTURE uniquement (§5.1). La garde anti-géants (v1.10) reste pleine sur les lignes CLÔTURÉES. | Fin du trou « camion en route, grille vide » : manœuvre de dépôt prolongée en vrai trajet (pause < 20 min fusionnée) alors que le portail n'a publié que la manœuvre. Audit de traçabilité : `trajet.veto_rejet_en_cours`. |
| R2 | **Rattrapage d'ouverture à chaque cycle Niveau 1** : si le dernier signal d'un camion MZoneX est un roulage récent (vitesse > `SEUIL_VITESSE_ARRET`, événement ≤ 15 min) et qu'aucune ligne « en cours » n'existe → (ré)ouverture datée du vrai DÉBUT_MOUVEMENT/REPRISE du jour (repli : dernier événement). Audits : `trajet.reouverture` (début conservé) / `trajet.ouverture_rattrapage`. | Couvre : plateforme lancée après le départ, événement manqué, lignes victimes de l'ancienne garde. |
| R3 | **Le TCC provisoire (colonne TCC, orange) se calcule TOUJOURS depuis la ligne « en cours »**, même si l'onglet Trajets est vide — garanti non-interrompu par R1+R2 tant que le camion roule. | L'objectif « anticiper le TCC sans attendre la validation par trajet » est atteint (§6.2 phase 1 rendu inconditionnel). |
| O1 | **Pré-alerte « pause non prise » à 4h00** (`SEUIL_TCC_PREALERTE = 4h00`, paramétrable §12.2 ; avant : 85 % du seuil, soit 3h49:30). Message : « Pré-alerte TCC (pause à prévoir) — … — seuil 4h30 dans XhYY ». | Anticipation réelle au lieu de la seule alerte de dépassement. |
| R4 | **CamtrackPro : la ligne « en cours » vient du rapport « Detail Trajet »** (preuve exploitant 14/08, capture 7306TCE : la dernière ligne du rapport est le trajet en cours, sa fin = dernière position connue). Mécanisme EXISTANT depuis v1.9 (§6.2 phase 1 — « PASSE 0 ») : fin provisoire < 20 min → importée **orange « en cours »**, ni rejetée ni validée, jugée à sa clôture effective (verdict manœuvre à la clôture, cohérent R1) ; TCC provisoire calculé dès l'import (R3). Cadence = `FREQUENCE_SYNC_TRAJETS_VALIDES` (**15 min conservées — arbitrage du 14/08**, vs 7 min MZoneX). Rattrapage R2 non applicable (pas de flux d'événements Camtrack) ET non requis : le rapport porte déjà la ligne. | Les 13 camions CamtrackPro affichent leur trajet en route avec ≤ 15 min de délai. Exemple 7306TCE du 14/08 : manœuvre 05:03 (0,00 km) ignorée §7 · T1 05:27:46→08:10:19 (79,94 km) · pause « 0:34 » (coupe TCC) · T2 08:44:44→en cours 🟠 22,12 km. |
| D0 | **Normalisation anti-fautes des identifiants** : plaque ET boîtier saisis à la main sont normalisés — espaces retirés + MAJUSCULES (« 8076 TCB » → « 8076TCB », « OBC-8076 TCB » → « OBC-8076TCB » ; **les tirets/parenthèses sont conservés**) — à la création, à la modification, ET par réparation automatique au démarrage des fiches existantes (collision → fiche non touchée + alerte ⓘ). | Sans cela, une fiche saisie avec espace ne correspond jamais aux relevés : le véhicule restait à « — » pour toujours (cause identifiée du constat « ajout manuel inexploité »). |
| D1 | **Découverte automatique des véhicules** : tout identifiant plaque valide (`^\d{3,4}[A-Z]{2,3}$` — ex. 8076TCB) vu par un collecteur (MZoneX Événements/Trajets, CamtrackPro rapport) et inconnu en base → **fiche créée automatiquement** (plateforme = portail détecteur MZX/CTPRO, ACTIF, exploitation immédiate) + alerte ⓘ « Nouveau véhicule détecté » + audit `vehicule.auto_cree`. Idempotent (existant renvoyé tel quel). | Un camion ajouté sur le portail GPS est exploité dès le relevé suivant — plus aucune donnée perdue faute d'enregistrement préalable. La fiche est à compléter à la main (marque, capacité, chauffeur). |
| D2 | **Découverte automatique des chauffeurs** : nom de conducteur vu (colonnes « Conducteur » / « dernier conducteur » MZoneX) et inconnu → **fiche Conducteur créée** (matricule `AUTO-xxxxxxxx`) + alerte ⓘ + audit `conducteur.auto_cree`. **L'affectation au véhicule reste MANUELLE** (remplacements quotidiens = décision métier). | Référentiel chauffeurs auto-alimenté ; contrôle humain conservé sur les affectations. |
| D3 | **Journal des inconnus** : tout identifiant non retenu pour D1 (forme non-plaque : lignes de totaux, parasites…) est visé en fenêtre noire (`§0quater D3 — identifiant non retenu…`) au lieu du silence « debug » d'avant. | Plus aucune perte silencieuse : un nouveau camion non capté se voit dans le journal. |

## 0quinquies. Arbitrages LSS du 14/08/2026 (fin de matinée) — recensement des portails (mandants)

Constat métier 14/08 ~11h00 : 2066TBP, 7136TCE, 7206TCE, 7766TBL (et 6256TCE) ont une fiche LSS mais n'apparaissent dans AUCUNE liste publiée du portail MZoneX (« aucune option véhicule » toutes les 7 min). D1 (§0quater) ne découvre un véhicule qu'à sa première DONNÉE (événement ou trajet) ; un camion sans activité du jour — ou rangé sur la mauvaise plateforme dans notre référentiel — restait invisible et, s'il vit sur CamtrackPro, n'était lu par AUCUN des deux collecteurs.

| # | Règle | Conséquence |
|---|---|---|
| D4 | **Recensement de la flotte telle que la PUBLIENT les portails, à chaque synchronisation (Niveau 2).** Les listes déroulantes véhicules de MZoneX (combo « Rechercher véhicules » du groupe courant) et de CamtrackPro (combo « objet » du rapport) sont lues en entier ; chaque libellé est ramené à sa plaque (`plaque_depuis_libelle_portail` : tête avant « ( » ou « - », espaces retirés, garde `^\d{3,4}[A-Z]{2,3}$`). Pour chaque véhicule publié : inconnu → **fiche créée avec la plateforme du portail d'origine** (extension de D1, alerte ⓘ + audit) ; connu mais rangé sur l'autre plateforme → **bascule AUTOMATIQUE de `plateforme_gps`** (arbitré par l'exploitant le 14/08 : correction auto, pas simple alerte) avec alerte ⓘ + audit `vehicule.plateforme_basculee` ; déjà rattaché → rien. Les ACTIFS vus sur AUCUN portail sont signalés en fenêtre noire (boîtier inactif / rattachement de groupe à faire côté portail), une seule fois tant que la liste ne change pas (anti-spam §10). **Jamais de suppression automatique.** | Plus aucun trou silencieux : un camion est exploité dès qu'un portail le publie, même sans trajet du jour ; une fiche mal rangée (MZX/CTPRO) se corrige toute seule au prochain cycle ; un boîtier non rattaché au groupe est pointé nommément au lieu d'avertissements répétés. |
| D5 | **Mots non-personnes dans la colonne chauffeur** (« garage », « dépôt », « station », « parking », « atelier » — liste surchargeable via `CONDUCTEUR_MOTS_IGNORES`) : ne créent JAMAIS de fiche chauffeur (D2 filtré). Les fiches déjà créées avec un tel mot (ex. « Garage LSS 2 », v1.21) sont passées INACTIVES au démarrage + alerte ⓘ explicative — réactivation manuelle possible, **jamais de suppression**. | Fin des faux chauffeurs venus des libellés de lieu que le portail glisse dans la colonne conducteur (constat réel 14/08). |
| —  (correctif technique, pas un changement de règle) | **Garde de fraîcheur de l'auto-réparation v1.17** : un « Début du trajet » connu mais resté sans ligne n'est ré-ingéré que s'il a moins de 45 min (`REPARATION_AGE_MAX_S = 2700`, surchargeable). Au-delà sans « Fin » ni mouvement, le boîtier est muet (contact coupé, camion garé) — constat réel 14/08 : va-et-vient re-création / rejet manœuvre toutes les 7 min sur 7936TCB et 8806TCB. Dès un roulage réel, R2 (§0quater, signal ≤ 15 min) reprend la main. Alignement de l'ancien mécanisme sur la fraîcheur R2, aucune règle métier modifiée. | Fin du spam de lignes fantômes pour les boîtiers muets, sans jamais cacher un vrai trajet en cours. |
| —  (mémorandum) | **Plaque 7766 = 7766TBL (lettre L)** — confirmé par l'exploitant le 14/08 (le « 7766TBM » du message était une faute de frappe). Aucun traitement technique. | La fiche existante reste la référence. |

**Complément D4 du 14/08 (après-midi, même session d'arbitrage)** — preuve exploitant (capture de l'onglet « Statuts » CamtrackPro) : le filtre « objet » du rapport **omet des unités** que le compte publie pourtant (6256/7136/7206TCE, 7766TBL y étaient invisibles alors qu'elles figurent dans « Statuts »). Le recensement CamtrackPro lit donc désormais, **EN UNION** avec la combo du rapport, la **page « Statuts »** (liste complète du compte : extraction des plaques par motif dans le texte affiché — insensible au DOM, défilement borné du panneau). La règle D4 est inchangée (« la flotte telle que les portails la PUBLIENT ») : seule la source de lecture est doublée, avec repli combo en cas d'échec et réciproquement. Plaque confirmée au passage : « 7766 TBL-SINOTRUK HOWO » — le TBL arbitré.

**Correctifs du 14/08 (soir — v1.24, lecture & moteur ; AUCUNE règle métier modifiée)** — trois défauts mis en évidence par le journal réel v1.23 (15:34–15:47), corrigés et couverts par tests. Déclarés ici au titre de la transparence (§A.9) ; D1–D5 et le Complément D4 restent la loi, seules leur LECTURE et leurs gardes techniques changent :

1. **Lecture « Statuts » (CamtrackPro) — lettres des plaques restaurées.** L'extraction par motif ne gardait que le compteur numérique (« 6256 » au lieu de « 6256TCE ») : les 19 unités vues étaient écartées une à une par la garde D3 et aucune bascule ne se produisait. Nombre + lettres sont désormais concaténés AVANT la garde plaque (`plaque_depuis_libelle_portail` / `^\d{3,4}[A-Z]{2,3}$`).
2. **Recensement MZoneX — liste déroulante virtualisée + garde anti-liste-partielle.** La combo Wijmo ne matérialise qu'une dizaine d'options visibles : 10 camions lus au lieu de 37, et la dénonciation des « absents » accusait à tort 34 camions sains (dont 4006TBS, lu dans le même cycle !). Le recensement MZoneX récolte désormais en **11 passes filtres** (filtre espace puis chiffres 0–9, défilement interne borné des panneaux déroulants) avec union des lectures. **Garde additionnelle** : si le recensement d'un portail couvre moins de **80 %** de ses véhicules ACTIFS du référentiel, il est déclaré **partiel** (trace WARNING explicite, ex. « MZONEX (10/37) ») et la dénonciation des absents est **SUSPENDUE pour ce portail** ce cycle — on ne dénonce jamais sur une liste incomplète.
3. **Auto-réparation v1.17 — garde de mouvement.** Le garde-fou naïf « vitesse > 0 » était inapplicable : le portail publie les « Début du trajet » à 0 km/h y compris pour un camion réellement en route (le véto aurait cassé les vrais trajets). La ré-ingestion d'un « Début » sans ligne n'a désormais lieu que si la **dernière position connue du camion prouve le roulage** : vitesse > seuil d'arrêt (3 km/h, §0ter) et signal ≤ 20 min — la condition R2 (§0quater). Un « Début » seul à 0 km/h (blip de contact : 0926TBV 15:14:45, 8076TCB 15:09:55 le 14/08) ne recrée plus de ligne fantôme rejetée « manœuvre » au cycle suivant. Conséquence testuelle : le piège §3 des tests v1.17 suppose désormais que le camion du piège **roulait** (preuve de mouvement jointe) ; sémantique métier inchangée (réparer un vrai trajet manqué, jamais un blip). La garde de fraîcheur 45 min (§0quinquies) est cumulée, pas remplacée.
4. **D5 — trace unique.** La fenêtre noire n'affiche le blocage d'un mot non-personne (ex. « Garage LSS 2 ») qu'**une fois par libellé et par session** (anti-spam §10) ; le blocage D5 lui-même est inchangé.

Attente résiduelle déclarée à l'exploitant : si le rapport CamtrackPro refuse encore une unité basculée (« aucun objet ne commence par … »), le réglage est côté portail (groupe/droits du compte) — la fiche LSS sera déjà correctement rattachée grâce à D4.

## 0sexies. Arbitrages LSS du 20/08/2026 — connecteurs API des portails (mandants)

Origine : le responsable SAV Camtrack a confirmé à l'exploitant que les plateformes (MZoneX/MZone et CamtrackPro) disposent d'**API OUVERTES utilisables sans formalité** (« un développeur peut les trouver »). Exploration technique du 20/08/2026, aboutie et validée avec le compte LSS existant :

- **MZoneX** : API OData v4 publique `https://live.mzoneweb.net/mzone62.api/` (~277 ensembles) + serveur d'identité OAuth2/OIDC (IdentityServer4) `https://login.mzoneweb.net` ; authentification par flux « code + PKCE » (client public « mz6 PKCE », étendues `openid mz_username mz6-api.all offline_access`) rejouée automatiquement avec les identifiants configurés ; jeton d'accès 1 h + jeton d'actualisation entretenus sans intervention. Validée en direct : `Vehicles` = 37 (groupe LSS), `LastKnownPositions` = 37, `Trips` jour complet à la seconde, `Events` ≈ 1 signal/boîtier/60-90 s, `Drivers` = 95.
- **CamtrackPro** : le portail est un **Wialon (Gurtam) blanchi** ; API Remote publique `https://hst-api.wialon.com/wialon/ajax.html`. Gurtam refuse le mot de passe en API : un **jeton (token) créé dans l'interface** est requis — **Fourni par l'exploitant le 20/08/2026** (formulaire oAuth du portail : `hosting.camtrack.net/login.html?client_id=…&access_type=-1&duration=0`, clause « access_token » de l'adresse de retour). Validation directe (v1.26, 20/08) : session `token/login` OK ; 19 unités avec dernier message (position+vitesse, flags 1025) — borne §5 (« pas de temps réel fiable côté Camtrack ») levée par A4 ; gabarit **« Detail Trajet Vehicule »** (ressource « LPSA PL », id 9, table « Detail Trajet ») — 45 trajets officiels du jour lus, 6256/7136/7206TCE et 7766TBL inclus (les 4 basculés D4 enfin exploités) ; recensement D4 (19) en UN appel.

| # | Règle | Conséquence |
|---|---|---|
| A1 | **Cadence cible : toutes les 60 secondes.** `COLLECTOR_PERIODE_S=60` (paramètre technique §12). En dessous, inutile : les boîtiers n'émettent que toutes les 60-90 s et le portail lui-même se rafraîchit à 30 s. | Suivi quasi-direct (≈ 1 min) au lieu de 7 min. |
| A2 | **API en PRINCIPAL, lecteur d'écran en SECOURS AUTOMATIQUE.** Toute panne de l'API replie le cycle EN COURS sur le scraping Playwright historique (et réciproquement) ; jamais de perte ni de double comptage (anti-rejeu partagé). Coupable via `MZONEX_API_ENABLE=0` (diagnostic §10). | Robustesse identique à aujourd'hui, avec la fraîcheur en plus. |
| A3 | **MZoneX via l'API OData.** N1 : `Events` en fenêtre incrémentale (reprise dernier signal − 3 min, fin = maintenant − 20 s, plafond 30 min à la 1ʳᵉ passe §10) — les points entrent dans le MÊME `ingest_event` (§7.1, seuils §0ter, machine états souveraine) ; N2 : `Trips` du jour civil local (contrat réconciliation inchangé) ; D4 : `Vehicles` en UN appel (supersède la moisson écran virtualisée corrigée en v1.24). Aucune règle métier (§1-§12, §0bis→§0quinquies) modifiée : seul le TRANSPORT des données change. | Fin des fragilités de lecture d'écran MZoneX (virtualisation, sessions, lenteurs) ; données identiques, horodatées à la seconde. |
| A4 | **CamtrackPro via l'API Wialon — ACTIVÉ le 20/08/2026 (v1.26).** Jeton de l'exploitant (`CAMTRACKPRO_TOKEN`, droits complets, validité illimitée, conservé dans `backend\.env` comme les autres secrets). N1 : dernier message par unité à 60 s (incrémentalité native par anti-rejeu ; échec → cycle reporté, sans repli écran possible §5) ; N2 : gabarit « Detail Trajet Vehicule » par unité (mêmes valeurs officielles que l'écran) + repli écran A2 ; D4 : liste des unités en UN appel (supersède Statuts ∪ combo v1.23). | Tous les camions (37 MZoneX + 19 CamtrackPro) vus à la minute ; les basculés D4 fournissent leurs données. |

---

## 0septies. Arbitrages LSS du 20/08/2026 (fin de matinée) — exploitation avancée des API : conducteurs & conduite (mandants)

Origine : l'exploitant a choisi la piste « Conducteurs & conduite » parmi les pistes d'approfondissement des API (§0sexies). Reconnaissance en LECTURE SEULE du 20/08/2026 (aucune donnée modifiée) ayant précédé l'inscription :

- **MZoneX** : `Trips` porte `driver_Id`/`driver_Description`/`driverKeyCode` + compteurs d'écoconduite par trajet (`numberOfSpeedingExceptions`, `…HarshBraking…`, `…ExcessiveAcceleration…`, `…ExcessiveIdle…`, `…ExcessiveRPM…`, …, `maxSpeed`). Mesuré : **105 trajets du jour dont 81 badgés (77 %)**, 28 conducteurs distincts. `Events` porte `driverKeyCode` en DIRECT (79/105 lignes sur 4 min). `Places` = **749 géozones** (centre + rayon `buffer`). `Drivers` = 95 fiches clés. 4 camions jamais badgés le 20/08 (0616TCD, 0936TBV, 7946TCB, 9856TCD).
- **CamtrackPro** : ressource « LPSA PL » = **155 conducteurs** (codes clé iButton ; nombreux « Nouveau conducteur » jamais renommés) + **450 géozones** (centre + emprise). Le rapport N2 id 9 porte déjà : vitesse maximale, **ralenti moteur**, **conducteur** (noms observés en production dès le premier cycle v1.26). 21 autres gabarits disponibles (id 6 « Detail Trajet Chauffeur », id 5 « Analyse détaillée excès de vitesse », id 17 « Resumé exces de vitesse », …).

| # | Règle | Conséquence |
|---|---|---|
| B1 | **Périmètre v1.27 : attribution automatique + carnet de conduite + alertes en direct.** (La piste « hygiène des référentiels » est écartée pour l'instant ; « carburant » et « géozones/situations » restent des pistes ouvertes non arbitrées.) | Trois chantiers, une livraison. |
| B2 | **« Le badge fait foi » — avec exception clés de service.** Le conducteur publié par le portail s'inscrit sur le trajet validé (origine « badge », journalisée) et reste **modifiable à la main**. EXCEPTION arbitrée : libellés **« Nouveau conducteur »** et **« garage LSS »** (clés de service — comparaison insensible casse/accents) ⇒ badge écarté (journal §10) et **la saisie manuelle fait le travail** : elle n'est jamais écrasée. Liste `CONDUCTEUR_BADGE_IGNORES` extensible par l'administrateur (§12). Les non-personnes D5 (§0quinquies) restent filtrés en amont. | Fini les attributions à la main pour les chauffeurs badgés ; aucune pollution par les clés de service. |
| B3 | **Carnet de conduite (page « Conduite »), deux vues arbitrées.** Vue par chauffeur : trajets, km, infractions par type (vitesse, freinage brusque, accélération, ralenti, sur-régime, autres), total et **infractions/100 km**. Vue « trajets noirs » : détail par trajet (qui, quand, vitesse max, types d'infractions). Sources : compteurs natifs des `Trips` MZoneX ; côté CamtrackPro : vitesse max + ralenti moteur du rapport id 9 (les compteurs détaillés CamtrackPro via les gabarits 5/17 sont une extension ultérieure possible sans nouvel arbitrage, déclarée en CONFORMITÉ). **Aucune colonne ajoutée aux exports figés 25/51 (§9 intouché)** : le carnet est consultable à l'écran ; export dédié uniquement sur demande expresse. | Le chef voit qui conduit comment, sans retraitement. |
| B4 | **Alerte vitesse en direct : 45 km/h hors géozone ; seuils des portails en géozone.** Hors géozone : vitesse > **45 km/h** (paramètre `CONDUITE_VITESSE_HORS_ZONE=45`, §12) confirmée sur **2 signaux consécutifs** (anti-blip) ⇒ alerte `VITESSE_LIVE` (1 par épisode ; épisode clos après 2 signaux ≤ 45 ou 10 min sans signal). Dans une géozone de l'union des deux portails (test cercle : centre + `buffer` MZoneX ; centre + **rayon inscrit à 70 % de l'emprise** CamtrackPro — choix d'implémentation déclaré) ⇒ pas d'alerte 45 : **les seuils des portails gouvernent** et restent consommés via les compteurs officiels (B3). Cache des zones rechargé au démarrage puis toutes les 6 h ; portail de zones indisponible ⇒ point considéré HORS zone (choix déclaré, journalisé). | Alerte au bon endroit, silence là où le portail règle déjà. |
| B5 | **« Camion roule sans badge ».** MZoneX, EN DIRECT : véhicule > 3 km/h (§0ter) sans `driverKeyCode` vu sur les 10 dernières minutes de signaux ⇒ alerte `SANS_BADGE` (1 par épisode de mouvement ; réarmement après 30 min à l'arrêt). CamtrackPro : le flux continu ne publie pas la clé à chaque message → **pas d'alerte directe fiable** (§10 : on n'invente pas une donnée absente) ; le constat se fait au Niveau 2 (trajet sans conducteur) et remonte dans le carnet. | Vols d'utilisation détectés à la minute sur 37 camions ; honnêteté sur les 19 autres. |
| B6 | **Transport vs métier.** Comme §0sexies : aucune règle métier antérieure (§1→§12, §0bis→§0sexies) n'est modifiée ; §0septies AJOUTE des données (conducteur badge, compteurs écoconduite, alertes) sur les mêmes flux. La machine à états, les seuils de validité, la réconciliation et les formats d'audit restent souverains et inchangés. | Compatibilité totale, traçabilité totale. |

---

## 0octies. Arbitrage LSS du 20/08/2026 (mi-journée) — rectificatif fuseau horaire CamtrackPro (mandant)

Origine : signalement de l'exploitant le 20/08/2026 avec captures de preuve — les heures CamtrackPro affichées par la plateforme (v1.26) ont **3 heures de retard** sur le portail. Constat mesuré en lecture seule le même jour : le serveur Wialon rend les textes horaires du rapport « Detail Trajet » en **UTC** (cellule brute « 2026-08-20 02:24:44 », epoch associé « v » = 1787192684 = 05:24:44 affiché à l'écran — décalage exact, constant, vérifié à la seconde sur plusieurs lignes ; le paramètre serveur de fuseau `tzOffset` est ignoré par `exec_report`, test fait). L'hypothèse initiale de §0sexies A4 (« textes déjà locaux ») était donc fausse : c'est une **correction de bug**, pas un changement de règle — le §0sexies reste intégralement valable (source, cadence, gabarit, mapping des colonnes).

| # | Règle | Conséquence |
|---|---|---|
| C1 | **Heure de vérité + réparation unique.** (a) Lecture : l'instant retourné par le rapport est PRIORITAIREMENT l'epoch UTC « v » des cellules dict (→ heure locale Antananarivo, jamais DST) ; une cellule texte seule (« YYYY-MM-DD HH:MM:SS ») est relue comme UTC puis convertie — choix d'implémentation justifié par le constat mesuré, déclaré ici. (b) **Réparation des données du 20/08 — option LSS choisie : « correction automatique +3h ».** Au premier démarrage v1.28, les trajets N2 CamtrackPro stockés en UTC (instants ≥ 19/08 21:00 UTC-naïf, fenêtre qui exclut toute donnée CAMTRACKPRO antérieure venue du repli écran) sont **décalés de +3h00 exactes** ; si la bascule 01h00 (§8.1) change le jour d'attribution, la ligne est rattachée au suivi du bon jour ; **une seule fois** (marqueur d'audit, transaction unique tout-ou-rien) ; **aucune donnée supprimée** — chaque heure fausse est remplacée par l'heure vraie, journalisée (§10). La grille, les chaînes et les compteurs se recalculent au cycle suivant par les moteurs existants (écran = export = archive §A.2). Aucune règle §1→§12 / §0bis→§0septies n'est modifiée. | La grille du 20/08 redevient juste au démarrage, sans retraitement et sans risque. |

## 0nonies. Amendements LSS du 22/08/2026 — « AMÉLIORATIONS v3 » (mandants — ils priment sur les sections qu'ils citent et sur §0bis B et §0ter E, amendés ci-dessous)

Origine : l'exploitant a remis le document `AMELIORATIONS_REGLES.md` (v3, daté 21/08/2026, « CE DOCUMENT PRIME ») assorti d'un supplément de la Référence. **Point de méthode inscrit (D0)** : le supplément joint reposait sur une copie ANCIENNE de la loi (sans §0bis→§0octies) ; il n'a donc PAS été repris comme base — la présente Référence consolidée reste la loi unique. Les points d'attention §8 du document v3 sont réputés tranchés tels quels (R1→R5 « validés par le PO ») et les quatre conflits avec des arbitrages antérieurs ont été arbitrés par l'exploitant le 22/08/2026 (réponses C1→C4).

| # | Règle | Conséquence |
|---|---|---|
| C1 | **Consolidation à 23:59:59 + SPLIT à minuit (AM-3 intégral).** `HEURE_PRE_CONSOLIDATION` = 23:59:59 (§12.1). Un trajet **encore en cours** à 23:59:59 est découpé : segment A `[début → 23:59:59]` au jour J (consolidation), segment B `[00:00 → fin]` au jour J+1 — sans pause artificielle. La règle §0bis B « début < 01h00 → HIER » et la bascule 01h00 sont **ABROGÉES** : `jour = DATE(heure_debut)`, point final (les mentions « < 01h00 »/« < 02h00 » du corps sont réputées supprimées — §8.2/§8.3, §9.1→§9.3, §12.1). Conséquence d'implémentation déclarée : un trajet reçu DÉJÀ CLÔTURÉ qui franchit minuit (début J, fin J+1) est découpé de la même manière à l'écriture — seule façon de tenir « chaque segment rapporte ses propres compteurs ». **R1 maintenu** : le TCC traverse minuit (pas de remise à zéro sans pause ≥ 30 min ; le segment B continue le même compteur — l'ancrage croise la veille quand le premier segment du jour commence à 00:00). | Fin du jour « à cheval » ; chaque journée comptabilise exactement ses heures. |
| C2 | **AM-6 borné : arrêts = portails À LA CONSOLIDATION ; le DIRECT garde la machine 3 km/h (§0ter E maintenu sur ce seul périmètre temps réel).** En consolidation (jour figé, historique), les arrêts se lisent sur les trajets publiés par les portails — `SEUIL_VITESSE_ARRET` n'y joue plus (§5.1/§12.1 réduits au temps réel). En temps réel, rien ne change : ligne orange « en cours » §0quater/v1.16, alerte « roule sans badge » §0septies B5 (v > 3 km/h) inchangés. | Le direct ne perd rien ; le figé suit le portail. |
| C3 | **AM-5 : onglet Infractions = vitre de lecture filtrée EXTERNE — vide tant qu'aucune source externe n'est branchée.** Plus AUCUNE écriture locale dans `Infraction` (ni dépassements TCC/TCJ/TTJ, ni excès de vitesse/freinage calculés) ; toutes les alertes locales vont à l'**onglet Alertes**, comme aujourd'hui. Les lignes Infraction déjà stockées RESTENT en base (jamais de suppression, §A) mais ne s'affichent plus dans l'onglet (elles n'étaient pas « pré-filtrées par une autre plateforme »). Archives déjà figées : inchangées (§A.2). | Zéro bricolage local dans Infractions ; rien n'est perdu. |
| C4 | **Priorité livraison : v3 D'ABORD (v1.29), la réparation du 20/08 (0906TBV) ensuite** — la vérité reste au portail, rattrapable (AM-4/R5). | Voir plan. |

| # | Règle AM inscrite (texte v3 adopté tel quel, sauf C2/C3) | Remplace dans la Référence |
|---|---|---|
| AM-1 | **TCJ = (maintenant − départ) − Σ TOUS les arrêts (toute durée).** « Arrêt » = intervalle entre deux trajets valides (et, en consolidation, tel que publié par les portails — C2) ; les spans de manœuvres sont des arrêts. « Maintenant » = horloge réelle (en cours) ou `heure_fin` (terminé/consolidé). TCJ = la conduite où le véhicule ROULE. **T1 : TTJ = maintenant − départ** (= TCJ + Σ arrêts), borne 12h00 conservée. TCC : session courante depuis la dernière pause ≥ `SEUIL_PAUSE_COUPURE_TCC` (30 min) — une manœuvre ≥ 30 min compte comme pause (AM-6) ; traversée de minuit sans reset (R1). | §1.2, §1.4 (ligne TCJ), §2.2 |
| AM-2 | **Affichage découplé du calcul — jamais d'effacement.** L'écran montre TOUS les trajets valides (chacun sa ligne ; plus de fusion d'affichage) et **seules les pauses ≥ 30 min** (cellule vide sinon). Le stockage conserve TOUTES les heures (audit) ; seule la présentation filtre ; aucune colonne n'est jamais vidée. R2 : une manœuvre (< 0,3 km) est conservée en base, masquée, JAMAIS comptabilisée (ni l'heure, ni le trajet). Les formats figés 25/51 colonnes (§9, §0septies B3) gardent leurs colonnes — seul le contenu suit l'affichage (écran = export = archive §A.2 maintenu). Le mécanisme de couleur (orange « en cours » immédiat §0quater/v1.16 ; noir une fois l'arrêt constaté depuis `DUREE_MIN_PAUSE_VALIDE`) n'est PAS supplanté. | §3.2, §4.1, §4.2 |
| AM-3 | Voir C1 (split minuit, bascule 01h00 abrogée). | §8.2, §8.3, §9.1→§9.3, §12.1 |
| AM-4 | **Catch-up au démarrage (localhost).** Au boot : consolider TOUS les jours non consolidés (du plus ancien au plus récent, idempotent — un jour déjà archivé n'est jamais réécrit, §A.2) ; chaque jour est rempli des données RÉELLES relues aux portails (MZoneX Trips + CamtrackPro, fenêtres de dates arbitraires) puis consolidé (C1) et archivé ; le calcul s'arrête quand tout est à jour. **Garde-fou R5** : si un jour est réellement absent des deux sources (rien relu, rien en local), on n'écrit RIEN de partiel — journal + avertissement, la journée reste à reprendre au prochain démarrage. | §9 (déclenchement) complété |
| AM-5 | Voir C3. | Nouveau (§6.4 cesse d'écrire dans Infraction ; §11 inchangé pour l'extraction) |
| AM-6 | Voir C2. Au démarrage d'une journée, une manœuvre (< 0,3 km) est ignorée : **le départ = 1er mouvement valide** ; « maintenant » R4 comme AM-1. | §5.1, §7, §12.1 (seuil vitesse réduit au direct), glossaire |

## 0decies. Arbitrages LSS du 24/08/2026 — réparation embarquée des journées abîmées par l'ancienne bascule 01h00 (v1.30, mandants)

Origine : signalement de l'exploitant le 24/08/2026 (captures à l'appui) — dans l'Historique, des fins de trajet du 21/08 frappent « 01:00 » pile (FIN T1 01:00 pour 0916TBV avec **TCJ 19:36** aberrant ; FIN T3 01:00 pour 4876TBU alors que le portail MZoneX montre une fin réelle à **20:38:36**). Cause établie : le poste était **éteint le vendredi soir** → les fins du soir jamais reçues ; la v1.28, redémarrée samedi matin, a appliqué l'ancienne bascule 01h00 (§0bis B, abrogée depuis §0nonies C1) et **archivé la journée avec ces valeurs** ; la v1.29 ne réécrit jamais une archive (§A.2) → le défaut reste visible. S'y ajoute la journée du **20/08** (0906TBV, trajet du milieu 10:19→13:44 perdu — reportée par §0nonies C4). L'exploitant ne peut pas remettre sa base (`lss.db`) ; la vérité reste disponible aux portails (R5 : historiques jamais effacés côté portails) → **la réparation s'exécute chez lui, embarquée dans la v1.30**.

| # | Règle | Conséquence |
|---|---|---|
| D1 | **Périmètre — contrôle complet depuis le 19/08/2026.** La v1.30 revérifie CHAQUE journée du 19/08/2026 (borne incluse) jusqu'à la veille du premier démarrage v1.30 : relecture des deux portails (mêmes mécanismes qu'AM-4) puis comparaison trajet-à-trajet avec la base (tolérance d'appariement identique à la réconciliation). Sont réparées uniquement les journées **avec écart** : trajet du portail sans correspondant en base (trajet perdu), trajet en base sans correspondant au portail (fantôme/clôture forcée), ou fin divergeant au-delà de la tolérance — la fin « 01:00 » est la signature de l'ancienne bascule. Journée déjà conforme : **aucune écriture**, archive intacte (§A.2 préservé au maximum). | Toutes les blessures de l'ère 01h00 sont traitées ; les jours sains ne bougent pas d'un octet. |
| D2 | **Réécriture exceptionnelle des archives réparées — EXCEPTION PONCTUELLE à §A.2, adoptée par l'exploitant.** Pour chaque journée réparée : l'archive est régénérée depuis l'état réparé (écran = export = archive maintenu, §A.2) ; un journal d'audit **AVANT/APRÈS** (par véhicule : trajets, fins, TCJ/TTJ) est écrit ; une alerte INFORMATION récapitule chaque journée réparée. **Jamais de suppression de données** : les lignes fautives d'origine restent en base, marquées REJETÉES avec lien d'audit « remplacée par relecture portail v1.30 » (extension d'AM-2/R2 : masquées en grille, jamais effacées). Aucune autre réécriture d'archive ne sera jamais faite hors de ce périmètre. | L'Historique devient exact ; la trace prouve chaque retouche. |
| D3 | **Déclenchement automatique, une seule fois, au premier démarrage de la v1.30** (tâche de fond après les migrations ; marqueur global d'achèvement + marqueur par journée). Une journée sautée pour portail indisponible (garde-fou R5 : rien d'écrit en partiel) est reprise au démarrage suivant — comme AM-4, la réparation « s'arrête quand tout est à jour ». Récapitulatif lisible dans l'onglet **Alertes**. | Zéro manipulation pour l'exploitant. |
| D4 | **Renfort du cycle de minuit (extension d'implémentation d'AM-4, déclarée).** Avant de figer la veille à 23:59:59, le cycle quotidien RELIT d'abord les portails sur la veille (même mécanisme que le catch-up AM-4) afin de récupérer les fins de soirée manquées ; si les portails sont injoignables, la consolidation se fait quand même sur la base (minuit n'attend pas) avec note d'audit explicite. | Le scénario « PC rallumé juste avant minuit » ne peut plus figer une soirée incomplète. |

## 0undecies. Arbitrages LSS du 24/08/2026 (fin d'après-midi) — affichage allégé des trajets & positions nommées par les zones des portails (v1.31, mandants)

Origine : deux remarques de l'exploitant le 24/08/2026 (captures du suivi du 24/08 à l'appui). **Remarque 1** : la grille « bondée » par les micro-arrêts — cas réel 0936TBV : départ 09:54, arrêt 13:06, reprise 13:13 (arrêt de 7 min), arrêt 13:55, reprise 14:36 ; l'exploitant veut les colonnes **DÉPART 9:54, FIN T1 13:55, PAUSE 1 0:40, DÉBUT T2 14:36** avec `TCJ = maintenant − (14:36−13:55) − (13:13−13:06) − 9:54`, c.-à-d. le calcul « en arrière-plan, sans bonder les colonnes ». **Remarque 2** : la colonne J-1 affiche « position inconnue » alors que les portails nomment les lieux partout ; l'exploitant veut **toujours la position de J-1 dans sa colonne**, libellée par « le nom de lieu affiché sur carte à proximité » (formulation exacte). Cause établie de la remarque 2 : depuis le passage aux connecteurs API (§0sexies A2), ni les événements MZoneX ni les positions CamtrackPro ne géocodent → `adresse = None` partout ; s'y ajoute un **bug d'implémentation** : le `buffer` des Places MZoneX est en **degrés** et était consommé en **mètres** → les 749 zones MZoneX n'étaient de fait jamais reconnues (les 450 zones CamtrackPro, en mètres, fonctionnaient).

| # | Règle | Conséquence |
|---|---|---|
| E1 | **Fusion d'affichage des ruptures < 20 min — amendement partiel d'AM-2 (§0nonies), adopté.** Dans la grille (suivi, historique, exports Excel/PDF, modale des trajets), deux lignes séparées par un arrêt **strictement < 20 min** (`DUREE_MIN_PAUSE_VALIDE`) sont affichées comme **UNE seule ligne** : début = début de la première composante ; fin = fin de la dernière (cellule vide si celle-ci est en cours) ; statut/couleur = ceux de la dernière composante ; distance = somme des distances connues ; `segments` = somme ; numérotation re-séquencée ; « Nb trajets » = nombre de lignes fusionnées. La fusion s'applique à la **sérialisation unique** (`s_suivi`) : les archives nées après la v1.31 stockent la forme affichée (écran = export = archive strict, §A.2) ; **les archives antérieures (19-23/08 restaurées par v1.30 comprises) passent par la MÊME fusion à la lecture — idempotente — et s'affichent donc selon la même règle SANS AUCUNE réécriture** (§A.2 respecté, pas d'exception à arbitrer ici). Les tables de base (`Trajet`, `EvenementGPS`) conservent toujours les lignes réelles : rien n'est perdu pour la contre-vérification. | Écran = export = archive, une seule source (§A.2) ; la grille suit l'exemple 0936TBV au caractère près. |
| E2 | **Compteurs INCHANGÉS — TCC/TCJ/TTJ continuent de déduire TOUS les arrêts, toute durée** (AM-1, T1, seuil de coupure TCC 30 min intacts) : la fusion E1 ne touche jamais le calcul (`construire_journee` conserve les lignes réelles pour les compteurs). | TCJ = Σ durées de conduite réelle, exactement comme dans l'exemple de l'exploitant. |
| E3 | **Affichage des pauses : seuil inchangé à 30 min** (`SEUIL_PAUSE_COUPURE_TCC`) — une rupture de 20-29 min crée bien deux lignes (E1 ne la fusionne pas) mais sa durée reste « — » dans la colonne PAUSE (choix de l'exploitant du 24/08/2026). | Comportement d'affichage des pauses stable (pas de surprise). |
| E4 | **Positions nommées par les zones des portails (corrigé + étendu).** À l'ingestion de chaque point GPS, le libellé d'adresse = (a) le nom de la zone portail **contenant** le point (zone la plus spécifique = plus petit rayon, union MZoneX Places + géozones de la ressource CamtrackPro, cache 6 h existant §0septies B4) ; (b) sinon « **proche \<nom\>** » si la zone la plus proche est à moins de **5 km** (bord à bord) — formulation exacte de l'exploitant « nom de lieu affiché sur carte à proximité » ; (c) sinon les coordonnées « \<lat\>, \<lng\> » (4 décimales → jamais de cellule vide). Ce libellé alimente les événements, `arret_final`, `lieu_arrêt` et la colonne J-1 (§8 existant), partout où « position inconnue » apparaissait. **Correctif d'implémentation inscrit** : conversion du `buffer` MZoneX degrés → mètres (× 111 320) à la charge des zones — §0septies B4 inchangé dans son principe, mais les zones MZoneX jouent désormais réellement leur rôle B4 (alerte vitesse directe silencieuse en zone) ; le rayon servant au NOMMAGE est plafonné à 5 km (pas de nom de région géante), le seuil de proximité « proche » (5 km) et le plafond ne concernent que le libellé, jamais B4. | La colonne « position » se remplit avec le vocabulaire des portails ; l'alerte vitesse directe respecte enfin les zones MZoneX. |
| E5 | **Colonne J-1 automatique + rattrapage unique, adoptés.** À la création de la ligne du jour (§8) : `emplacement_j_moins_1` = libellé E4 de la **dernière position GPS connue de la veille** (complète la dérivation existante depuis `arret_final` quand celle-ci est vide ou restée « position inconnue »). Au **premier démarrage v1.31, une seule fois** (marqueur d'audit `position_j1_v131`), remplissage de la colonne J-1 de la journée courante depuis les événements GPS de la veille déjà enregistrés + ligne d'audit de synthèse ; idempotent. | Zéro manipulation ; la colonne J-1 est toujours remplie. |

---

## 0duodecies. Arbitrages LSS du 25/08/2026 — garde anti-double-comptage des compteurs & réactivation des lignes rattrapées (v1.32, mandants)

Origine : question de l'exploitant le 25/08/2026, capture de 10:02 à l'appui — la ligne **0926TBV affichait TCC 5:13 = TCJ 5:13 > TTJ 4:26** (DÉPART 05:34 orange, toutes les cases trajets « — »). **Cause établie** : un **chevauchement de lignes de 47 min** compté deux fois dans TCJ/TCC (5:13 = 4:26 d'amplitude + 0:47 en double), reproduction de laboratoire EXACTE obtenue avec les fonctions de production (`construire_journee` + `recalculer_temps` + `s_suivi`) : [ligne officielle MZoneX 05:34:08→09:35:48 validée] + [ligne « camion roulant sans ligne » (§0quater R2) rouverte vers 08:49, encore en cours] + [ligne temps réel du matin 05:47→ marquée REJETÉE tôt (garde anti-géants sur une ligne refermée au moment d'un micro-arrêt) donc invisible]. Le portail MZoneX ne publiait, lui, que 2 trajets au 25/08 : 05:34:08→09:35:48 puis 09:36:28→10:04:27. Les cases « — » s'expliquent : la fusion d'affichage E1 (§0undecies) présente les deux lignes recouvrantes comme UNE ligne « en cours » sans fin, et aucune des deux ne porte de distance (MZoneX ne publie pas `distanceKilometers` ; la ligne rouverte n'en a pas). Le drapeau rouge ⚠ = franchissement du seuil légal TCC (4:30), conséquence du double comptage. **Règle de construction confirmée au passage et non modifiée** : tant que les lignes ne se recouvrent pas, TCJ ≤ TTJ par construction (TCJ = Σ durées des lignes ⊂ amplitude TTJ) — l'anomalie n'était possible QUE par un recouvrement. Arbitrations adoptées le 25/08/2026 (réponses : oui aux quatre) :

| # | Règle | Conséquence |
|---|---|---|
| F1 | **Garde absolue anti-double-comptage — amendement de E2 (§0undecies), adopté.** Les compteurs (TCJ, TCC et, par différence, le total des pauses) sont calculés sur l'**union des intervalles** des lignes (fusiondes recouvrements à la mesure, `construire_journee`) : **un même instant n'est JAMAIS compté deux fois → TCJ ≤ TTJ devient mathématiquement impossible**, quel que soit l'état des données en amont. Pour des journées saines (sans recouvrement), le résultat est **strictement identique** à E2 (Σ des durées) — la règle ne change rien d'observable, sauf à éliminer le double comptage ; les arrêts toute durée restent déduits (AM-1/T1/E2 intacts sur ce point) et le TCC garde sa coupure ≥ 30 min. `fin_ref` (borne d'amplitude TTJ) = fin la plus tardive des lignes (ou l'instant présent si une ligne est ouverte). Chaque détection de recouvrement au recalcul est tracée (audit `trajet.chevauchement_detecte`, une ligne par journée et par quantité — pas de spam) ; **les lignes elles-mêmes ne sont ni modifiées ni supprimées par F1** (garde de mesure pure). Écran = export = archive inchangé (même source unique `construire_journee`). | TCJ > TTJ : impossible à jamais ; journal de transparence. |
| F2 | **Rattrapage « camion roulant sans ligne » (§0quater R2) : réactivation AVANT création, adopté.** Avant de créer une ligne « en cours » rattrapée, le mécanisme réutilise toute ligne existante qui **couvre déjà l'instant** : (a) ligne REJETÉE ouverte → réactivée (début conservé — comportement R1 existant étendu avec audit `trajet.reactivation`) ; (b) ligne REJETÉE refermée depuis **moins de 20 min** (`DUREE_MIN_PAUSE_VALIDE`) → **réouverte** (fin effacée, début conservé, audit) au lieu d'en empiler une nouvelle qui doublerait le temps. La création d'une ligne nouvelle n'a plus lieu que si **aucune** ligne (quel que soit son statut de validation) ne couvre l'instant. | La cause du doublon du 25/08 est supprimée à la racine. |
| F3 | **Réparation unique de l'anomalie du 25/08/2026, adoptée.** Au premier démarrage v1.32, une seule fois (marqueur d'audit `duplication_v132.terminee`, idempotent, reprise au boot suivant en cas d'échec) : dans chaque journée du **25/08/2026** encore active (non archivée), les lignes NON rejetées qui se **recouvrent** sont départagées — **la ligne VALIDÉE (officielle portail) prime** ; à statut égal, celle au début le plus tôt ; la ligne perdante est marquée **REJETÉE** (jamais supprimée, §3.2/R2, audit `trajet.doublon_rejete` avec les deux horaires) et les compteurs sont recalculés aussitôt. Avec F1, la ligne de l'exploitant s'affiche aussitôt corrigée (DÉPART 05:34, TCC = TCJ = TTJ ≈ 4:26 à 10:02, cases trajets de nouveau remplies). | La journée abîmée redevient exacte sans aucune perte de données. |
| F4 | **Rapport de diagnostic exportable, adopté.** Le bouton « **Rapport de diagnostic** » (page Suivi) produit un fichier texte (jour par jour, véhicule par véhicule) : lignes de la journée (début/fin/statuts/distances, y compris les rejetées masquées), compteurs calculés, positions GPS de la journée (heure, vitesse, lieu), et les actions automatiques du journal d'audit de la journée pour ce véhicule (rejets, rattrapages, réconciliations, chevauchements détectés). Horodaté, lisible sans connaissance technique — à joindre à toute question d'exploitation. | Diagnostic en une pièce jointe. |

---

## 0tricies decies. Arbitrage LSS du 25/08/2026 (fin de journée) — abrogation d'E3 : la fusion d'affichage passe de 20 à 30 min (v1.33, mandant)

Origine : le 25/08/2026 (~14 h 10), l'exploitant demande pourquoi les cases **PAUSE sont vides** pour trois camions (captures à l'appui, portails vérifiés à la seconde) : **0916TBV** (arrêt 07:48:24 → 08:08:30 = **20 min 06 s**), **0576TCD** (11:13:15 → 11:36:14 = **22 min 59 s**), **8076TCB** (07:46:27 → 08:12:17 = **25 min 50 s**) — trois arrêts de la bande « 20-29 min » que la règle E3 (§0undecies, arbitrage du 24/08) affiche en deux lignes avec case pause masquée « — ». Après explication chiffrée, l'exploitant **décide lui-même et explicitement** (formulation exacte) : « Je veux supprimer la règle E3 […] 1. si la durée de l'arrêt est < 30 min, les deux lignes sont fusionnées en une seule — pas de case pause du tout ; 2. si la durée de l'arrêt est ≥ 30 min, pause affichée normalement (ex. 0:35, 1:43) ». Arbitrage **ADOPTÉ directement** le 25/08/2026 :

| # | Règle | Conséquence |
|---|---|---|
| G1 | **Un arrêt STRICTEMENT < 30 min → les deux lignes sont FUSIONNÉES en UNE seule** (début = début de la 1re composante ; fin = fin de la dernière, cellule vide si en cours ; statut/couleur = ceux de la dernière ; distances et segments sommés ; numérotation re-séquencée) — **aucune case pause**. Amendement d'E1 : le seuil de fusion d'**affichage** passe de 20 min à **30 min**, porté par un nouveau réglage dédié **`SEUIL_FUSION_AFFICHAGE_S = 1800`** inscrit dans les seuils (§12.1 : jamais modifié sans validation explicite ; inséré automatiquement dans Paramètres des bases existantes au démarrage, comme toute nouvelle clé de seuil). La fusion reste opérée par la sérialisation unique `s_suivi` et par la relecture des archives `fusionner_snapshot` : **écran = export = archive** (§A.2), **aucune archive n'est réécrite**, l'opération est **idempotente et transitive** (relire à 30 min une archive stockée fusionnée à 20 min ≡ fusionner à 30 min les lignes d'origine — démontré par test). | Les trois cas du 25/08 s'affichent en lignes fusionnées (ex. 8076TCB : une ligne 07:33 → 08:28 au lieu de deux lignes + case « — ») ; « Nb trajets » diminue en conséquence (affichage seul). |
| G2 | **Un arrêt ≥ 30 min → deux lignes, pause AFFICHÉE normalement** (ex. 0:35, 1:43). **E3 est ABROGÉE** : la bande « 20-29 min : deux lignes + case pause masquée » n'existe plus — par construction, deux lignes affichées séparées sont désormais **toujours distantes d'au moins 30 min**, leur pause est donc toujours affichée (le seuil d'affichage des pauses, 30 min via `SEUIL_PAUSE_COUPURE_TCC`, est inchangé et devient automatiquement cohérent avec G1). | La règle d'affichage tient en **DEUX cas exactement**, comme dicté par l'exploitant. |

**Périmètre strict — rien d'autre ne change.** Les compteurs sont INTACTS (E2/F1 : **tous** les arrêts, quelle que soit leur durée, restent déduits du TCJ ; coupure du TCC à 30 min — v1.9 §1.1/§2.2) et **tous les usages moteur/données du seuil 20 min** (`DUREE_MIN_PAUSE_VALIDE` : passage noir/orange AM-2 et fin PROVISOIRE, réouverture R1/F2 de `rattraper_ouvertures`, poursuite d'un trajet à l'ingestion §1.2, garde anti-géants, marquage « ouvert » PASSE 0 de la réconciliation) restent à **20 min, intacts**. Seul change le rendu : grille du suivi, historique, exports Excel/PDF, modale des trajets, archives relues.

---

## 0quaterdecies. Arbitrages LSS du 25/08/2026 (soir) — TCC = chrono de session : les arrêts courts sont INCLUS (v1.34, mandants)

Origine : le 25/08/2026 (~15 h), l'exploitant dicte les règles du moteur avec un exemple chiffré exact — **0576TCD** : départ 10:20, arrêt 11:13, mini-manœuvre rejetée 11:15:15→11:15:49 (0,004 km), reprise 11:36, jusqu'à 14:30 (après correction de sa première formule, la seconde fait foi). Compteurs attendus : **TCC = 14:30 − 10:20 = 4:10** (« pas encore de pause ≥ 30 min »), **TCJ = 14:30 − (11:36 − 11:13) − 10:20 = 3:47:00** (« le trajet-manœuvre est ignoré ainsi que sa durée »), **TTJ = 4:10**. Vérification instrumentée des fonctions de production sur ce scénario exact : **TCJ = 3:46:19 ✔ et TTJ = 4:09:18 ✔ DÉJÀ conformes** (écarts = secondes réelles du portail) ; **TCC = 3:46:19 ✗** (l'arrêt de 23 min ne coupait pas la session mais était *déduit*). Constat de droit : le texte §1.1 (14/08) dit depuis toujours *« Arrêt < 30 min → TCC continue (la pause courte est **incluse** dans le TCC) »* — c'est l'**implémentation** qui avait dérivé (E2/F1 mesuraient la session en conduite pure). Arbitrations adoptées le 25/08/2026 (réponses : oui aux deux, après information expresse que le drapeau rouge TCC 4:30 et la pré-alerte 4:00 pourront apparaître plus tôt) :

| # | Règle | Conséquence |
|---|---|---|
| H1 | **TCC = chrono de session : temps ÉCOULÉ depuis le début de la session courante, arrêts < 30 min INCLUS.** Début de session = premier départ valide du jour, ou la reprise après la dernière pause ≥ 30 min (`SEUIL_PAUSE_COUPURE_TCC`, coupure ≥ 30 min inchangée — « supérieure ou égale à 30 min »). Les arrêts < 30 min ne coupent PAS et ne se déduisent PLUS du TCC. Réalignement de l'implémentation sur le texte §1.1 existant (« pause courte incluse ») ; **amendement d'E2/F1 sur ce seul point** (Ils mesuraient la session en somme de conduite). Le chrono ne peut pas double-compter par construction (garde F1 inchangée pour TCJ/pauses ; l'audit `trajet.chevauchement_detecte` reste). **TCJ et TTJ : INTACTS** (AM-1/T1/F1 : TCJ = Σ conduite réelle, TOUS les arrêts toute durée déduits, manœuvre totalement effacée ni ligne ni durée ; TTJ = amplitude depuis le 1er départ valide — AM-6). R1 (traversée de minuit, amorce au TCC figé de la veille) inchangée ; camion en pause ≥ 30 min → TCC = 0 (inchangé). | Votre exemple : **TCC = 4:10**, comme demandé. Conséquence assumée : seuil 4:30 et pré-alerte 4:00 atteints jusqu'à ~29 min plus tôt les jours à arrêts 20-29 min (le chrono reflète la fatigue potentielle réelle). |
| H2 | **Arrêt court EN COURS : le chrono TCC continue de s'écouler.** Camion arrêté depuis < 30 min sans avoir repris → le TCC s'écoule (il ne se fige pas) ; dès que l'arrêt atteint 30 min → pause coupante, TCC = 0, nouvelle session au redémarrage. | Pas de « trou » de chrono à l'arrêt sandwich ; pas de TCC figé artificiellement. |

Périmètre : seule la **mesure** du TCC change (un seul site de calcul : `recalculer_temps`, compteur stocké `suivi.tcc_s` ; grille, exports, alertes et drapeau lisent cette source unique — §A.2 préservé ; les **archives des jours passés ne sont ni recalculées ni réécrites**). Aucun impact sur DÉPART/TCJ/TTJ, sur la fusion d'affichage 30 min (G1/G2), ni sur aucun autre seuil (§12.1).

---

## 0quinquies decies. Arbitrages LSS du 25/08/2026 (soir) — onglet Infractions alimenté par Ym@ne (v1.35, mandants)

Origine : consigne de l'exploitant le 25/08/2026 (soir) — « passer à la configuration de l'onglet Infraction » avec la plateforme **Ym@ne** (plateforme d'infractions de MZoneX, https://bi.camtrack.pro — appli identifiée par reconnaissance : AngularJS/Spring-Tomcat signée Pumex, `<meta description="Ym@ne">`), structure d'onglet dictée colonne par colonne. C3 (§0nonies) avait décidé que l'onglet resterait vide tant qu'aucune source externe pré-filtrée n'était branchée : **Ym@ne est cette source**. Arbitrages adoptés le 25/08/2026 :

| # | Règle | Conséquence |
|---|---|---|
| I1 | **Source unique = Ym@ne (MZoneX), conforme à C3.** L'onglet Infractions n'affiche QUE des infractions remontées de Ym@ne (`exterieure=True`, jamais d'écriture locale — §0nonies C3/AM-5 confirmés). Ym@ne ne couvre que MZoneX : les 13 camions **CamtrackPro n'y figureront jamais** (confirmé par l'exploitant) — leurs alertes restent à l'onglet Alertes, inchangé. | Zéro bricolage local dans Infractions ; portée connue (36 véhicules MZoneX). |
| I2 | **Filtrage des niveaux : seuls ALERTE et ALARME comptent.** Ym@ne publie trois niveaux (enregistrement / alerte / alarme) ; le niveau **« enregistrement » n'entre JAMAIS en base** (filtré à l'import — choix express de l'exploitant, variante « stocker masqué » écartée). | L'onglet ne compte que du significatif. |
| I3 | **Structure de l'onglet — les 10 colonnes dictées, à l'identique :** `Date · Heure · Immatriculation · Chauffeur (nom et prénom) · Nom de l'infraction · Niveau · Seuil · Coordonnées GPS · Validation · Observation`. « Seuil » porte le seuil de référence quelle que soit la famille (conduite → seuil horaire ; vitesse → limite) ; « Coordonnées GPS » renseignée pour les infractions de vitesse. Filtres d'écran : période, véhicule, chauffeur, niveau, état de validation. Exports Excel/PDF = mêmes colonnes (§A.2 écran = export), **les lignes invalidées en sont exclues**. Format propre à cet onglet (le verrou 25/51 colonnes reste réservé à la grille du Suivi). | Écran, export et audit disent la même chose. |
| I4 | **Workflow de validation (validé par l'exploitant) :** chaque infraction arrive **NON TRAITÉE** ; liste déroulante `Valide / Invalide` ; **Invalide exige une observation** (cause) ; Valide n'en exige pas. L'invalidée est **exclue des totaux et des exports, jamais supprimée, jamais masquée à l'écran** ; décision traçable (par qui, quand, quelle observation — audit `infraction.validation`, re-décision possible, chaque changement tracé). Droit de valider : **ADMIN + TRACKING** ; Consultation en lecture. | Débat clos sur chaque ligne, sans rien perdre. |
| I5 | **Branchement technique Ym@ne — état et garde-fou.** Constat de reconnaissance : le portail exige une **session applicative** (intercepteur Spring, cookie JSESSIONID) ; ni le jeton OAuth MZoneX ni les formulaires usuels ne passent sans session navigateur. Le collecteur `api_ymane.py` est livré **désactivé** (`YMANE_ACTIVE=0`) : dès que l'exploitant fournit **une copie de requête navigateur** (F12 → Réseau → « Copier comme cURL » sur la page « détail exception »), le mapping définitif de ses champs JSON + la méthode de session sont gravés et activés (ajustement mineur, sans changer I1–I4). Collecte idempotente (clé externe Ym@ne, sinon clé naturelle date+heure+plaque+nom — jamais de doublon), cadence adossée au cycle N2 (15 min) quand actif. | Tout est prêt sauf le tuyau ; le tuyau tient à une copie d'écran-requête. |

### Exécution de I5 — branchement gravé le 26/08/2026 (v1.36)

L'exploitant a fourni la requête navigateur le 26/08/2026 matin (`/detailedexceptionreport?clientid=1&affiliateid=1&transporterid=2004&vehicleid=0&startdate=…&enddate=…&exceptiontype=0&exceptionlevel=0` + `/isvalidaccess`). Lecture du code applicatif (`js/services/login-service.js`, `js/controllers/login.js`) puis **vérification EN DIRECT avec le compte LSS** (identifiants MZoneX, consigne exploitant) : tout fonctionne à l'identique. Mapping gravé — il ne changera que si Ym@ne change, jamais les règles I1→I4 :

| Élément | Valeur figée (vérifiée en direct le 26/08/2026) |
|---|---|
| Session | `GET /changelanguage/French` puis `POST /login/` (JSON `{"username","password","language":"French"}`) → `errorCode:200` + cookie `JSESSIONID` ; les identifiants sont ceux de MZoneX (I5). La réponse porte `transporterid=2004`, `customerid=1`, `affiliateid=1` — réutilisés dans la requête du rapport (pas de valeur recopiée à la main). |
| Garde de session | `GET /isvalidaccess` → `true` ; à toute réponse non-JSON / HTTP 401-403-500 / `false` sur la lecture, **une** nouvelle connexion puis une seule nouvelle tentative (session expirée). |
| Lecture | `GET /detailedexceptionreport?clientid={customerid}&affiliateid={affiliateid}&transporterid={transporterid}&vehicleid=0&startdate={J-1}&enddate={J}&exceptiontype=0&exceptionlevel=0` (fenêtre J-1→J à chaque cycle N2 : couvre les exceptions de veille finalisées de nuit ; l'upsert par `exceptionid` rend le recouvrement sans effet). Tous niveaux demandés (`exceptionlevel=0`) : le filtrage I2 reste souverain À L'IMPORT, jamais délégué au portail. |
| Identité d'une ligne | `exceptionid` → `ymane_id` (clé d'upsert) ; secours : clé naturelle (jour+heure+plaque+nom). |
| Niveaux réels | `Alarm` → **ALARME** · `Alert` → **ALERTE** · `Recording` → rejeté à la normalisation (**I2**, jamais en base — 6 lignes sur 55 les 24-25/08 écartées ainsi au test réel). |
| Familles réelles (`parameter` → libellé FR officiel Ym@ne) | `Speeding` → « Excès de Vitesse » · `Acceleration` → « Accélération Brusque » · `HarshBrake` → « Freinage Brusque » · `ContinuousDrive` → « Conduite Continue » · `DailyDrive` → « Conduite Journalière » · `NightDrive` → « Conduite de Nuit » · `DailyRest` → « Temps de Repos Journalier » · `WeeklyRest` → « Temps de Repos Hebdomadaire » (libellés prélevés sur `/getcurrentlanguage` en session française ; repli : clé brute). |
| Seuil (`threshold`) | Valeur **verbatim** de Ym@ne, sans unité inventée : vitesse → km/h (`25`, `45`…) ; conduite hebdo/jour/continue/repos → heures décimales (`4.50` = 4h30) ; conduite de nuit → plage horaire brute (`18:00:00 to 05:30:00`) ; accélération/freinage → indice brut (`6.00`, `10.00`). Stockage : `seuil_unite` `kmh`/`s`/`brut` + `seuil_texte` pour la forme affichée exacte. |
| Mesuré | `maxvalue` (pointe : vitesse 33 pour 25 ; accélération 15-16) ; pour les familles de temps : `totalduration` (heures décimales). Représentation interne des durées : secondes (la colonne I3 « Seuil » reste la seule affichée). |
| Date/heure | `startdatetime` au format `YYYY-MM-DD HH:MM:SS`, heure **locale** (Tana) — jamais convertie, jamais devinée. |
| Véhicule / chauffeur | `vehiclename` « 4526 TCC (LSS) » → plaque via `plaque_depuis_libelle_portail` (jamais de création de véhicule — D4 seul créateur ; inconnu = reporté au cycle suivant) ; `drivername` → rapprochement fiche insensible casse/accents, sinon `chauffeur_brut`. |
| Coordonnées GPS | `startgps`/`endgps` au format `[longitude,latitude]` — la colonne I3 « Coordonnées GPS » affiche latitude, longitude. |
| Activation | `YMANE_ACTIVE=1` dans `backend/.env` (I5) ; le cycle N2 (15 min, déjà câblé v1.35) collecte ; inactif = no-op total. |

### Exécution de I5 — AMENDÉE le 26/08/2026 (v1.37, arbitrage direct exploitant)

Origine : retour d'expérience de l'exploitant le 26/08/2026 (jour même de l'activation) — « le traitement d'une infraction peut prendre plus de 12 h dans Ym@ne : les infractions d'hier remontent aujourd'hui à partir de 15 h ». La fenêtre J-1→J gravée la veille pouvait donc manquer des lignes tardives. Arbitrages adoptés le 26/08/2026 (réponses de l'exploitant aux trois questions posées avant codage) :

| # | Amendement | Conséquence |
|---|---|---|
| **A-I5** | **Fenêtre de relecture : ABROGÉE J-1→J → devient J-8→J à CHAQUE cycle 15 min** (`startdate = J-8`, `enddate = J` — « retrouver les infractions 8 jours avant », option retenue par l'exploitant parmi J-8→J / J-7→J / J-8 1×heure). Motif : une infraction peut apparaître dans Ym@ne plusieurs jours après les faits. L'anti-doublon par `exceptionid` (I5) rend la relecture sans effet sur l'existant ; le workflow I4 reste souverain (une ligne déjà validée/invalidée n'est jamais défaite). Coût mesuré EN DIRECT le 26/08 sur la fenêtre réelle 18→26/08 : **215 lignes brutes** (106 alarmes, 56 alertes, 53 enregistrements écartés par I2) — relecture intégralement absorbée par le cycle. Toute la ligne « Lecture » du tableau v1.36 est amendée **sur ce seul point** ; session, garde, identité, niveaux, familles, seuils, mesuré, date locale, GPS, activation : **INTACTS**. | Aucune infraction tardive jamais perdue ; aucun doublon. |
| **A-I6** | **Familles de repos/nuit — vocabulaire métier dicté et documenté :** conduite de nuit = plage **18:00 → 05:30** ; temps de repos journalier minimum **09:00:00** ; temps de repos hebdomadaire minimum **24:00:00**, exigé après un temps de conduite hebdomadaire (**TCH = 56:00**) ; le TCH **repart à zéro** dès 24:00:00 sans trajet ≥ 0,3 km **même s'il est < 56:00**. **Arbitrage express : AUCUN calcul interne de TCH dans la plateforme** (option « aucun calcul » retenue, contre « compteur affiché » et « compteur + alerte ») — Ym@ne reste souverain de la règle et remonte les infractions correspondantes, importées telles quelles (I1→I4 intacts, seuils verbatim I3). Constat terrain 26/08 : aucune occurrence `WeeklyDrive`/`WeeklyRest` sur la fenêtre 18→26/08 ; si elles apparaissent, les mappings prévus dans `api_ymane.py` les prennent telles quelles (familles horaires → seuil « 56:00 » / « 24:00 »). **Grain futur noté : si un compteur TCH interne est un jour demandé, il suivra le chauffeur, tous véhicules confondus** (réponse de l'exploitant le 26/08) — arbitrage à graver à ce moment-là. | La plateforme ne réinvente pas la règle Ym@ne ; elle la montre, fidèlement. |

---

## 0sexies decies. Arbitrages LSS du 27/08/2026 (matin) — déduplication chauffeurs + réparation unique (v1.38, mandants)

Origine : constat de l'exploitant le 27/08/2026 (captures à l'appui) — le chauffeur **MICHAËL Justin** est dupliqué des centaines de fois dans Conducteurs (référentiel gonflé à 2 118 fiches) et l'alerte « ⓘ Nouveau chauffeur détecté » part en rafale. Diagnostic prouvé en test unitaire le 27/08 : l'anti-doublon de `creer_conducteur_auto` comparait via `lower()` **SQLite, qui ne met en minuscules que A–Z** — tout nom accentué (É, È, Ë, À, Ç…) n'était JAMAIS reconnu comme existant → une fiche créée à CHAQUE relevé. Contagion : tout chauffeur au nom accentué. Les noms sans accent n'étaient pas touchés. Arbitrages adoptés le 27/08/2026 (réponses aux questions posées avant codage) :

| # | Règle | Conséquence |
|---|---|---|
| **J1** | **Anti-doublon chauffeur = nom NORMALISÉ, partout.** Forme canonique `nom_normalise` = NFKD + accents écartés + minuscules + espaces resserrés (« MICHAËL Justin » = « MICHAEL JUSTIN » = « Michaël   Justin » ; formes Unicode NFC/NFD confondues). La comparaison se fait sur cette forme — JAMAIS plus via `lower()` SQL. Défense en profondeur : colonne `conducteurs.nom_normalise` + **index UNIQUE** en base (posé après réparation si plus aucun doublon ; sinon reporté au prochain démarrage, sans jamais bloquer le boot). La création/édition MANUELLE (écran Conducteurs) est bornée de la même manière : nom normalisé déjà pris → **409 « Un chauffeur au nom équivalent existe déjà »**. | Un chauffeur = une fiche, quelle que soit la forme du nom publié. |
| **J2** | **Réparation unique au démarrage (marqueur idempotent).** Regroupement par `nom_normalise`. **Gardien = fiche la plus complète** (dans l'ordre : véhicule affecté actuellement > téléphone renseigné > matricule non-« AUTO- » > plus grand nombre de références liées) et, à égalité, **la plus ancienne** (choix de l'exploitant). Rebranchement vers le gardien : `vehicules.conducteur_actuel_id`, `suivi_journalier.conducteur_id`, `trajets.conducteur_badge_id`, `missions.conducteur_id`, `alertes.conducteur_id`, `infractions.conducteur_id`. **Archives (`historique_journalier`) NON retouchées — §A.2 intact** : le texte du snapshot d'archive fait déjà foi à l'affichage si la fiche jointe a disparu. | Plus jamais de fiche perdue ni de référence orpheline. |
| **J3** | **♦ AMENDEMENT À « JAMAIS DE SUPPRESSION » — exception étroite, choix express de l'exploitant (27/08/2026).** Les fiches doublons sont **SUPPRIMÉES DÉFINITIVEMENT**, mais uniquement : (a) si elles appartiennent à un groupe homonyme normalisé de taille > 1 ; (b) après rebranchement complet (J2) ; (c) après **consignation intégrale** de chaque fiche supprimée dans `AuditLog` (action `conducteur.doublon_supprime` — snapshot JSON : id, matricule, nom, téléphone, date de création, nombre de références rebranchées par table). La réparation est **à passage unique** (marqueur) ; la règle générale « jamais de suppression automatique » reste en vigueur PARTOUT ailleurs (trajets, suivi, infractions, archives, alertes…). | Dégâts purgés une fois, traçabilité totale, règle-mère préservée. |
| **J4** | **Alertes « Nouveau chauffeur détecté » en rafale = fantômes du bug clôturées en masse, une fois** (statut TRAITÉE, motif audité « rafale du bug corrigé v1.38 » — jamais effacées). La cause étant corrigée (J1), elles ne peuvent plus se re-déclencher : une alerte ne part désormais que pour un chauffeur réellement nouveau. | Écran d'alertes lisible à partir du premier lancement v1.38. |

**Mise au point technique du même jour (gravée) :** la forme canonique resserre aussi les espaces INTERNES multiples (« michael␣␣justin » = « michael justin ») — dérive constatée sur données réelles au fumier ; la passe de fusion est rejouée à chaque démarrage tant qu'un groupe homonyme canonique survit (idempotente : aucun groupe → aucun effet), le marqueur ne court-circuite que J4 et l'alerte récapitulative ; le regroupement se fait toujours sur la forme RECALCULÉE, jamais sur la colonne stockée.

---

(lu et appliqué par l'agent avant chaque tâche)

Avant de produire du code, une configuration ou une maquette pour LSS Tracking, l'agent DOIT :

1. Lire ce document en entier — il est la source de vérité.
2. Citer les règles applicables par numéro de section (ex. : « s'applique : §1.1, §5.1, §8 »).
3. Identifier la source de données :
   - MZoneX — Onglet « Trajets » = données OFFICIELLES (trajets terminés uniquement).
   - MZoneX — Onglet « Événements » = données TEMPS RÉEL (trajet en cours).
   - CamtrackPro — rapport « Détail Trajet groupe de véhicules ».
4. Appliquer l'attribution des jours (§8) : `jour = DATE(heure_debut)`, sauf `heure_debut < 01:00 → HIER`.
5. Ignorer les manœuvres : tout mouvement < 0,3 km n'ouvre pas de trajet.
6. Respecter les seuils constants (§12.1). Ne jamais les durcir/assouplir sans validation explicite de l'administrateur.
7. Remplacer, jamais dupliquer : un trajet officiel remplace les données provisoires dans le MÊME emplacement `Tn` (§3.2).
8. Produire en fin de réponse une SECTION « CONFORMITÉ » (modèle §A.9) qui mappe chaque règle applicable à l'implémentation et valide les checklists.
9. Signaler toute ambiguïté / tout conflit AVANT de coder (ne pas deviner silencieusement).

### Gravité des non-conformités

- 🔴 Bloquant : dépassement des seuils (TCC/TCJ/TTJ), mauvaise attribution de jour, faux positif/négatif sur manœuvre ou fin de trajet, duplication d'emplacement `Tn`.
- 🟡 Avertissement : format d'heure/durée, couleur/style d'affichage, libellés.

### A.9 — Modèle de section CONFORMITÉ (à recopier en fin de réponse)

```markdown
## 0septies decies. Arbitrages LSS du 27/08/2026 (fin de matinée) — observabilité de la collecte Ym@ne (v1.39, mandants)

Origine : constat de l'exploitant le 27/08/2026 (capture à l'appui) — l'onglet Infractions affiche « 0 » partout alors que le portail Ym@ne est alimenté. Diagnostic du même jour (10:17) : connexion Ym@ne saine, **201 lignes brutes** sur la fenêtre J-8→J dont **155 importables** (103 ALARMES + 52 ALERTES ; les « enregistrements » restent écartés, §0quinquies decies I2) ; la base exploitant, elle, est **totalement vide** — l'échec ou la désactivation de la collecte Ym@ne ne laissait AUCUNE trace visible dans l'application (journal console seul). Arbitrages adoptés le 27/08/2026 (réponses aux questions posées avant codage) :

| # | Règle | Conséquence |
|---|---|---|
| **K1** | **Échec de collecte Ym@ne = alerte visible, anti-spam, auto-refermée à la guérison** (choix de l'exploitant). Tout cycle Ym@ne en échec (site injoignable, identifiants refusés après la reconnexion réglementaire, réponse illisible, délai dépassé) crée UNE alerte de type `COLLECTE_YMANE` (gravité MOYENNE, lien « /infractions ») dont le message dit la raison **en français**. Si une alerte de ce type est déjà ouverte (NOUVELLE ou VUE), **jamais de doublon** : son message est mis à jour (nombre d'échecs, dernier essai) sans changer son statut. Dès qu'un cycle redevient sain (session ouverte ET lecture aboutie, même à 0 ligne importée), l'alerte ouverte est **refermée automatiquement** (statut TRAITEE, motif consigné en audit — « guérison automatique ») — **jamais effacée**. Collecte désactivée (`YMANE_ACTIVE≠1`) = silence souverain : ni alerte ni écriture — la configuration de l'exploitant fait loi. | Plus jamais d'échec invisible ; guérison sans geste. |
| **K2** | **Bouton « Relancer la collecte maintenant » sur l'onglet Infractions** — réservé aux rôles d'écriture (ADMIN / TRACKING), masqué en consultation. Relance synchrone du cycle Ym@ne (fenêtre J-8→J inchangée, §0quinquies decies A-I5 ; anti-doublon I5 : rejouer n'abîme rien) ; message de résultat affiché à l'écran (« N lignes lues — X nouvelles, Y déjà présentes » ou la raison de l'échec en Français, ou « collecte désactivée (YMANE_ACTIVE) »). Verrou de passe : deux cycles ne tournent jamais concurremment (planifié vs manuel) — conflit → **409 « une collecte est déjà en cours »**. Chaque relance manuelle est consignée en audit (`infraction.relance_ymane`, auteur = opérateur). | L'exploitant peut vérifier/forcer la collecte sans redémarrer ni attendre 15 min. |
| **K3** | **Refusés / inchangés (gravés pour ne pas reposer la question) :** (a) ligne de statut permanente sur l'écran Infractions : **REFUSÉE** — l'écran ne change qu'avec le bouton K2 ; (b) interrupteur « collecte Ym@ne » dans Paramètres : **REFUSÉ** — l'activation reste souveraine dans `backend\.env` (`YMANE_ACTIVE=`) ; (c) cadence inchangée : cycle adossé à la synchronisation Niveau 2 (~15 min, §0quinquies decies I5). | Périmètres figés par l'exploitant. |
**Mise au point technique du même jour (après-midi), gravée (v1.40) :** constat d'exécution chez l'exploitant (message fourni) — le collecteur Ym@ne s'appuyait sur la bibliothèque externe `requests`, **absente du poste malgré le `pip install` du lanceur** : le cycle échouait en silence AVANT même de pouvoir lever l'alerte K1, et le bouton renvoyait une erreur 500. Gravé : (a) le transport HTTP de **TOUS** les collecteurs (MZoneX, CamtrackPro, Ym@ne) est **`urllib` de la bibliothèque standard** — aucun paquet réseau externe n'est admis dans un collecteur ; `requests` est retiré de `requirements.txt` à jamais ; (b) le bouton K2 ne renvoie **JAMAIS** de code 500 : toute erreur, même inattendue, devient un message français affiché à l'écran et une ligne d'audit ; (c) les règles K1/K2/K3 elles-mêmes sont inchangées — seul le moteur du transport change ; les suites d'essais v1.36/v1.39 ont été réalignées sur `urllib` (réalignement déclaré, périmètre vérifié identique).


---

## 0octies decies. Arbitrages LSS du 27/08/2026 (midi) — barre de défilement unifiée + période des infractions (v1.41, mandants)

Origine : demandes de l'exploitant le 27/08/2026 — (1) sur les onglets Infractions et Véhicules, il faut descendre tout en bas du tableau pour faire glisser la barre horizontale ; (2) sur l'onglet Infractions, ajouter un filtre par type d'infraction et afficher « à quelle heure l'infraction a commencé puis terminé » pour les familles de temps (repos hebdomadaire, repos journalier, conduite de nuit, conduite journalière — ex. dicté : arrêt 23:00, reprise 07:30, seuil 09:00 → seulement 8:30 de repos) et pareil pour les autres familles, **toujours en collectant les données Ym@ne**. Arbitrages adoptés le 27/08/2026 (réponses aux questions posées avant codage) :

| # | Règle | Conséquence |
|---|---|---|
| **L1** | **Barre de défilement : comportement de l'onglet Suivi Journalier généralisé à TOUS les onglets à grille** (Infractions, Véhicules, Conducteurs, Alertes, Historique, Missions, Conduite, Dashboard, Paramètres) : la zone de grille défile EN INTERNE (hauteur d'écran), la barre horizontale est donc TOUJOURS visible sans descendre en bas de page. Règle d'ergonomie PUREMENT VISUELLE : aucune colonne, aucun format, aucune donnée ne change (verrou 25/51 du Suivi intact). | Plus de « descente en bas » pour glisser à droite. |
| **L2** | **♦ AMENDEMENT I3 (choix express 27/08) — période des infractions : DEUX colonnes s'ajoutent** à la grille « Début de l'infraction » et « Fin de l'infraction », placées entre « Coordonnées GPS » et « Validation ». Valeurs = `startdatetime` / `enddatetime` **verbatim Ym@ne** (date et heure, JJ/MM/AAAA HH:MM:SS ; une fin peut être au J+1) ; « — » si le portail ne publie rien — JAMAIS d'invention (enddatetime vérifié présent pour toutes les familles sur données réelles, 27/08). Stockage : `infractions.date_fin` (Date) + `infractions.heure_fin` (Time) ; l'upsert I5 complète les lignes déjà en base à la prochaine relecture J-8→J (rattrapage automatique, idempotent). Workflow I4 intact ; archives §A.2 non concernées. | La période complète de l'infraction est lisible pour TOUTES les familles. |
| **L3** | **Filtre « type d'infraction »** : liste déroulante des familles Ym@ne réellement présentes en base (libellés verbatim `nom_ymane`), filtrage par nom exact, combiné aux filtres existants (période, véhicule, chauffeur, niveau, validation). **Écran = export (§A.2, choix confirmé)** : exports Excel/PDF — colonnes alignées sur la grille amendée, dans l'ordre dicté : `Date · Heure · Immatriculation · Chauffeur · Infraction · Niveau · Seuil · Coordonnées GPS · Début de l'infraction · Fin de l'infraction · Validation · Observation` (12 colonnes), mêmes lignes filtrées que l'écran, lignes invalidées toujours exclues. | Recherche par famille ; écran, export et audit disent la même chose. |

### Mise au point du 29/08/2026 (correctif v1.42, portant sur L1) — barres de défilement : pages multi-sections

**Origine** : captures exploitant du 29/08/2026 — onglet **Historique** : la grille du jour (27/51 colonnes) est **coupée à droite SANS barre horizontale**, et le bas de page est **inaccessible SANS barre latérale** (régression v1.41). Diagnostic technique : dans un conteneur racine à hauteur fixe (`h-full`, patron Suivi de L1), la mise en page flexible écrase les sections empilées entre elles — une carte à débordement masqué (`overflow-hidden`) SANS contrainte de taille propre est compressée jusqu'à rogner son contenu interne, barres de défilement comprises. **Aucune donnée, aucune colonne, aucun format n'est touché** (règle d'ergonomie purement visuelle ; verrous §A.2 et 25/51 intacts).

**Règle correctrice** — adoptée sans nouvel arbitrage (§A.9.9 : aucune ambiguïté — c'est la restauration pure du comportement arbitré en L1, l'attendu étant dicté par capture) :

1. **Page à UNE SEULE zone de données** (Suivi, Infractions, Véhicules, Conducteurs, Conduite) : patron L1 **INCHANGÉ** — racine pleine hauteur, la zone de grille défile en interne sur les deux axes, barre horizontale toujours visible. (Conduite : deux vues à 50/50, chacune avec ses deux barres internes — contenu toujours accessible.)
2. **Page MULTI-SECTIONS empilées** (Historique : sélecteur de plage + choix de journée + grille du jour + indicateurs + tableau de synthèse ; Paramètres) : retour au **défilement vertical DE PAGE** (racine sans hauteur fixe — la barre latérale réapparaît) ; chaque tableau large/long garde sa **zone de défilement interne bornée** (grille du jour Historique ≤ 72 % d'écran ; synthèse Historique, comptes Utilisateurs et journal d'Audit ≤ 60 % d'écran, défilement dans les DEUX sens) → la barre horizontale reste TOUJOURS visible sans descendre en bas (esprit L1 préservé).
3. Le correctif Paramètres est pris **par anticipation** (même défaut structurel que Historique, non encore signalé) — déclaré ici-même conformément au workflow.

**Conséquence** : version **1.42**.

---

## 0nonies decies. Arbitrages LSS du 29/08/2026 (matin) — rattrapage des boîtiers muets (v1.43, mandants)

Origine : captures exploitant du 29/08/2026 — (1) en journée, certains camions semblent « arrêter » leurs données en cours de matinée (2746TCC figé à 10:20 le 28/08) ; (2) la veille au soir des lignes incomplètes, complètes le lendemain ; (3) des lignes DÉFINITIVEMENT vides alors que le portail a les trajets (3056TBS : 9 trajets, 0936TBV : 11 trajets le 28/08, constatés en direct). **Diagnostic mesuré en direct sur les portails le 29/08 (preuves à l'appui)** : la cause mère est le **boîtier GPS en zone sans couverture GSM** (route Brickaville) qui garde ses positions en tampon et les renvoie en différé ; la plateforme n'a jamais perdu de données reçues. Cycle Niveau 2 mesuré : ~30 s au total (CamtrackPro 29 s / MZoneX 4 s) — la cadence 15 min n'est PAS en cause ; volume Événements ~600/15 min, très loin de la limite technique (9 000/appel). 2736TCC et 3046TBS n'avaient réellement AUCUN trajet le 28/08 (camions au repos — lignes vides NORMALES). Le vrai trou de conception : **la plateforme ne relisait jamais un jour passé une fois archivé** — un trajet publié au portail APRÈS la consolidation de minuit était perdu pour la plateforme. Arbitrages adoptés le 29/08/2026 (réponses aux questions posées avant codage) :

| # | Règle | Conséquence |
|---|---|---|
| **M1** | **Relecture des jours passés (choix adopté)** : à chaque cycle Niveau 2 (15 min), relire **J ET J-1** ; toutes les heures, relire **J-2 → J-7** (réflexe identique à Ym@ne J-8→J, déjà gravé). Upsert idempotent, jamais de suppression. ♦ **AMENDEMENT §A.2 (extension#2, déclarée)** : si la relecture apporte des trajets NOUVEAUX pour un jour déjà archivé, la grille archivée du jour est **régénérée en place** — UNIQUEMENT en cas de changement de contenu, avec **audit avant → après** (`archive.raffraichie`) — jamais de ligne supprimée hors remplacement provisoire → validé (règle déjà adoptée §2.4). | Tout retard portail ≤ 7 jours est rattrapé TOUT SEUL ; les lignes vides de 3056TBS/0936TBV du 28/08 se complètent au premier cycle après installation. |
| **M2** | **Découpage souverain des « géants » provisoires (choix adopté)** : quand une ligne provisoire (fabriquée en direct, ex. 10:20→18:11) recouvre une série de trajets OFFICIELS publiés plus tard (10:20→13:06, pause 1:11, 14:17→18:11), la série officielle **remplace et découpe toujours** le provisoire — remplacement en place pour la 1ʳᵉ ligne, insertion des suivantes, éventuels restes provisoires couverts **marqués REJETÉ** (flag, jamais physiquement supprimés), comparaison tracée en audit (`trajet.geant_rejete`). | La grille épouse TOUJOURS la vérité du portail, pauses réelles comprises ; TCJ/TTJ refidélisés. |
| **M3** | **Fenêtre temps réel élargie à 3 heures (choix adopté)** : la fenêtre incrémentale Niveau 1 plafonne à 3 h (au lieu de 30 min) — volumétrie vérifiée sans risque. | Un tampon remonté avec < 3 h de retard enrichit la journée EN COURS quasi immédiatement ; au-delà, M1 prend le relais. |
| **M4** | **Repère « boîtier muet — données en transit » (choix adopté)** : dans le Suivi Journalier, badge orange dès qu'un camion n'a rien émis depuis 30 min ; audit `vehicule.muet_jour` UNE fois par camion et par jour (jamais de spam) ; audits de complétion M1/M2 conservés. | L'exploitant distingue d'un coup d'œil la zone blanche (normal) de la vraie panne — fini l'incertitude de l'avant-midi. |

**Conséquence** : version **1.43**. Suite de non-régression dédiée (`test_boitiers_muets_v143.py`) **rejouant mécaniquement le scénario du 2746TCC** (géant redécoupé), la complétion des lignes vides (3056TBS) et la relecture J-1→J-7 : la régression est désormais impossible sans casser la batterie.

---

## 0vicies decies. Arbitrages LSS du 31/08/2026 — TCC masqué hors temps réel & positions automatiques 18h/20h/22h (v1.44, mandants)

Origine : retour de l'exploitant après installation de la v1.43 (« elle fonctionne bien ») — deux ajustements mandatés. (1) **TCC dans l'Historique** : « il ne faut pas afficher le TCC dans l'onglet historique, cela complique l'explication durant l'audit ; en réalité le TCC se calcule en temps réel et se remet à zéro après un arrêt ou une pause ≥ 30 min — il ne devrait pas s'afficher pour les trajets déjà terminés ». (2) **Positions** : ajouter 2 colonnes (20h, 22h) après 18h, remplir automatiquement 18h/20h/22h avec les positions réelles nommées selon §0undecies E4 (« le nom de lieu affiché sur carte à proximité », formulation exacte), et laisser 08h/10h/12h/14h/16h manuelles. Réponses aux 4 questions d'arbitrage posées avant codage — dont une précision souveraine : « dans MZoneX ou CamtrackPro, le camion est toujours affiché à la position où il est à ce moment, même si le boîtier n'émet pas ; **le système doit prendre ces positions-là** ».

| # | Règle | Conséquence |
|---|---|---|
| **N1** | **TCC masqué hors temps réel (choix adopté).** La colonne TCC reste présente partout (structure identique Suivi/Historique — exigence d'audit des formats intacte, §A.2) mais pour toute **journée terminée** ses cellules affichent **« 0:00 »** (réponse textuelle de l'exploitant). **Portée : partout sauf le jour en cours** — Historique (liste mensuelle, grille d'une journée archivée, exports jour/période Excel/PDF, synthèse mensuelle écran + exports) ET Suivi Journalier d'un jour passé (écran + exports). Le jour en cours garde le chrono vivant (H1/R3 inchangés : session depuis la dernière pause ≥ 30 min). **Implémentation** : masquage à la **lecture** (copie du snapshot, jamais de réécriture d'archive — précédent E1) ; la valeur interne `tcc_s` est conservée en base pour la contre-vérification ; le « 0:00 » sort du point unique de sérialisation/lecture (écran = export = archive maintenu). | Fin des justifications en audit ; le TCC redevient ce qu'il est métier : un chrono du moment. |
| **N2** | **Colonnes « Pos. 20h » et « Pos. 22h » + positions automatiques 18h/20h/22h (choix adopté).** La grille gagne 2 colonnes après « Pos. 18h » — **formats 25→27 / 51→53 colonnes** (amendement déclaré au verrou des formats, MANDAT exploitant comme E1) ; exports Excel (« Pos. 20h », « Pos. 22h »), PDF (« 20h », « 22h ») et archives futures au même format (écran = export = archive). Colonnes **08h→16h : 100 % manuelles** (bouton « Pré-remplir (GPS) » inchangé, simple aide à la saisie). Colonnes **18h/20h/22h : automatiques** — remplies dès que l'heure est passée avec la **dernière position connue à l'heure dite** (dernier point GPS ≤ l'heure, **sans limite d'âge** — instruction exacte : le portail affiche toujours le camion là où il est, même boîtier muet), libellée **E4** (zone portail contenant le point ; sinon « proche ‹nom› » à moins de 5 km ; sinon coordonnées 4 décimales). **Réécriture autorisée jusqu'au verrouillage de la journée à minuit** quand de meilleures données arrivent (boîtier muet qui remonte son tampon) ; les 5 colonnes manuelles ne sont jamais touchées automatiquement. Champs `position_20h`/`position_22h` ajoutés (VARCHAR 200, migration additive idempotente). Les archives antérieures (sans ces clés) affichent « — » dans les nouvelles colonnes : aucune réécriture. | L'exploitant ne saisit plus les positions du soir ; la valeur affichée = celle que montre le portail. |
| **N3** | **Rattrapage des positions des jours passés (choix adopté).** Adossé au cycle Niveau 2 : les cellules **18h/20h/22h VIDES** des jours J-1 → J-7 se complètent seules quand les données arrivent en retard (même source N2) — **jamais de modification d'une cellule déjà remplie** ; audit `suivi.position_rattrapee` par ligne complétée (avant → après par cellule) ; si la journée est déjà archivée, régénération du snapshot via le pipeline M1 (§A.2 extension #2, audit `archive.raffraichie`). | Cohérent M1 : un boîtier muet ne laisse plus de cellule vide, même à J-7. |

**Conséquence** : version **1.44**. Suite de non-régression dédiée (`test_positions_tcc_v144.py`) : masquage TCC (0:00 partout sauf jour courant, jamais réécrit), colonnes 20h/22h (modèle, migration sur base existante, grille, exports 27/53), auto-remplissage 18h/20h/22h (sans limite d'âge, réécriture jusqu'à minuit, colonnes manuelles intactes) et rattrapage N3 (cellules vides seulement + audits).

---

## 0unvicies decies. Arbitrages LSS du 01/09/2026 — Positions 18h/20h/22h EXACTES (historique portails) & nomenclature des lieux resserrée (v1.45, mandants)

Origine : retour de l'exploitant après installation de la v1.44 (capture d'écran du 01/09 à l'appui). (1) **Positions du soir identiques** : « la configuration actuelle est fausse car le système collecte les mêmes positions pour les 3 heures différentes (18, 20 et 22), il prend la position de 18 h pour les 3 […] pour les camions qui roulent après 18 h, c'est faux — il faut que le système distingue exactement les positions pour les 3 ». Cause racine constatée en direct : le serveur étant éteint le soir, le rattrapage matinal (v1.44 N3) ne trouvait en base locale que le dernier point d'avant l'arrêt (~17 h) et le répétait dans les 3 cases. (2) **Noms de lieux trop généralisés** : « il faut que tu utilises l'onglet "lieu" dans MZoneX et l'onglet "zone" dans CamtrackPro […] éviter "proche COLAS PK13" qui est généralisé ; il faut un nom comme "BASE TAMATAVE", "Ambatosonegaly", "Ampasimadinika" […] si la position exacte ne correspond pas au lieu enregistré, tu dois utiliser le nom du lieu le plus proche ». (3) Question support : le portail web CamtrackPro dysfonctionne **uniquement sur la machine serveur** — liaison prouvée par sonde (toute nouvelle session API invalide la session précédente côté portail). Quatre questions d'arbitrage posées avant codage ; réponses reçues le 01/09/2026 (O1 « avant », O2 « corriger », O3 « proche », O4 « inchangé »).

| # | Règle | Conséquence |
|---|---|---|
| **O1** | **Positions 18h/20h/22h lues dans l'historique des portails, point retenu = dernier point AVANT l'heure (choix adopté).** Les portails conservent l'historique complet même quand la plateforme est éteinte : **MZoneX** → ensemble `Events` (fil horodaté à la seconde, fenêtre [04h00 → 22h30 locales]) ; **CamtrackPro** → messages Wialon (`messages/load_interval` + `messages/get_messages`, fenêtre journée locale complète). Pour chaque heure H ∈ {18, 20, 22} : retenir le **dernier point GPS horodaté ≤ H pile** (sémantique exacte du portail : « le camion est affiché là où il est à ce moment — même boîtier muet », instruction déjà inscrite §0vicies decies N2 — un camion arrêté à 19h30 affiche donc à 20h et 22h le même point de 19h30, ce qui EST la vérité terrain). Chaîne de repli si le portail est muet pour ce véhicule ce soir-là : dernier point connu en base locale **sans limite d'âge** (comportement v1.44 N2 conservé) ; si rien nulle part, la cellule reste telle quelle (**jamais d'effacement**). Passe d'intégration : (a) le soir même à partir de 22h05 si le serveur tourne (réécriture libre jusqu'au verrouillage de minuit — N2 inchangé), (b) au démarrage du lendemain pour J-1 → J-7, (c) relance J-1 à chaque cycle Niveau 2 (au plus 1×/30 min). Libellé selon **O3**. | Un camion roulant après 18h affiche 3 positions DISTINCTES et vraies ; un camion garé garde le même point — exactement ce qu'exige l'exploitant. |
| **O2** | **Réparation ciblée des journées déjà fausses (choix adopté).** Pour J-1 → J-7 : toute ligne dont les **3 cellules 18h/20h/22h sont identiques** (signature exacte du défaut) est recalculée via O1 (source portail + nomenclature O3) ; si le résultat diffère, la ligne est **corrigée en place** avec audit `suivi.position_reparee` (avant → après par cellule, motif « signature 18h=20h=22h »). Camion légitimement immobile toute la soirée : le portail rend la même valeur → **aucune écriture, aucun audit** (idempotent). Toute ligne sans cette signature est **intouchée** ; cellule vide : complétée (N3 v1.44 étendu à la source portail) ; journée déjà archivée : régénération via le pipeline M1 (§A.2 extension #2, audit `archive.raffraichie`, mention « positions ») — **aucune donnée supprimée, aucun autre champ modifié**. Si les deux portails sont injoignables : rien n'est écrit, journal WARN, nouvelle tentative au cycle suivant. ✦ **Addendum d'implémentation (déclaré 01/09, preuve smoke)** : la régénération d'archive est commise **par journée** (rappel `apres_jour`), plus une **auto-guérison purement locale** (`aligner_archives_positions`) qui, à chaque passage O2 et au cycle J-1, resynchronise toute archive dont les relevés 08h→22h diffèrent du suivi — source de vérité — SANS appel portail : un arrêt en plein balayage ne laisse jamais une archive en retard. | Les lignes « Debut zone 3/Debut zone 3/Debut zone 3 » d'hier se corrigent seules au premier démarrage v1.45 — traçables en audit. |
| **O3** | **Nomenclature resserrée (choix adopté) — AMENDE §0undecies E4 (extension #1, déclarée).** Référentiel INCHANGÉ : les noms viennent exclusivement de l'onglet **« Lieux » de MZoneX** et de l'onglet **« Zones » de CamtrackPro** (union des deux portails, ~1 200 lieux). Nouvel ordre de choix du nom pour un point GPS : **1.** point DANS un lieu précis → son nom exact (le plus spécifique = plus petit rayon) ; **2.** sinon lieu précis le plus proche mesuré **à son centre** à moins de **3 km** → « proche ‹nom› » (un dépôt précis à 1 km gagne face à une grande zone de ville — fini le bord-à-bord qui favorisait les grosses zones) ; **3.** sinon grande zone générique (référentiel automatique « …-CC », « …-TOWN » — ex. MDG-Antananarivo-CC-TOWN) contenant le point → son nom ; **4.** sinon zone générique dont le centre est à moins de 5 km → « proche ‹nom› » ; **5.** sinon coordonnées 4 décimales (jamais de cellule vide). « Précis » = tout lieu/zone du portail qui n'est PAS du référentiel automatique -CC/-TOWN (mesure du 01/09 : seules 24 zones génériques sur 1 199). Portée : tous les libellés de position écrits après installation (temps réel, O1, pré-remplissage) ; les données déjà écrites ne sont jamais réécrites en masse (archives §A.2) — seules passent par O3 les cellules touchées par O2/N3. | « proche COLAS PK13 »/« Ambohimangakely-…-CC » généralisés → noms précis des deux portails ; ex. vérifié en direct : le point PK13 de la plainte devient « proche Jovenna By Pass (ENC) » (1,1 km) au lieu du nom de commune générique. NB : « Ampasimadinika » n'existe dans aucun portail (mesuré) — un lieu manquant s'ajoute dans l'onglet Lieux/Zones d'un portail, il sera utilisé dès le cycle de cache suivant (6 h). |
| **O4** | **Session CamtrackPro INCHANGÉE (choix adopté souverainement, malgré la liaison prouvée).** La sonde du 01/09 a démontré que chaque nouvelle session API Wialon invalide la session précédente (la plateforme en ouvre une par cycle ~60 s), ce qui déconnecte le portail web CamtrackPro sur la machine serveur. La correction « session unique durable partagée » a été **proposée et refusée** par l'exploitant (réponse « laisser tel quel ») : **aucune modification de la gestion de session n'est apportée** ; la collecte reste exactement comme en v1.44. Contournement support documenté au livrable : consulter le portail web depuis un autre poste/téléphone, ou sur la machine serveur APRÈS arrêt temporaire de la plateforme (Ctrl+C dans la fenêtre noire, relance ensuite par `demarrer.bat`). Conséquence assumée par l'exploitant. | Aucun risque de régression sur la collecte ; la gêne web reste possible et connue. |

**Conséquence** : version **1.45**. Suite de non-régression dédiée (`test_positions_portails_v145.py`) : sélection « dernier point AVANT l'heure » (séries Wialon/MZoneX simulées, bornes locales→UTC), passe d'intégration (écriture exacte des 3 heures, réécriture libre jour courant jusqu'à minuit), réparation O2 (signature identique corrigée + audit ; immobile légitime → rien ; ligne saine intouchée ; cellule vide complétée ; portails en panne → rien d'écrit), nomenclature O3 (précis > générique, centre ≤ 3 km, « proche » conservé, coordonnées en dernier recours), formats 27/53 colonnes inchangés.

---

## CONFORMITÉ

- Règles applicables : §1.1, §5.1, §8, §12.1
- Source de données : MZoneX — Onglet Événements (temps réel) + Onglet Trajets (officiel)
- Attribution des jours : < 01:00 → HIER (vérifié)
- Manœuvres < 0,3 km : ignorées (vérifié)
- Seuils respectés : TCC_MAX = 4h30, TCJ_MAX = 10h, TTJ_MAX = 12h
- Emplacement Tn : remplacement provisoire → officiel dans le même créneau (vérifié)
- Checklists validées : [x] §D.1  [x] §D.4  [x] §D.5
- Points d'attention / ambiguïtés soulevées : …
```

---

## PARTIE B — RÈGLES MÉTIER

INDEX DES RÈGLES (pour référence précise dans vos demandes)

§1 Temps de conduite (TCC / TCJ / TTJ) · §2 Validation trajets & pauses
§3 Cycle de vie d'un trajet · §4 Affichage suivi journalier
§5 Détection de fin de trajet · §6 Validation trajet en cours
§7 Manœuvres ignorées · §8 Attribution des jours
§9 Pré-consolidation à 02h00 · §10 Structure des colonnes
§11 Extraction des données · §12 Seuils & paramètres

---

### 1. Temps de Conduite (TCC / TCJ / TTJ)

#### 1.1 TCC — Temps de Conduite Continu

> **DÉFINITION :** Durée de conduite **SANS** pause supérieure ou égale à 30 minutes (pause TCC).

Le TCC est une valeur COURANTE : temps de conduite continu depuis la dernière pause ≥ 30 min.

- Arrêt < 30 min → TCC continue (la pause courte est incluse dans le TCC, elle ne le coupe pas).
- Arrêt ≥ 30 min (pause TCC) → TCC RESET à 0 ; un nouveau TCC commence au prochain trajet.
- Seuil maximum : 4h30. Si un segment continu atteint/dépasse 4h30 → alerte (dépassement).

⚠️ La colonne TCC affiche le segment continu courant, PAS une somme cumulée de la journée.

Exemple corrigé (illustrant reset + dépassement) :

```
Trajet 1 : 05:00 → 09:00    (4:00 de conduite continue)
Pause 1  : 09:00 → 09:20    (20 min) → PAS de pause TCC → TCC continue
Trajet 2 : 09:20 → 11:00    (1:40)
Pause 2  : 11:00 → 11:30    (30 min) → EST une pause TCC → TCC RESET
Trajet 3 : 11:30 → 14:00    (2:30)
Segments continus :
  Segment A (avant pause 2) = Trajet1 + Pause1(incluse) + Trajet2 = 4:00 + 0:20 + 1:40 = 6:00  🔴 DÉPASSE 4h30
  Segment B (après pause 2) = Trajet3                                    = 2:30  ✅
TCC affiché à la fin = 2:30 (segment courant)
Alerte : le Segment A a atteint 6:00 → violation de TCC_MAX
```

#### 1.2 TCJ — Temps de Conduite Journalier

> **DÉFINITION :** Somme des durées de **conduite pure** depuis le départ du 1er trajet jusqu'à l'instant, en **excluant** :
> - tous les **arrêts ≥ 20 min**
> - toutes les **pauses ≥ 30 min**

Équivalent pratique : `TCJ = Σ (durées des segments où le véhicule roule)`. Les arrêts/pauses ne sont simplement pas comptés.

Seuil maximum : 10h00.

Exemple corrigé :

```
Trajet 1 : 05:00 → 09:00    → +4:00  (conduite)
Arrêt 1  : 09:00 → 09:30    → exclu  (≥ 20 min)
Trajet 2 : 09:30 → 11:00    → +1:30
Pause 2  : 11:00 → 11:30    → exclu  (≥ 30 min)
Trajet 3 : 11:30 → 14:00    → +2:30
TCJ = 4:00 + 1:30 + 2:30 = 8:00   ✅ (≤ 10:00)
```

#### 1.3 TTJ — Temps de Travail Journalier

> **DÉFINITION :** `TTJ = TCJ + TOUS les arrêts et pauses` (y compris ceux < 20 min).

Seuil maximum : 12h00.

Exemple (avec TCJ corrigé) :

```
TCJ = 08:00  (conduite pure, voir §1.2)
+ Arrêt court 1 (20 min) + Arrêt court 2 (20 min) = 00:40
TTJ = 08:00 + 00:40 = 08:40   ✅ (≤ 12:00)
```

#### 1.4 Récapitulatif

| Indicateur | Définition | Seuil Max | Ce qui est déduit / inclus |
|---|---|---|---|
| TCC | Conduite sans pause ≥ 30 min (segment continu courant) | 04h30 | Pause < 30 min incluse (ne coupe pas) ; pause ≥ 30 min reset |
| TCJ | Somme de la conduite pure | 10h00 | Tous arrêts ≥ 20 min ET pauses ≥ 30 min exclus |
| TTJ | TCJ + tous les arrêts/pauses | 12h00 | Rien n'est déduit (tout est ajouté) |

---

### 2. Validation des Trajets et Pauses

#### 2.1 Validation d'un Trajet

> **RÈGLE :** Un trajet est **VALIDE** si sa distance est **≥ 0,3 km**.

| Condition | Résultat |
|---|---|
| Distance ≥ 0,3 km | Trajet VALIDE |
| Distance < 0,3 km | Trajet = MANŒUVRE (ignoré, §7) |

#### 2.2 Validation d'une Pause

> **RÈGLE :** Une pause est **OFFICIELLE** si sa durée est **≥ 20 minutes**.

| Durée | Statut | Effet sur TCC | Effet sur TCJ |
|---|---|---|---|
| < 20 min | Manœuvre / Arrêt court | Ne coupe PAS le TCC | Ne se déduit PAS |
| 20–30 min | Pause valide | Ne coupe PAS le TCC | Se déduit |
| ≥ 30 min | Pause TCC | Coupe le TCC (reset) | Se déduit |

---

### 3. Cycle de Vie d'un Trajet

#### 3.1 Flux complet

```
TRAJET COMMENCE (sans fin)
   │
   ├─ ÉTAPE 1 : écrire le DÉBUT en PROVISOIRE (orange / italique)
   ├─ ÉTAPE 2 : écrire le TCC provisoire (orange)
   ├─ ÉTAPE 3 : attendre… le chauffeur roule (temps réel)
   ├─ ÉTAPE 4 : VALIDER le trajet (via vitesse/distance ou Onglet Trajets)
   │
   └─ Le trajet est-il VALIDE (distance ≥ 0,3 km) ?
          │
       OUI ──► OFFICIEL (noir) : écrire fin + pause si ≥ 20 min
       │         → TRAJET SUIVANT (retour ÉTAPE 1)
       │
       NON ──► EFFACER les données provisoires (manœuvre)
                 → Attendre prochain départ (retour ÉTAPE 1)
```

#### 3.2 Règle de Remplacement (ABSOLUE)

> Un trajet officiel doit **REMPLACER** les données provisoires dans le **MÊME emplacement** `T1`, `T2`, …, jamais créer un nouvel emplacement.

❌ INCORRECT (deux emplacements différents) :

```
| T1 debut | T1 fin | T2 debut | T2 fin |
| 05:53    |        | 05:53    | 10:13  |
   PROV                   OFFICIEL
```

✅ CORRECT (même emplacement, données remplacées) :

```
| T1 debut | T1 fin | T2 debut | T2 fin |
| 05:53    | 10:13  | 10:52    | 13:32  |
   OFFICIEL
```

---

### 4. Affichage dans le Suivi Journalier

#### 4.1 Règle fondamentale

> **RÈGLE :** SI la colonne de **début** contient des données, ALORS la colonne **TCC** doit aussi contenir des données, **même si le trajet est encore provisoire**.

| Situation | Début | TCC | Fin | Affichage |
|---|---|---|---|---|
| Rien commence | Vide | Vide | — | Rien |
| Trajet commence, pas terminé | 05:31 (orange) | 2:30 (orange) | Vide | Provisoire |
| Trajet terminé | 05:31 (noir) | 4:30 (noir) | 10:13 (noir) | Officiel |

#### 4.2 Tableau de décision d'affichage

| Condition | Couleur | Style | Exemple |
|---|---|---|---|
| Trajet VALIDE (≥ 0,3 km) | NOIR | Normal | 05:33 |
| Trajet INVALIDE (< 0,3 km) | EFFACER | — | — |
| Pause VALIDE (≥ 20 min) | NOIR | Normal | 0:35 |
| Pause INVALIDE (< 20 min) | NE PAS ÉCRIRE | — | — |
| Trajet PROVISOIRE (temps réel) | ORANGE | Italique | 05:53 |
| TCC provisoire | ORANGE | Normal | 2:30 |

#### 4.3 Cas du trajet en cours

```
Debut | TCC | Fin | Pause | T2 debut | T2 fin | ...
05:53 | 2:30|     |       |          |        |
(org) |(org)|     |       |          |        |
  ↑       ↑
  │       └─ Même si provisoire, le TCC est affiché
  └─ Provisoire
```

---

### 5. Détection de Fin de Trajet

#### 5.1 Principe fondamental

> **RÈGLE :** La fin d'un trajet = le **PREMIER ARRÊT** (vitesse ≤ `SEUIL_VITESSE_ARRET`, soit ≤ 3 km/h depuis l'arbitrage §0ter E du 14/08/2026) **APRÈS** un trajet valide.

❌ INCORRECT : `SI distance > 0,3 km → nouveau trajet, fin = dernière position`

✅ CORRECT :

```
SI vitesse ≤ 3 km/h (premier arrêt — §0ter E du 14/08/2026) ALORS
    → fin de trajet POSSIBLE
    → VÉRIFIER : le mouvement précédent était-il valide (distance ≥ 0,3 km) ?
        SI oui  → Fin = moment de ce premier arrêt
        SINON  → c'était une manœuvre, continuer
```

#### 5.2 Exemple — Cas 0616TCC

```
5:31 ───────▶ 8:24 ─▶ ... ─▶ 8:40 ───────▶ 9:56
  │            │              │              │
  │  TRAJET 1  │  MANŒUVRES   │  TRAJET 2    │
  │ (VALIDE)   │  (< 0,3 km)  │  (VALIDE)    │
  │            │  × 4         │  1,442 km    │
  ▼            ▼              ▼              ▼
Debut        FIN = 8:24     (manœuvres)   FIN = 9:56
               ✅ CORRECT     ignorées       ✅
```

Chronologie :

- 5:31 — Départ dépôt (début Trajet 1)
- 8:24 — PREMIER ARRÊT (fin Trajet 1) ✅
- 8:24 → 8:40 — Manœuvres (< 0,3 km chacune) — IGNORER
- 9:48 — REPRISE (début Trajet 2)
- 9:56 — Arrivée (fin Trajet 2)

Affichage correct :

```
| T1 debut | T1 fin | Pause 1 | T2 debut | T2 fin |
| 5:31     | 8:24   | 1:24    | 9:48     | 9:56   |
                          ↑
          Pause = 9:48 − 8:24 = 1h24 (84 min ≥ 30 min → pause TCC)
```

---

### 6. Validation Trajet en Cours

#### 6.1 Critères (« a vraiment commencé »)

| Critère | Seuil | Description |
|---|---|---|
| Durée | ≥ 15 minutes | Temps depuis le départ |
| Vitesse moyenne | ≥ 12 km/h | Vitesse moyenne du mouvement |
| Distance | ≥ 0,3 km | Distance parcourue |

> ⚠️ **Portée (arbitrage LSS du 06/08/2026, D)** : ces critères ne s'appliquent
> QU'AUX FLUX DE POSITIONS GPS (points intermédiaires). Ils sont INAPPLICABLES
> à MZoneX — aucun événement n'est émis entre « Début du trajet » et « Fin du
> trajet » : tout début de trajet MZoneX s'affiche DONC IMMÉDIATEMENT en orange
> « en cours » et est jugé à la clôture (§6.2, §11.1).

#### 6.2 Stratégie hybride

PHASE 1 — TEMPS RÉEL : analyser les événements GPS seconde par seconde.

`SI durée ≥ 15 min ET vitesse_moyenne ≥ 12 km/h ALORS → afficher début provisoire + TCC provisoire`

PHASE 2 — VALIDATION : attendre que le trajet apparaisse dans l'Onglet Trajets MZoneX.

`SI distance ≥ 0,3 km → officialiser (noir) ; SINON → effacer les données provisoires`

---

### 7. Manœuvres et Trajets Ignorés

#### 7.1 Définition

> **MANŒUVRE :** tout mouvement dont la distance est **< 0,3 km**.

#### 7.2 Règle de traitement

| Type | Distance | Action |
|---|---|---|
| Manœuvre | < 0,3 km | IGNORER — ne pas créer de trajet |
| Trajet non valide | < 0,3 km | EFFACER les données provisoires |

#### 7.3 Exemple

```
Camion arrive à 8:24 → mouvements < 0,3 km : 8:24 → 8:27 → 8:32 → 8:36 → 8:40
Tous = MANŒUVRES → IGNORER pour la construction des trajets
```

---

### 8. Attribution des Jours

#### 8.1 Règle fondamentale

> `Trajet.jour_attribution = DATE(heure_debut)`, **SAUF** la règle §8.3.

#### 8.2 Cas limites (corrigé — voir correction #3)

Un trajet appartient au jour de son heure de début, sauf si elle est < 01:00 (alors → HIER).

| Heure début | Jour d'attribution | Exemple |
|---|---|---|
| 23:00 | HIER | Trajet 23:00 → 23:50 |
| 23:40 | HIER | Trajet 23:40 → 00:30 |
| 00:15 | HIER | Trajet 00:15 → 01:05 (corrigé : < 01:00) |
| 00:50 | HIER | Trajet 00:50 → 01:40 |
| 01:05 | AUJOURD'HUI | Trajet 01:05 → 02:00 |

#### 8.3 Règle supplémentaire (prime)

> **SI `heure_debut < 01:00` ALORS `jour = HIER`** (continuation de la veille).

---

### 9. Pré-Consolidation à 01h00

#### 9.1 Règle

> **À 01h00 chaque jour, le système doit :**
> 1. Arrêter tous les trajets en cours dont le début < 01:00.
> 2. Leur attribuer le **jour de la veille** (car début < 01:00).
> 3. Valider toutes les heures → **OFFICIELLES**.
> 4. Transférer vers l'**HISTORIQUE**.

#### 9.2 Résultat

| Zone | Contenu |
|---|---|
| Hier | 100 % OFFICIEL (y compris trajets finissant après minuit) |
| Aujourd'hui | Commence quand un trajet débute ≥ 01:00 |

#### 9.3 Exemple

```
- Trajet 1 : 23:00 → 23:50  → Jour = HIER
- Trajet 2 : 23:55 → 00:45  → Jour = HIER (début < 01:00)
- Trajet 3 : 01:00 → 01:50  → Jour = AUJOURD'HUI (début = 01:00)
- Trajet 4 : 02:05 → 03:00  → Jour = AUJOURD'HUI (début ≥ 01:00)
RÉSULTAT :
- Hier        : Trajets 1, 2, (consolidés à 01:00)
- Aujourd'hui : Trajet 3, 4 (début de la nouvelle journée)
```

---

### 10. Structure des Colonnes

#### 10.1 Ordre des colonnes

```
| TCC | TCJ | TTJ | T1 debut | T1 fin | Pause 1 | T2 debut | T2 fin | Pause 2 | ... | Arret Final | Lieu Arret |
```

> 🔴 Les indicateurs TCC/TCJ/TTJ sont en **PREMIER** pour alerter immédiatement.

#### 10.2 Description

| Colonne | Contenu | Format | Exemple |
|---|---|---|---|
| TCC | Temps de Conduite Continu | H:MM | 3:15 |
| TCJ | Temps de Conduite Journalier | H:MM | 8:30 |
| TTJ | Temps de Travail Journalier | H:MM | 9:10 |
| T1 debut | Heure début trajet 1 | HH:MM | 05:33 |
| T1 fin | Heure fin trajet 1 | HH:MM | 10:13 |
| Pause 1 | Durée pause après T1 | H:MM | 0:39 |
| … | … | … | … |
| Arret Final | Heure d'arrêt final | HH:MM | 16:30 |
| Lieu Arret | Nom du lieu d'arrêt | Texte | Antananarivo |

#### 10.3 Règles de format

Format heure : HH:MM · Format durée : H:MM · Colonne vide : « — » (tiret)

---

### 11. Extraction des Données (MZoneX / CamtrackPro)

#### 11.1 MZoneX — Stratégie hybride

ÉTAPE 1 — Onglet TRAJETS : tous les trajets TERMINÉS (veille/passé) = données OFFICIELLES (source de validation).
ÉTAPE 2 — Onglet ÉVÉNEMENTS : le trajet EN COURS (s'il existe) = donnée TEMPS RÉEL.
ÉTAPE 3 — Fin du trajet en cours : revenir à l'Onglet TRAJETS, vérifier l'enregistrement, marquer OFFICIEL.

#### 11.2 CamtrackPro — Approche actuelle

Rapport « Détail Trajet groupe de véhicules » :

- Trajet valide si distance ≥ 0,3 km
- Pause valide si durée ≥ 20 min
- Pause TCC si durée ≥ 30 min

> ✅ **Arbitrage LSS du 06/08/2026 (C)** : la règle historique « en mouvement
> ≥ 20 min » est SUPPRIMÉE — la distance ≥ 0,3 km est la SEULE condition de
> validité d'un trajet CamtrackPro (§2.1). La colonne « en mouvement » du
> rapport reste lue à titre informatif (cumuls), sans pouvoir de rejet.

---

### 12. Seuils et Paramètres

#### 12.1 Paramètres constants

| Paramètre | Valeur | Unité | Description |
|---|---|---|---|
| `SEUIL_DISTANCE_MIN_TRAJET` | 0,3 | km | Distance minimum pour valider un trajet |
| `DUREE_MIN_PAUSE_VALIDE` | 20 | min | Durée minimum pour valider une pause |
| `DUREE_MIN_PAUSE_TCC` | 30 | min | Durée minimum pour couper le TCC |
| `SEUIL_DUREE_MIN_MOUVEMENT` | 15 | min | Durée minimum pour détecter un trajet en cours |
| `SEUIL_VITESSE_MOYENNE` | 12 | km/h | Vitesse moyenne minimum pour valider un trajet |
| `SEUIL_VITESSE_ARRET` | 3 | km/h | Vitesse considérée comme arrêt (≤ 3 km/h — §0ter E du 14/08/2026 : 5 km/h du 06/08 au 13/08) |
| `TCC_MAX` | 4,5 | h | Temps de Conduite Continu maximum |
| `TCJ_MAX` | 10 | h | Temps de Conduite Journalier maximum |
| `TTJ_MAX` | 12 | h | Temps de Travail Journalier maximum |
| `HEURE_PRE_CONSOLIDATION` | 01:00 | HH:MM | Heure de pré-consolidation |

#### 12.2 Seuils variables (en base)

Ces valeurs doivent être stockées dans la table `ParametrageSeuil` et modifiables par l'administrateur (ne pas les coder en dur).

---

## PARTIE C — CHECKLISTS CONSOLIDÉES (pour l'implémenteur)

- [ ] §C.1 Temps de conduite : TCC = conduite sans pause ≥ 30 min (pause < 30 min incluse, ≥ 30 min reset) · TCJ = conduite − (arrêts ≥ 20 min + pauses ≥ 30 min) · TTJ = TCJ + TOUS les arrêts/pauses · Vérifier seuils 4h30 / 10h / 12h.
- [ ] §C.2 Validation : trajet valide si ≥ 0,3 km · pause valide si ≥ 20 min · pause TCC si ≥ 30 min (reset TCC) · manœuvres (< 0,3 km) ignorées.
- [ ] §C.3 Affichage : SI début ≠ vide ALORS TCC ≠ vide (même provisoire) · début provisoire = orange/italique · début officiel = noir · TCC provisoire = orange · trajets en emplacements FIXES · officialiser = REMPLACER dans le même emplacement.
- [ ] §C.4 Détection fin : fin = premier arrêt (≤ 3 km/h — §0ter E) APRÈS un trajet valide (≥ 0,3 km) · ne PAS prendre fin de manœuvre · exemple 0616TCC : fin = 8:24 (pas 8:40).
- [ ] §C.5 Attribution jours : `jour = DATE(heure_debut)` · SI `heure_debut < 01:00` ALORS `jour = HIER`.
- [ ] §C.6 Pré-consolidation 01h00 : pour chaque trajet EN_COURS avec début < 01:00 → arrêter à 01:00, jour = HIER, marquer OFFICIEL, transférer vers HISTORIQUE · résultat : 0 % provisoire dans l'archive.
- [ ] §C.7 Structure colonnes : ordre `TCC|TCJ|TTJ|T1 debut|T1 fin|Pause 1|T2 debut|T2 fin|Pause 2|…|Arret Final|Lieu Arret` · HH:MM / H:MM · vide = « — ».

---

## PARTIE D — GLOSSAIRE

| Terme | Définition |
|---|---|
| TCC | Temps de Conduite Continu (segment sans pause ≥ 30 min) |
| TCJ | Temps de Conduite Journalier (somme de la conduite pure) |
| TTJ | Temps de Travail Journalier (TCJ + tous arrêts/pauses) |
| Manœuvre | Mouvement < 0,3 km (ignoré) |
| Pause valide | Arrêt de 20–30 min (déduit du TCJ, ne coupe pas TCC) |
| Pause TCC | Arrêt ≥ 30 min (déduit du TCJ et reset du TCC) |
| Arrêt | Vitesse ≤ 3 km/h (`SEUIL_VITESSE_ARRET`, §0ter E du 14/08/2026) |
| Provisoire | Donnée temps réel non encore officialisée (orange) |
| Officiel | Donnée validée (noir), issue de l'Onglet Trajets |
| Pré-consolidation | Tâche à 01:00 figeant les trajets < 01:00 au jour de la veille |
| MZoneX / CamtrackPro | Sources de données GPS de la flotte |

---

## PARTIE E — DOCUMENTS DE RÉFÉRENCE INTÉGRÉS

Ce document consolide les addenda : BLUEPRINT_LSS_Tracking, v1.1 (Suivi Journalier/Historique), v1.2 (Extraction MZoneX/CamtrackPro), v1.3 (Validation Interfaces), v1.4 (Stratégie Hybride MZoneX), v1.5 (Conditions de Validité), v1.6 (Logique Affichage), v1.7 (Correction Doublons), v1.8 (Validation Temps Réel), v1.9 (Règles TCC/TCJ/TTJ), v1.10 (Correction Erreurs).

Fin du document — RÉFÉRENCE CONSOLIDÉE v2.

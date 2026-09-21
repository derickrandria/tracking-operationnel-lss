# DÉPLOIEMENT v1.48 — état au 17/09/2026 10:15

Commit poussé sur votre branche : **`52dc843`** (+ mise à jour des outils,
voir §5). Aucune migration de base, aucun rebuild frontend.

---

## 1. Résultat de votre premier bilan : ce qu'il dit

| Observation | Lecture |
|---|---|
| `/api/sante` **sans** les champs `sources_en_echec` / `collecte_par_source` | **Le service n'a pas été redémarré** : le processus en cours exécute encore l'ancien code (le `git pull` a bien mis à jour les fichiers, mais Python garde en mémoire le code chargé au démarrage). Le statut affiché reste donc aveugle. |
| `[2]` `TimeoutError` après 180 s | L'endpoint `/api/sante/sync` est **synchrone** : il enchaîne MZoneX (qui bloque en attendant l'IdP) puis CamtrackPro puis la synchro N2. 180 s ne suffisent pas quand une source est en panne. Le cycle a d'ailleurs continué côté serveur. **Corrigé dans l'outil v2** : cycle forcé en option (`--sync`), délai porté à 900 s, message d'attente explicite. |
| MZONEX : camions muets depuis **≈ 08:40** (1,5 h avant 10:10), 3 jamais vus | MZoneX **fonctionnait ce matin** puis s'est tu. Signature classique : le jeton d'accès (durée de vie **1 h**) a expiré vers 08:40, le code a basculé sur l'actualisation puis la reconnexion complète — **c'est exactement le chemin que le correctif traite**. |
| Archives : « sans trajet » sur 7 jours | Signal **trop grossier** de l'outil v1 : un camion peut n'avoir pas roulé. **Corrigé en v2** : la journée est croisée avec les **mouvements GPS réellement présents** et avec les **audits** (`cycle_minuit.relecture_partielle`, `jour.catchup_consolide_local`…). |
| `55` actifs mais `58` véhicules listés | 3 véhicules inactifs (dont `0926TBV` en MAINTENANCE) — normal. |

---

## 2. Action immédiate : redémarrer le service

```powershell
# même méthode que d'habitude, selon votre installation :
#   demarrer.bat          (Windows)
#   docker compose up -d  (Docker)
#   .\run.sh              (script fourni)
```

**Contrôle en une seconde** que le nouveau code tourne :

```powershell
cd backend ; python3 verifier_collecte.py
```

Attendu dans `[1]` :
```
sources_en_echec       [...]
  ❌ MZONEX   dernière réussite=... erreur=...
```
Si vous voyez ces deux lignes → le service est à jour. Si vous voyez encore
« CHAMPS v148 ABSENTS » → le redémarrage n'a pas pris (service relancé depuis un
autre dossier, conteneur non reconstruit, ancien processus toujours actif).

---

## 3. Ce que le correctif fait pour MZoneX

1. **Un seul client httpx** pour tout le flux SSO : la page de connexion et le
   POST partagent désormais les mêmes cookies (session + jeton anti-CSRF). Avec
   deux clients, le POST partait sans cookie → l'IdP répond **400 sans
   `Location`**, que l'ancien message traduisait à tort par « identifiants
   portail à vérifier ».
2. `Referer` renseigné sur le POST (certains IdP le refusent vide).
3. En cas d'échec, la **preuve est conservée** : code HTTP, URL, cookies
   présents, **début du corps de la réponse du portail** (identifiants masqués).
   C'est cette ligne qui dira la cause exacte si le problème persiste.

Si après redémarrage MZoneX repart : la cause était bien le flux SSO (jeton
expiré → reconnexion → cookies perdus). Si MZoneX échoue encore, **envoyez la
ligne d'erreur telle quelle** : elle contient la réponse du portail.

---

## 4. Reprise des archives : ne pas se précipiter

Pendant la panne, chaque minuit a scellé la journée avec ce que la base
contenait (règle D4 « minuit n'attend pas »), et `archiver_jour` ne réécrit
jamais une archive existante. La reprise est donc **nécessaire mais ciblée** :
seules les journées où une source manquait **et** où des camions ont réellement
roulé méritent un recalcul.

L'outil v2 les désigne précisément :

```
⚠️ À REPRENDRE  16/09  archives=55 · a bougé sans trajet=3
        audits : cycle_minuit.relecture_partielle×1
        └ 0616TCD  a roulé ce jour-là, archive sans trajet (6 points en mouvement)
```

Recommandation : **attendre que MZoneX recollecte** (les portails relus pendant
le recalcul d'une journée doivent répondre), puis recalculer les journées
signalées, une par une :

```powershell
# via l'API (utilisateur admin), après redémarrage
curl -X POST -H "Authorization: Bearer <jeton>" ^
     "http://127.0.0.1:8000/api/suivi/recalculer-archive?date_jour=2026-09-16"
```

> Remarque importante : CamtrackPro/Wialon est **sain** sur votre serveur
> (6 événements au dernier cycle) — les journées où seul MZoneX manquait
> pourront être reprises dès que le SSO est rétabli, sans attendre d'autres
> corrections.

---

## 5. Outils livrés (tous en lecture seule)

| Outil | Ce qu'il mesure |
|---|---|
| `backend/verifier_collecte.py` | état de santé, sources en échec, **détection « service non redémarré »**, flotte par portail, véhicules jamais vus, camions muets, journées à reprendre (mouvements GPS × archives × audits) |
| `backend/verifier_cloture_journees.py` | lignes restées PROVISOIRE sur les journées passées (écran **et** archives) |
| `backend/verifier_sante.py` | bilan 7 axes en une commande |
| `backend/verifier_appels_morts.py` | garde-fou CI : appelant ↔ signature (rc=1 si dérive) |

---

## 6. Suites de tests

**30/43 → 32/43** avec le commit `52dc843`, aucune régression.
Rouges restants = chantiers distincts (v113, v125, v130 D4-2, v138, v14, v143,
v121, v145 B9, v122, reglement_metier_missions, runner).


---

## 7. v1.48-bis — les deux corrections demandées (poussées le 17/09)

### a) Compteurs bornés à la dernière preuve (`chaines.py`, `serializers.py`, `engine.py`)

Constat de vos captures : 17 camions affichaient « TCC 0:02 · TCJ 3:58 ·
TTJ 3:58 » à 12:42 pour un dernier mouvement à **08:43** — 3:58 était le temps
écoulé depuis le dernier signal, pas une durée de conduite.

Règle appliquée (`fin_bornee_ouverte`) : la ligne ouverte **reste « en cours »**
à l'écran (arbitrage v1.16 préservé : un vrai long trajet ne disparaît pas),
mais ses **compteurs se figent à `min(maintenant, dernière trace + 30 min)`**.
Monotone (la trace ne recule jamais), aligné sur la frontière du badge
« boîtier muet », et appliqué **partout** : écran, export, archive **et**
`engine.recalculer_temps` (donc les valeurs stockées).

Mesuré sur une instance réelle, scénario exact de vos captures :

| | Avant | Après |
|---|---|---|
| TCJ / TTJ de `0576TCD` (dernier signal 08:43) | ≈ 166 min (et jusqu'à minuit) | **30 min**, figés |
| État de la ligne | EN_COURS | EN_COURS (inchangé) |

**Bonus mesuré** : `test_chaines_v113` passait 31 OK / **10 KO** — il est
désormais **41 OK / 0 KO**. Ces 10 KO portaient précisément sur les compteurs
des lignes ouvertes.

### b) Badge honnête (`scrapers.py`, `main.py`, `operations.py`, frontend)

`scrapers.sources_en_echec()` expose les portails dont la **dernière tentative a
échoué** (une réussite remet `derniere_erreur` à `None`). `/api/suivi` transporte
la liste, `s_suivi` porte `plateforme_gps`, et la grille affiche :

- **rouge** « source MZONEX en panne — collecte interrompue » quand la panne est
  **globale au portail** (il n'y a rien à attendre) ;
- **orange** « boîtier muet — données en transit » sinon (zone sans réseau,
  remontée automatique attendue) — message d'origine conservé.

Le bundle `frontend/dist` a été **reconstruit et committé** (c'est lui qui est
servi : `app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST))`).

### c) Trois erreurs de type corrigées (le build était aveugle)

`npx tsc --noEmit` renvoie désormais **0 erreur** (il en renvoyait 3) :
`Missions.tsx` lisait `v.modele` (champ inexistant → libellé vide en silence),
`TempsConduite.tsx` utilisait un repli aux clés périmées (`nb_chauffeurs_actifs`
au lieu de `total_chauffeurs`…) et incomplet (`du`/`au`/`seuils` manquants).

### d) Suites de tests

**34 / 44** (contre 30/43 avant le premier commit) — dont la nouvelle suite
`test_compteurs_muets_v148.py` **14 OK / 0 KO**. Rouges restants (10, chantiers
distincts) : v125, v143, v138, v145 (47/1), v122 (25/1), v14, v121,
reglement_metier_missions, v130 (25/1), runner.

### À faire de votre côté

```powershell
git pull origin arena/01a0aa2b-tracking-operationnel-lss
# redémarrer le service (le bundle front est déjà committé : rien à builder)
```

Attendu à l'écran (une fois la source MZONEX rétablie ou non) : les lignes de
camions MZoneX affichent **« source MZONEX en panne — collecte interrompue »** au
lieu de « données en transit », et leurs compteurs ne gonflent plus.


---

## 8. v1.49 — les heures de départ venaient de nos propres photos

Vos captures du 17/09 (Suivi + portail MZoneX côte à côte) ont mis au jour deux
défauts de fond, **corrigés et vérifiés** (`DIAGNOSTIC_HEURES_DEPART_v149.md`
pour le détail, `backend/test_heure_depart_v149.py` pour la preuve) :

| Constat | Cause | Correctif |
|---|---|---|
| `2746TCC` : départ affiché **15:32** alors que MZoneX publie **14:08:56 → 15:47:18** | `dernieres_positions()` (endpoint `Vehicles` = « dernière position connue ») était ingéré comme un **événement** ; `ingest_event` ouvrait la ligne à l'heure de la photo | marqueur `observation: True` → une photo met à jour la trace et l'état observé, mais **n'ouvre, ne ferme ni ne soude jamais une ligne** |
| `4006TBS` : départ **08:43** · `0916TBV` : départ **08:43**, TCC 0:02 / TCJ 7:46 pour un camion resté à l'arrêt (0 km/h, odomètre figé) | `_synchroniser_dernier_point_mzonex()` (appelé à chaque **timeout N1** et par « Sync GPS ») **fabriquait** un événement « il y a 2 minutes » et forçait `last_event_at = maintenant − 2 min` | plus aucune trace fabriquée : le silence est **compté** (`portail_muet`, `statut: PARTIEL`) et remonté dans la réponse de `sync-gps` |

Le second défaut expliquait aussi trois choses que vous nous aviez signalées :

1. le repère **« boîtier muet » ne pouvait jamais s'afficher** (la fausse trace
   disait toujours « il y a 2 min ») — le faux vert contredisait `/api/sante` ;
2. le **bornage v1.48 des compteurs** (« dernière trace + 30 min ») était
   neutralisé, la fausse trace étant toujours fraîche → les compteurs couraient
   encore jusqu'à minuit ;
3. l'arbitrage R2 (`rattraper_ouvertures` : signal ≤ 15 min **et** vitesse
   > 3 km/h) croyait à un roulage en cours et **rouvrait une ligne datée du
   dernier événement connu** → les heures de départ fantômes (08:43, 15:32).

Bonus du même passage : ces événements s'écrivaient **sans clé
d'idempotence** (empilement à chaque clic sur « Sync GPS ») — corrigé.

### Vérification

- `backend/test_heure_depart_v149.py` — **13 OK / 0 KO** (photo ≠ événement,
  photo après un vrai départ, portail muet jamais inventé, signal périmé → R2
  n'ouvre rien, roulage réel et frais → R2 ouvre toujours) ;
- `backend/test_compteurs_muets_v148.py` — **14 OK / 0 KO** (non-régression) ;
- campagne complète : **37 verts / 45** (les 8 rouges restants sont des
  chantiers déjà connus et inchangés : v125, v143, v138, v14, v121,
  reglement_metier_missions, v145, v122, v130 ; `runner_complet` repasse au
  vert). `test_mzonex_ping` et `test_e2e_reel_v113` sont **environnementaux**
  (TLS MZoneX injoignable depuis la machine de contrôle, base bac à sable
  absente) — pas des régressions.

### Nouveau vérificateur à votre disposition

```powershell
python verifier_heures_depart.py                     # journée en cours
python verifier_heures_depart.py --date 2026-09-17 --suspects
python verifier_heures_depart.py --plaque 2746TCC
```

Lecture seule, aucun accès réseau : pour chaque camion il affiche l'heure de
départ **affichée**, la provenance de chaque ligne (portail, statut, distance),
les audits de la journée (`trajet.ouverture_rattrapage` = ligne ouverte par R2,
`trajet.rejet_distance`, `trajet.reactivation`) et le dernier signal réel — puis
signale les lignes douteuses (« départ sans événement à ±15 min », « ligne en
cours alors que le boîtier est muet depuis X min »).


---

## 9. v1.50 — « database is locked » : ce n'était pas MZoneX, c'était nous

Votre relevé `/api/sante` du 18/09 (10:08) contenait trois bonnes nouvelles : le
jeton Wialon est présent, CamtrackPro collecte (`derniere_reussite 10:08:26`,
5 points), et **la base se remplit en temps réel** (`dernier_evenement_gps
10:08:03`, retard 0,6 min). Aucune erreur d'authentification, aucun HTTP 5xx,
aucun timeout vers le portail.

La seule erreur était la nôtre :

```
OperationalError: (sqlite3.OperationalError) database is locked
[SQL: INSERT INTO collecte_checkpoints ...]
```

`collecte_checkpoints` est le **journal** des passes de collecte, écrit **avant**
l'appel au portail. La plateforme déclarait donc « MZONEX en panne » sans même
avoir interrogé MZoneX.

### Les causes (mesurées, cf. `DIAGNOSTIC_VERROUS_SQLITE_v150.md`)

| Cause | Mesure |
|---|---|
| Tout un lot dans **une seule transaction** | 10 000 points = **10,9 s** de verrou d'écriture ; une fenêtre d'arrêt de 25 h ≈ **44 s** — au-delà du `busy_timeout` de 30 s |
| Le **journal** qui fait tomber la collecte | le checkpoint s'écrit avant l'appel : un verrou sur cet INSERT avortait toute la passe N1 |
| Un **timeout sans effet** | le thread n'était pas tué : il continuait d'écrire (et de tenir le verrou) pendant que le cycle suivant démarrait |
| **Relecture sans plafond** | elle repartait à chaque cycle sur la même fenêtre géante jamais terminée |
| **Diagnostic trompeur** | un verrou de base s'affichait comme une panne de portail |

### Les correctifs

1. **commit par lots** (`COLLECTE_LOT_INSERTION=250`) + **rejeu idempotent** du
   lot en cas de verrou résiduel ;
2. **souffle inter-lots** (`COLLECTE_SOUFFLE_S=0,01`) → blocage continu du
   verrou **3 810 ms → 270 ms** ;
3. le **checkpoint ne lève plus jamais** : une trace ne commande pas la collecte ;
4. la passe interrompue est **laissée se terminer** (bornée, 60 s) au lieu de
   semer un orphelin qui tient le verrou ;
5. **budget par cycle** pour la relecture (`RELECTURE_N1_MAX_POINTS=4000`,
   `RELECTURE_N1_TIMEOUT_S=180`) — la reprise est automatique, les fenêtres
   étant recalculées depuis ce qui manque réellement en base ;
6. **catégorisation des erreurs** — `locale` (base/disque/budget) vs `portail`
   (auth/HTTP/réseau) — exposée par `/api/sante`
   (`sources_en_echec_detail`, `sources_bloquees_localement`,
   `sources_portail_en_panne`, `collecte_bloquee_localement`) et reprise par le
   badge de l'écran : « collecte bloquée localement — base verrouillée ».

**Essayé puis écarté** : `BEGIN IMMEDIATE` (la réponse classique au piège WAL
« lire puis écrire »). Avec lui, même une transaction de **lecture** prend le
verrou d'écriture : deux sessions du même service s'attendent et échouent
(7 suites de tests cassées, mesuré). Le vrai levier était la durée des
transactions. Le mode reste disponible via `LSS_SQLITE_IMMEDIATE=1`, et les
outils de contrôle (`verifier_*.py`) le désactivent explicitement pour ne pas
gêner la collecte avec leurs longues analyses.

### Où regarder désormais

```json
"statut": "COLLECTE_BLOQUEE_LOCALEMENT",
"sources_bloquees_localement": ["MZONEX"],
"sources_portail_en_panne": [],
"sqlite": {"journal_mode": "wal", "busy_timeout": 30000, "synchronous": 1}
```

`sqlite.journal_mode` doit être **`wal`** et `busy_timeout` **30000**. Le champ
`sqlite.erreur` (s'il apparaît) signalerait une base sur support partagé qui
refuse le WAL.

### Vérification

`backend/test_verrous_sqlite_v150.py` — **18 OK / 0 KO** (6000 points + écrivain
concurrent sans échec ; plage bloquée maximale < 1 s ; lots committés conservés
malgré une panne ; checkpoint qui ne lève plus ; catégorisation de l'erreur
exacte de votre relevé). Non-régression v1.48 (14/0) et v1.49 (13/0).

> À retenir : SQLite est une base **mono-écrivain**. Pour 39 boîtiers + plusieurs
> postes qui saisissent en même temps, le chemin propre est PostgreSQL — la
> plateforme l'accepte déjà par simple `DATABASE_URL`, sans modification de code.

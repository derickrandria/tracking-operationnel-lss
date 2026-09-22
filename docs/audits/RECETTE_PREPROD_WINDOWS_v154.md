# Procédure WINDOWS — recette de PRÉPRODUCTION v1.54

**Objet** : exécuter la recette de préproduction (`backend/recette_preprod_v154.py`)
sur un poste Windows, en **lecture seule**, avec preuves vérifiables.

**Commit exigé** : `618c4aed6bd63138109636f48c1623400175988f` (branche
`arena/01a0aa2b-tracking-operationnel-lss`).

**Ce que la recette prouve / ne prouve pas**

| Prouve | Ne prouve pas |
|---|---|
| Les 6 points d'entrée des portails répondent depuis **ce poste et ce réseau** | Qu'une collecte complète aboutit (ce n'est pas son objet) |
| La guérison v1.13 (`test_e2e_reel_v113`) rejouée sur les données de préprod | Le rendu visuel de l'interface |
| La base de préprod n'a **pas** été modifiée (SHA-256 avant/après) | La performance sous charge (voir la campagne) |

---

## 0. Découvrir le moteur de base de la préproduction

**Contexte repo** : le lanceur Windows documenté de l'exploitant (`demarrer.bat`)
utilise le **Python système**, installe `backend/requirements.txt` — **qui ne
contient aucun pilote PostgreSQL** — et démarre `uvicorn`. La base par défaut est
donc **SQLite** : `backend/app/config.py` l.199
(`DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'lss.db'}")`),
fichier `backend/data/lss.db`, **gitignoré** (`.gitignore` l.7).

`docker-compose.yml` décrit l'autre mode (*« passage en production »*,
README §5) : `postgres:16` + `DATABASE_URL=postgresql+psycopg2://…@db:5432/lss`,
avec un service `sauvegarde` qui écrit un `pg_dump` quotidien dans
`./sauvegardes/*.sql.gz`.

**Trois commandes de découverte (lecture seule, aucun secret affiché)** :

```powershell
Set-Location 'C:\LSS\preprod\tracking-operationnel-lss\backend'

# (1) Quelle base le collecteur utilise-t-il réellement ? (mot de passe masqué)
python -c "import re; from app.config import DATABASE_URL as u; print(re.sub(r'://[^@]*@','://***@',u))"

# (2) Collecteur en fenêtre noire (SQLite) ou pile Docker (PostgreSQL) ?
Get-Process | Where-Object { $_.ProcessName -match 'python|docker' } | Select-Object ProcessName, Id
Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue

# (3) La base SQLite locale existe-t-elle, et a-t-elle une activité récente ?
Get-ChildItem 'C:\LSS\preprod\tracking-operationnel-lss\backend\data' -Filter *.db |
    Select-Object Name, Length, LastWriteTime
```

| Résultat de (1) | Chemin à suivre |
|---|---|
| `sqlite:///…\backend\data\lss.db` (attendu) | **PATH A** — toute cette procédure s'applique telle quelle |
| `postgresql+psycopg2://***@…` | **PATH B** — voir §0.1 : **ne lancez pas encore la recette** |

### 0.1 PATH B (base PostgreSQL) — prérequis non outillé à ce jour

L'étape E2E de la recette **exige un fichier SQLite** : le pilote copie la base
fournie en `data/lss.db` puis la suite l'ouvre en SQLite
(`recette_preprod_v154.py` l.147 ; `test_e2e_reel_v113.py` l.9 et l.15). Un dump
PostgreSQL (`.dump`/`.sql.gz`) provoquerait un faux « ÉCART ».

Dans ce cas : **ne lancez pas la recette**, signalez-le — un export PostgreSQL →
SQLite (lecture seule côté PostgreSQL, puis contrôle) doit être écrit **et testé**
avant la recette. La sauvegarde de rollback existe déjà dans ce mode :
`.\sauvegardes\lss_*.sql.gz` (produite par le service `sauvegarde` du compose).

---

## 1. Chemins et variables (aucun secret)

| Élément | Valeur |
|---|---|
| Dépôt de préproduction | `C:\LSS\preprod\tracking-operationnel-lss` |
| **Base de préprod vivante** (PATH A) | `C:\LSS\preprod\tracking-operationnel-lss\backend\data\lss.db` — **ne jamais la donner à la recette** |
| **Copie remise à la recette** | **`C:\LSS\preprod\lss_preprod.db`** |
| Sauvegardes | `C:\LSS\preprod\sauvegardes\lss_preprod_<horodatage>.db` |
| Journal JSON de la recette | `C:\LSS\preprod\recette_preprod_<horodatage>.json` |
| Journal console (Tee) | `C:\LSS\preprod\recette_console_<horodatage>.log` |
| Empreintes consignées | `C:\LSS\preprod\empreintes_avant.json` / `empreintes_apres.json` |
| Dossier de travail imposé | `C:\tmp` (les deux suites y écrivent leurs copies) |

**Variables non secrètes** : `APP_ENV=STAGING`, `APP_TZ=Indian/Antananarivo`,
`SIM_ENABLE=0`, `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`,
`TEMP=TMP=TMPDIR=C:\tmp` ; et **`DATABASE_URL` supprimée**.

> **Aucun identifiant n'est nécessaire** : le ping est anonyme, l'E2E travaille sur
> des données déjà en base. Ne saisissez, n'affichez et ne journalisez jamais
> `MZONEX_*`, `CAMTRACKPRO_*`, `WIALON_*`, `JWT_SECRET` — et **n'exécutez aucune
> commande du type `Get-ChildItem Env:`** filtrant ces noms.

---

## 2. Préparation (avant tout essai)

```powershell
Set-Location 'C:\LSS\preprod\tracking-operationnel-lss'

# commit exigé
git fetch origin
git checkout --detach 618c4aed6bd63138109636f48c1623400175988f
git rev-parse HEAD          # attendu : 618c4aed6bd63138109636f48c1623400175988f
git status --porcelain      # attendu : AUCUNE ligne

# environnement
New-Item -ItemType Directory -Force -Path 'C:\tmp' | Out-Null
$env:TMPDIR = 'C:\tmp'; $env:TEMP = 'C:\tmp'; $env:TMP = 'C:\tmp'
$env:APP_ENV = 'STAGING'; $env:APP_TZ = 'Indian/Antananarivo'
$env:SIM_ENABLE = '0'; $env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue

# même Python que le collecteur (celui de demarrer.bat) + dépendances
(Get-Command python).Source
python --version
python -c "import httpx, sys; print('httpx', httpx.__version__, '| python', sys.version.split()[0], '|', sys.executable)"
```

Attendu : `python` = **celui du collecteur**, ≥ 3.12, `httpx` présent.
(`httpx` est importé au niveau module par `app/api_mzonex.py:30`,
`app/api_wialon.py:41`, `app/oauth_mzonex.py:32`, mais **absent de
`backend/requirements.txt`** : s'il manque → `python -m pip install httpx`,
notez-le dans le compte rendu.)

---

## 3. Sauvegarde

```powershell
# a) arrêter le collecteur PRÉPROD : Ctrl+C dans la fenêtre noire de demarrer.bat, puis
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue   # attendu : rien
$base = 'C:\LSS\preprod\tracking-operationnel-lss\backend\data\lss.db'

# b) sauvegarde cohérente (API sqlite3 .backup : valable collecteur allumé ou arrêté)
New-Item -ItemType Directory -Force -Path 'C:\LSS\preprod\sauvegardes' | Out-Null
$sauv = 'C:\LSS\preprod\sauvegardes\lss_preprod_' + (Get-Date -Format 'yyyyMMdd_HHmm') + '.db'
python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close(); print('sauvegarde OK')" "$base" "$sauv"
python -c "import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); print('integrite :', c.execute('PRAGMA integrity_check').fetchone()[0])" "$sauv"
(Get-FileHash -Algorithm SHA256 -Path $sauv).Hash
```

Attendu : `sauvegarde OK`, `integrite : ok`, une empreinte de 64 caractères.

---

## 4. Copie remise à la recette + empreintes AVANT

```powershell
Copy-Item -Path $base -Destination 'C:\LSS\preprod\lss_preprod.db' -Force
$p = 'C:\LSS\preprod\lss_preprod.db'
$avant = @{}
foreach ($f in @($p, "$p-wal", "$p-shm")) {
    if (Test-Path $f) { $avant[$f] = (Get-FileHash -Algorithm SHA256 -Path $f).Hash }
}
$avant.GetEnumerator() | Sort-Object Name | ForEach-Object { '{0}  {1}' -f $_.Value, $_.Name }
$avant.GetEnumerator() | Sort-Object Name | ConvertTo-Json | Set-Content -Encoding UTF8 'C:\LSS\preprod\empreintes_avant.json'
Get-Item $p | Select-Object Length, LastWriteTime
```

---

## 5. Lancement de la recette

Collecteur **toujours arrêté**, copie §4 en place :

```powershell
Set-Location 'C:\LSS\preprod\tracking-operationnel-lss\backend'
$stamp   = Get-Date -Format 'yyyyMMdd_HHmm'
$journal = "C:\LSS\preprod\recette_preprod_$stamp.json"
$console = "C:\LSS\preprod\recette_console_$stamp.log"

python recette_preprod_v154.py --base-preprod 'C:\LSS\preprod\lss_preprod.db' --sortie "$journal" 2>&1 |
    Tee-Object -FilePath $console
"code de sortie = $LASTEXITCODE"
```

Étapes attendues : `ouverture` → `ping_portails` → `e2e_reel_v113` → `verdict_final`.
*(`2>&1` ne concerne que votre log console : le pilote garde `stdout`/`stderr`
séparés en interne.)*

**Fichiers JSON produits**

| Fichier | Contenu |
|---|---|
| `recette_preprod_<horodatage>.json` | `recette`, `horodatage`, **`commit`**, `environnement {plateforme, python, app_env}`, `verdict`, `resultats {ping, e2e}` (verdict, code, mesures, `base_intacte`, `empreinte`), `journal[]` horodaté étape par étape |
| `empreintes_avant.json` / `empreintes_apres.json` | vos empreintes SHA-256 (fichier principal + `-wal` + `-shm`) |

Le pilote est le **seul** à écrire (ce JSON) ; il ne crée aucun autre fichier hors
dossier temporaire.

---

## 6. Contrôle d'intégrité APRÈS

```powershell
$apres = @{}
foreach ($f in @($p, "$p-wal", "$p-shm")) {
    if (Test-Path $f) { $apres[$f] = (Get-FileHash -Algorithm SHA256 -Path $f).Hash }
}
$apres.GetEnumerator() | Sort-Object Name | ConvertTo-Json | Set-Content -Encoding UTF8 'C:\LSS\preprod\empreintes_apres.json'
"fichier principal identique : " + ($apres[$p] -eq $avant[$p])
Get-Item $p | Select-Object Length, LastWriteTime     # doit être IDENTIQUE à §4
```

Le pilote ne hache que le **fichier principal** (`recette_preprod_v154.py` l.144 et
l.155) : ce contrôle ajoute les fichiers `-wal`/`-shm`, que le pilote ne couvre pas.

---

## 7. Rollback (si l'empreinte a changé, ou sur décision)

```powershell
# 0) collecteur préprod ARRÊTÉ (Ctrl+C)
# 1) mise à l'écart — JAMAIS de suppression (la preuve est conservée)
New-Item -ItemType Directory -Force -Path 'C:\LSS\preprod\incident' | Out-Null
Move-Item 'C:\LSS\preprod\lss_preprod.db' ('C:\LSS\preprod\incident\lss_preprod_' + (Get-Date -Format 'yyyyMMdd_HHmm') + '.db')
# 2) restauration depuis la sauvegarde de §3
Copy-Item 'C:\LSS\preprod\sauvegardes\lss_preprod_<AAAAMMJJ_HHMM>.db' 'C:\LSS\preprod\lss_preprod.db' -Force
(Get-FileHash -Algorithm SHA256 -Path 'C:\LSS\preprod\lss_preprod.db').Hash   # = empreinte de §4
# 3) relance du collecteur préprod : double-clic sur demarrer.bat
# 4) transmettre : journal JSON + empreintes + fichier d'incident (aucun secret)
```

Si la base de travail restaurée doit revenir à sa place d'origine, répétez la même
séquence avec `$base` (§3). **La production n'a aucune étape de rollback : la
recette ne la touche jamais.**

---

## 8. Les dix vérifications exigées

| # | Ce qui le prouve | Commande | Attendu |
|---|---|---|---|
| 1 | Base = copie de préprod | `Get-Item 'C:\LSS\preprod\lss_preprod.db'` + ligne `ouverture` du JSON | `resume=base=lss_preprod.db (nnnn octets)` ; le chemin passé contient `preprod` et n'est **pas** `backend\data\lss.db` |
| 2 | Production jamais modifiée | `Select-String -Path $journal -Pattern 'REFUS_SECURITE','production'` | **0 correspondance** ; garde-fou l.194 (refus si `APP_ENV=production`, code 2) ; aucun chemin de production dans la commande |
| 3 | Lecture seule MZoneX / CamtrackPro / Ym@ne | `Select-String -Path backend\test_mzonex_ping.py,backend\test_e2e_reel_v113.py -Pattern '\.post\(|\.put\(|password|token='` | **0 correspondance** : uniquement `httpx.Client(...)` / `client.get(...)` (ping l.24-25) ; la suite E2E ne fait **aucun** appel réseau |
| 4 | Commit = `618c4ae` | `git rev-parse HEAD` puis `Select-String -Path $journal -Pattern '618c4aed6bd63138109636f48c1623400175988f'` | même SHA en console et dans `commit` du JSON ; `git status --porcelain` vide **avant** l'exécution |
| 5 | Mode STAGING | `Select-String -Path $journal -Pattern '"app_env"'` | `"app_env": "STAGING"` — étiquette **tracée** (voir §10 : le produit ne lit pas `APP_ENV`) |
| 6 | Identifiants jamais affichés | `Select-String -Path $journal,$console -Pattern 'MZONEX_USER|MZONEX_PASSWORD|CAMTRACKPRO_USER|CAMTRACKPRO_PASSWORD|WIALON_TOKEN|JWT_SECRET'` | **0 correspondance** : le pilote n'imprime aucune valeur d'environnement et filtre les lignes suspectes (l.98-107) |
| 7 | Logs sans secret | idem 6 **plus** `-Pattern 'password','token=','bearer '` sur `$journal` et `$console` | **0 correspondance** ; sinon : **ne transmettez pas le log**, signalez-le |
| 8 | SHA-256 avant/après | `empreinte_avant`/`empreinte_apres` du JSON **et** §6 | identiques, `base_intacte = true`, `LastWriteTime` inchangé |
| 9 | E2E n'écrit aucune donnée (dans la préprod) | §6 + `Get-ChildItem C:\tmp -Filter 'e2e_v113.db'` | écritures **exclusivement** dans `C:\tmp\e2e_v113.db` et `C:\tmp\recette_preprod_*\data\lss.db` ; base préprod intacte |
| 10 | Ping depuis l'environnement du collecteur | `(Get-Command python).Source` **vs** `environnement.python` du JSON | même interpréteur que `demarrer.bat` (Python système), même poste, même proxy/pare-feu |

---

## 9. Critères PASS / FAIL

| Porte | PASS | FAIL |
|---|---|---|
| **P0 Pré-requis** | commit `618c4ae…`, `git status` vide, base nommée `*preprod*`, `C:\tmp` créé, `httpx` importable, sauvegarde et empreintes consignées | **STOP** — ne lancez pas la recette |
| **P1 Ping** | `verdict=PORTAILS_JOIGNABLES` (**6/6**) | `PARTIEL` / `AUCUN_PORTAIL_JOIGNABLE` → relever la liste des injoignables (réseau/proxy, pas le produit) |
| **P2 E2E** | `verdict=CONFORME`, `RÉSULTAT E2E RÉEL : N OK / 0 KO` | `NON_PROBANT` (suite sautée = preuve nulle) ou `ECART` (code ≠ 0 ou KO > 0) |
| **P3 Intégrité** | `base_intacte = true` **et** empreinte principale identique **et** `LastWriteTime` inchangé | empreinte différente → **anomalie grave** : rollback §7, conserver les fichiers, arrêter |
| **P4 Verdict global** | `verdict_final=RECETTE_CONFORME`, **code de sortie 0** | code 1 = écart ; **code 2 = refus de sécurité** → corriger le pré-requis et relancer, **ne jamais contourner** |
| **Après PASS** | transmettre `recette_preprod_<horodatage>.json` + `empreintes_*.json` + log console (scannés) | le rapport `RAPPORT_GO_NO_GO_v154.md` est mis à jour avec le journal cité ; **le NO-GO n'est levé qu'après** |

---

## 10. Limites et pièges connus (à lire avant de lancer)

1. **`/tmp` n'existe pas par défaut sous Windows.** La suite E2E écrit en dur
   `/tmp/e2e_v113.db` (`test_e2e_reel_v113.py` l.9 et l.15) ; Windows résout ce
   chemin relatif au **lecteur courant** → `C:\tmp\e2e_v113.db`. D'où §2 :
   création de `C:\tmp` et `TEMP/TMP/TMPDIR` sur `C:`. Sans cela : faux « ÉCART ».
2. **`APP_ENV` n'est lu nulle part dans le produit** (`grep -rn APP_ENV backend/app/`
   → 0 occurrence ; `docker-compose.yml` ne le définit pas non plus). `STAGING` est
   une **étiquette déclarative** tracée dans le journal et le garde-fou du pilote.
   Les protections réelles sont : base **copiée**, collecteur **arrêté**, 6 GET
   anonymes, empreintes avant/après.
3. **Le garde-fou de nom a une faille de chemin** : `recette_preprod_v154.py`
   l.199-203 refuse un fichier sans « preprod »… **mais** accepte tout fichier dont
   le *chemin complet* contient « preprod ». Sur un poste nommé `…\preprod\…`,
   donner `backend\data\lss.db` **passerait**. Ne vous fiez pas à ce garde-fou :
   utilisez la **copie** de §4 et vérifiez les empreintes.
4. **`httpx` absent de `backend/requirements.txt`** alors qu'il est importé au
   niveau module par trois modules produit (`api_mzonex.py:30`, `api_wialon.py:41`,
   `oauth_mzonex.py:32`) — la ligne l.16-20 du fichier affirme même « plus aucun
   paquet réseau à installer ». Sur le poste du collecteur il est présent ; §2 le
   vérifie.
5. **Base PostgreSQL en préproduction** → §0.1 : la recette E2E n'est pas outillée
   pour ce mode ; ne lancez rien avant l'export SQLite testé.
6. **Le pilote n'hache que le fichier principal** : complétez par §6 (`-wal`/`-shm`).
7. **Un `-wal`/`-shm` qui apparaît ou disparaît n'est pas une modification de
   données** (checkpoint SQLite) ; la référence décisive reste l'empreinte du
   fichier principal et `base_intacte` du journal.

---

## 11. Ce que cette procédure ne fait pas

Aucune écriture produit, aucune collecte réelle, aucun déploiement, aucune
modification de `main`, de `SPEC_RULES_v3.md`, ni d'une base de production.
Le seul fichier écrit par la recette est son **journal JSON** ; les seules bases
écrites sont les **copies** de travail sous `C:\tmp`.

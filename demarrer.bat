@echo off
REM ============================================================
REM Plateforme de Tracking Operationnel LSS - Lancement Windows
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

REM P0 — un seul serveur autorisé sur le port 8000. Arrêter l'ancien
REM processus explicitement avant de relancer ce script.
echo ==^> [0/4] Verification du port 8000...
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if ($c) { Write-Host ('ERREUR : port 8000 deja utilise par PID ' + $c[0].OwningProcess); exit 2 }"
if errorlevel 1 (
    echo Arretez le serveur existant avant de relancer demarrer.bat.
    exit /b 2
)

echo ==^> [1/4] Verification de Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERREUR : Python n'est pas installe ou pas dans le PATH.
    echo 1. Telechargez Python 3.12+ sur https://www.python.org/downloads/
    echo 2. Pendant l'installation, COCHEZ la case "Add python.exe to PATH"
    echo 3. Relancez ce script.
    echo.
    pause
    exit /b 1
)
python --version

echo ==^> [2/4] Dependances backend (pip install)...
python -m pip install -q -r backend\requirements.txt
if errorlevel 1 (
    echo ERREUR pendant l'installation des dependances Python.
    pause
    exit /b 1
)

REM v1.18.1 — navigateur de collecte reelle (Playwright/Chromium) :
REM present une fois pour toutes ; telecharge automatiquement si absent.
if not exist "%USERPROFILE%\.cache\ms-playwright" (
    echo    Premiere installation : telechargement du navigateur de collecte...
    python -m playwright install chromium
)

if not exist frontend\dist (
    echo ==^> [3/4] Compilation du frontend React (premiere fois seulement)...
    pushd frontend
    call npm install --no-audit --no-fund
    if errorlevel 1 (
        echo ERREUR npm install. Verifiez que Node.js est installe : node --version
        pause
        exit /b 1
    )
    call npm run build
    if errorlevel 1 (
        echo ERREUR pendant la compilation du frontend.
        pause
        exit /b 1
    )
    popd
) else (
    echo ==^> [3/4] Frontend deja compile (frontend\dist present), etape ignoree.
)

echo ==^> [4/4] Demarrage du serveur...
echo.
echo    -----------------------------------------------
echo     Plateforme :   http://localhost:8000
echo     API (Swagger): http://localhost:8000/docs
echo    -----------------------------------------------
echo.
echo     Comptes de demonstration :
echo       admin / Admin@2026          (Administrateur)
echo       tracking / Tracking@2026    (Responsable Tracking)
echo       consultation / Consult@2026 (Lecture seule)
echo.
echo     Laissez cette fenetre ouverte tant que vous utilisez
echo     la plateforme. Ctrl+C pour arreter le serveur.
echo.
echo     A VERIFIER AU DEMARRAGE (fenetre ci-dessous) :
echo       - «  MODE DONNEES REELLES  ... collecteur MIXTE  » = OK
echo       - «  MODE DEMONSTRATION (simulateur) » = donnees FICTIVES
echo         ^(backend\.env absent ou SIM_ENABLE=1^)
echo.
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause

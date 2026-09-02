#!/usr/bin/env bash
# ============================================================
# Plateforme de Tracking Opérationnel LSS — lancement complet
# ============================================================
set -e
cd "$(dirname "$0")"

echo "==> [1/3] Dépendances backend (Python)…"
pip install -q -r backend/requirements.txt

if [ ! -d frontend/dist ]; then
  echo "==> [2/3] Compilation du frontend React…"
  (cd frontend && npm install --no-audit --no-fund && npm run build)
else
  echo "==> [2/3] Frontend déjà compilé (frontend/dist présent)."
fi

echo "==> [3/3] Démarrage du serveur…"
echo ""
echo "   ➜ Plateforme :  http://localhost:8000"
echo "   ➜ API (Swagger): http://localhost:8000/docs"
echo ""
echo "   Comptes démo :"
echo "     admin / Admin@2026          (Administrateur)"
echo "     tracking / Tracking@2026    (Responsable Tracking)"
echo "     consultation / Consult@2026 (Lecture seule)"
echo ""
cd backend
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

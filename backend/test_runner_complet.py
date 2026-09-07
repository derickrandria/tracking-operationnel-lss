"""
Test Runner Unifié — Validation Globale de Non-Régression LSS Tracking.
Exécute l'ensemble des suites de tests critiques (Missions, Temps de Conduite, Dédoublonnage, etc.)
et produit un rapport synthétique d'intégrité opérationnelle.
Compatible Windows (PowerShell / CMD), Linux et macOS (encodage UTF-8 universel).
"""
import os
import sys
import time
import tempfile
import subprocess

# Configuration universelle de l'encodage UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SUITES_CRITIQUES = [
    ("Missions & Cycles Logistiques (v2026.1)", "backend/test_missions_v2026.py"),
    ("Missions & Règles Logistiques v2026 (1 à 9)", "backend/test_missions_v2026_complet.py"),
    ("Temps de Conduite & TCH Glissant", "backend/test_temps_conduite.py"),
    ("Dédoublonnage & Rapprochement Chauffeurs", "backend/test_conducteurs_fusion.py"),
    ("Conduite & Seuils Réglementaires", "backend/test_conduite_v127.py"),
]

def executer_suite(nom: str, fichier: str) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    env = os.environ.copy()
    
    # Préservation de sys.path (site-packages, venv, user base) + ajout de backend
    cur_pypath = env.get("PYTHONPATH", "")
    pypaths = ["backend"]
    if cur_pypath:
        pypaths.append(cur_pypath)
    env["PYTHONPATH"] = os.pathsep.join(pypaths)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["TESTING"] = "1"
    env["SIM_ENABLE"] = "0"
    env["COLLECTOR_SOURCE"] = "AUCUN"
    env["MZONEX_API_ENABLE"] = "0"
    env["WIALON_ENABLE"] = "0"
    env["YMANE_ACTIVE"] = "0"
    
    # Chemin DB temporaire portable Windows / Linux
    db_file = os.path.join(tempfile.gettempdir(), f"test_runner_{int(time.time()*1000)}.db").replace("\\", "/")
    env["DATABASE_URL"] = f"sqlite:///{db_file}"
    
    cmd = [sys.executable, fichier]
    proc = subprocess.run(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    duree = time.perf_counter() - t0
    
    # Nettoyage DB temporaire
    try:
        if os.path.exists(db_file):
            os.remove(db_file)
    except Exception:
        pass
        
    succes = (proc.returncode == 0)
    output = proc.stdout + "\n" + proc.stderr
    return succes, duree, output

def main():
    print("=" * 70)
    print("  LSS TRACKING — SUITE GLOBALE DE VALIDATION & NON-RÉGRESSION")
    print("=" * 70)
    
    total = len(SUITES_CRITIQUES)
    succes_total = 0
    echecs = []
    
    for idx, (nom, fichier) in enumerate(SUITES_CRITIQUES, 1):
        sys.stdout.write(f"[{idx}/{total}] Exécution de {nom} ... ")
        sys.stdout.flush()
        
        reussi, duree, sortie = executer_suite(nom, fichier)
        if reussi:
            print(f"✅ SUCCÈS ({duree:.2f}s)")
            succes_total += 1
        else:
            print(f"❌ ÉCHEC ({duree:.2f}s)")
            echecs.append((nom, fichier, sortie))
            
    print("\n" + "-" * 70)
    print(f"RÉSULTAT GLOBAL : {succes_total}/{total} suites réussies avec succès.")
    print("-" * 70)
    
    if echecs:
        print("\nDÉTAILS DES ÉCHECS :")
        for nom, fichier, sortie in echecs:
            print(f"\n--- {nom} ({fichier}) ---")
            print(sortie[-500:])
        sys.exit(1)
    else:
        print("\n🎉 INTÉGRITÉ OPÉRATIONNELLE PARFAITE : 100% DES TESTS PASSENT SANS ERREUR.")
        sys.exit(0)

if __name__ == "__main__":
    main()

"""
Test Runner Unifié — Validation Globale de Non-Régression LSS Tracking.
Exécute l'ensemble des suites de tests critiques (Missions, Temps de Conduite, Dédoublonnage, etc.)
et produit un rapport synthétique d'intégrité opérationnelle.
"""
import os
import sys
import time
import subprocess

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
    env["PYTHONPATH"] = "backend"
    env["DATABASE_URL"] = f"sqlite:////tmp/test_runner_{int(time.time()*1000)}.db"
    
    cmd = [sys.executable, fichier]
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    duree = time.perf_counter() - t0
    
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

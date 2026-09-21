#!/usr/bin/env python3
"""Script de test de connectivité directe (Ping HTTP) pour MZoneX et CamTrackPro."""

import sys
import os
import time
import httpx

ENDPOINTS = [
    ("MZoneX SSO (IdentityServer4)", "https://login.mzoneweb.net/connect/authorize"),
    ("MZoneX API OData (Root)", "https://live.mzoneweb.net/mzone62.api"),
    ("MZoneX Web Portal", "https://live.mzoneweb.net/mzonex/"),
    ("CamTrackPro Wialon API", "https://hst-api.wialon.com/wialon/ajax.html?svc=core/get_version&params={}"),
    ("CamTrackPro Web Portal", "https://hosting.camtrack.net/?lang=fr"),
    ("CamTrackPro BI (Ym@ne)", "https://bi.camtrack.pro/login/"),
]

def ping_endpoint(nom: str, url: str, timeout_s: float = 10.0):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    }
    debut = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5.0), follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            duree = round(time.monotonic() - debut, 3)
            return {
                "nom": nom,
                "url": url,
                "statut": f"HTTP {resp.status_code}",
                "code": resp.status_code,
                "duree_s": duree,
                "succes": resp.status_code < 500,
                "erreur": None
            }
    except httpx.TimeoutException as e:
        duree = round(time.monotonic() - debut, 3)
        return {
            "nom": nom,
            "url": url,
            "statut": "TIMEOUT",
            "code": None,
            "duree_s": duree,
            "succes": False,
            "erreur": f"Timeout après {duree}s ({e})"
        }
    except Exception as e:
        duree = round(time.monotonic() - debut, 3)
        return {
            "nom": nom,
            "url": url,
            "statut": "ERREUR",
            "code": None,
            "duree_s": duree,
            "succes": False,
            "erreur": f"{type(e).__name__}: {e}"
        }

def main():
    print("=" * 75)
    print("  TEST DE CONNECTIVITÉ DIRECTE & PING RÉSEAU (MZoneX / CamTrackPro)")
    print("=" * 75)
    
    for nom, url in ENDPOINTS:
        res = ping_endpoint(nom, url, timeout_s=10.0)
        statut_badge = "✅" if res["succes"] else "❌"
        print(f"\n{statut_badge} [{res['nom']}]")
        print(f"   URL      : {res['url']}")
        print(f"   Réponse  : {res['statut']} (en {res['duree_s']}s)")
        if res["erreur"]:
            print(f"   Détail   : {res['erreur']}")

    print("\n" + "=" * 75)

if __name__ == "__main__":
    main()

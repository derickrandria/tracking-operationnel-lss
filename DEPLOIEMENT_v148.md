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

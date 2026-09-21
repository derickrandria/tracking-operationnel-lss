# Recette de PRÉPRODUCTION — v1.54 (21/09/2026)

**Objet** : exécuter les **deux suites qui ne peuvent rien prouver dans un bac à
sable** (`test_e2e_reel_v113`, `test_mzonex_ping`), en **lecture seule**, sur une
base de **préproduction**, avec des **logs vérifiables**.

**Pilote** : `backend/recette_preprod_v154.py`
**Journal produit** : `docs/audits/recette_preprod_<AAAAmmJJ_HHMM>.json` (JSON
horodaté, rejouable, sans aucun secret).

---

## 1. Pourquoi ces deux suites ne sont pas dans la campagne

| Suite | Ce qu'elle prouve | Pourquoi elle ne prouve rien ici |
|---|---|---|
| `test_mzonex_ping.py` | Les six points d'entrée des portails répondent (SSO, API OData, portails, Wialon, BI Ym@ne) | Le bac à sable n'a **aucun accès réseau** : les six réponses sont « hôte injoignable ». L'absence de réseau ne dit rien de la disponibilité réelle |
| `test_e2e_reel_v113.py` | La guérison v1.13 rejouée sur de **vrais trajets** du 04/08 (jumeaux orange/noir, noir prématuré, fausse pause) | Elle exige une **base bac à sable peuplée** (`data/lss.db`) qui n'existe pas ici : la suite **saute** (code 0 sans verdict) |

## 2. Prérequis (préproduction)

1. Une **copie** de la base de préproduction, nommée `*preprod*.db` (ou un
   fichier `.dump` PostgreSQL). **Jamais** la base de production.
2. Accès réseau sortant vers : `login.mzoneweb.net`, `live.mzoneweb.net`,
   `hst-api.wialon.com`, `hosting.camtrack.net`, `bi.camtrack.pro`.
3. `APP_ENV` **≠** `production` (le pilote refuse explicitement).
4. Python 3.11+ et les dépendances backend (`pip install -r backend/requirements.txt`).
5. **Aucun secret à fournir au pilote** : les jetons des portails ne sont pas
   nécessaires (le ping est anonyme, l'E2E rejoue des données déjà en base) ;
   le pilote n'affiche ni ne journalise de valeur issue de `.env`.

## 3. Exécution

```bash
cd backend

# Recette complète (défaut : journal dans docs/audits/)
python3 recette_preprod_v154.py \
    --base-preprod /srv/preprod/lss_preprod.db

# Variantes
python3 recette_preprod_v154.py --base-preprod … --sauter-ping     # réseau déjà prouvé
python3 recette_preprod_v154.py --base-preprod … --sortie /tmp/recette.json
```

Codes de sortie : `0` = recette conforme · `1` = écart · `2` = **refus de
sécurité** (base suspecte ou `APP_ENV=production`).

## 4. Ce que le pilote fait, étape par étape

| # | Étape | Action | Preuve produite |
|---|---|---|---|
| 0 | `ouverture` | Enregistre commit Git, version Python, `APP_ENV`, taille de la base | Ligne horodatée + empreinte SHA-256 de la base |
| 1 | `ping_portails` | Lance `test_mzonex_ping.py` (simulateur navigateur, 10 s) et compte les points d'entrée joignables | Verdict `portails_joignables` / `partiel` / `aucun`, liste des injoignables |
| 2 | `e2e_reel_v113` | **Copie** la base de préproduction dans un dossier temporaire (`data/lss.db`), lance la suite avec `cwd` = dossier temporaire et `PYTHONPATH` = `backend/`, puis **recalcule l'empreinte de la base source** | Verdict `conforme` / `écart` / `non_probant`, code de sortie, contrôles ✅/❌, `base_intacte = vrai` |
| 3 | `verdict_final` | Combine les deux étapes | `RECETTE_CONFORME` si portails joignables **et** E2E conforme **et** empreinte inchangée |

### Garanties de lecture seule (démontrées, pas déclarées)

- la base de préproduction n'est **jamais ouverte en écriture** : elle est
  copiée, la suite travaille sur la copie (`/tmp/e2e_v113.db`) ;
- l'empreinte SHA-256 est prise **avant et après** ; toute différence fait
  basculer le verdict en `ECART` (`base_intacte = false`) ;
- le pilote **refuse** de démarrer si `APP_ENV=production` ou si le fichier
  fourni ne s'appelle pas comme une base de préproduction ;
- les sorties sont filtrées avant journalisation (mots-clés `password`,
  `token=`, `bearer`, `mzonex_user`…) : **aucun secret dans les logs**.

## 5. Lecture du journal (exemple réel, essai à blanc du 21/09/2026)

Essai exécuté dans le bac à sable avec une base de démonstration
(le réseau y est volontairement absent) :

```json
[
 {"horodatage": "2026-09-21T12:56:19", "etape": "ouverture", "verdict": "OK"},
 {"horodatage": "2026-09-21T12:56:20", "etape": "ping_portails",
  "verdict": "AUCUN_PORTAIL_JOIGNABLE", "resume": "0 joignable(s), 6 injoignable(s)"},
 {"horodatage": "2026-09-21T12:56:21", "etape": "e2e_reel_v113",
  "verdict": "CONFORME", "code": 0, "empreinte_avant": "0362ba85edd4aedc",
  "empreinte_apres": "0362ba85edd4aedc", "resume": "RÉSULTAT E2E RÉEL : 16 OK / 0 KO"},
 {"horodatage": "2026-09-21T12:56:21", "etape": "verdict_final",
  "verdict": "RECETTE_NON_CONFORME",
  "resume": "ping=AUCUN_PORTAIL_JOIGNABLE · e2e=CONFORME · base intacte=True"}
]
```

Interprétation : le **mécanisme** de recette est prouvé (E2E conforme 16 OK/0 KO,
base source inchangée — empreintes identiques) ; seul le **réseau manque ici**.
En préproduction, `ping_portails` doit rendre `PORTAILS_JOIGNABLES` pour que le
verdict global passe à `RECETTE_CONFORME`.

## 6. Critères d'acceptation (à joindre au GO/NO-GO)

1. `ping_portails` : **6/6** points d'entrée joignables (ou liste d'injoignables
   motivée et datée par l'exploitant réseau) ;
2. `e2e_reel_v113` : `RÉSULTAT E2E RÉEL` avec **0 KO** ;
3. `base_intacte = vrai` (empreintes identiques) ;
4. `verdict_final = RECETTE_CONFORME`, code de sortie `0` ;
5. journal JSON conservé dans `docs/audits/` et **cité dans le rapport GO/NO-GO**
   (horodatage + commit).

## 7. En cas d'écart

| Symptôme | Lecture | Action |
|---|---|---|
| Étapes 1 en `aucun` | Réseau/proxy de préproduction, pas le produit | Faire vérifier l'accès sortant, relancer |
| Étapes 1 en `partiel` | Un portail est tombé | Consigner la liste, relancer après rétablissement |
| Étape 2 en `écart` | Le produit ne rejoue pas la guérison v1.13 sur la base de préproduction | Collecter le journal, ouvrir un correctif **avec** la base de préproduction comme preuve |
| `base_intacte = false` | **Anomalie grave** : la recette a écrit dans la base source | Arrêter, restaurer depuis la sauvegarde, analyser (aucune donnée de production n'est concernée) |
| Refus de sécurité (code 2) | Garde-fou déclenché | Vérifier `APP_ENV` et le nom du fichier — ne jamais contourner |

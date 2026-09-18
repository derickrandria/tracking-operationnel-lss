# « database is locked » — MZoneX n'était pas en panne, c'était nous

**Correctif v1.50** — établi le 18/09/2026 à partir de votre relevé `/api/sante`
(10:08, MZoneX rétabli). Répond au constat :

> statut « COLLECTE_DEGRADEE »
> sources_en_echec : MZONEX, MZONEX_RELECTURE
> MZONEX → `OperationalError: (sqlite3.OperationalError) database is locked`
> `[SQL: INSERT INTO collecte_checkpoints ...]`
> MZONEX_RELECTURE → `TimeoutError: Timeout dur de 30.0s dépassé`

---

## 1. Ce que ce relevé dit — et ce qu'il ne dit pas

Trois bonnes nouvelles d'abord, visibles dans VOS chiffres :

| Ce que vous avez envoyé | Ce que ça prouve |
|---|---|
| `wialon_token_present: true`, `dernier_nombre: 5`, `derniere_reussite: 10:08:26` | CamtrackPro collecte normalement |
| `dernier_evenement_gps: 10:08:03`, `retard_collecte_min: 0.6` | la base **se remplit en temps réel** |
| `sources_en_echec`, `collecte_par_source` présents | votre service tourne bien avec le code v1.48+ (ces champs n'existaient pas avant) |

Et le point capital : **aucune erreur d'authentification, aucun HTTP 5xx, aucun
timeout réseau vers MZoneX.** La seule erreur du portail, c'est… notre propre
base :

```
OperationalError: (sqlite3.OperationalError) database is locked
[SQL: INSERT INTO collecte_checkpoints ...]
```

`collecte_checkpoints` est le **journal** des passes de collecte. La plateforme
déclarait donc « MZONEX en panne » parce qu'elle n'avait pas pu écrire sa propre
ligne de journal — sans même avoir interrogé MZoneX : le checkpoint s'écrit
**avant** l'appel au portail.

---

## 2. Les deux mécanismes, mesurés

### 2.1 Une transaction d'écriture trop longue

Tout un lot de collecte vivait dans **une seule transaction** :

```
mesuré au laboratoire : 10 000 points = 10,9 s de verrou d'écriture
extrapolé (arrêt MZoneX de 25 h ≈ 40 000 points) : ≈ 44 s
```

Or SQLite n'a **qu'un écrivain à la fois**, et notre `busy_timeout` est de
**30 s** : tout autre écrivain (le checkpoint de la passe N1 MZoneX, un PATCH de
l'écran, un audit) patiente, puis **échoue** avec exactement votre message.

La relecture rejouait le trou d'arrêt de 25 h (MZoneX rétabli la veille) : elle
repartait donc **à chaque cycle** sur la même grosse fenêtre jamais terminée —
les compteurs ne pouvaient jamais rattraper leur retard.

### 2.2 Un timeout qui laissait un orphelin

`_collecte_protegee` abandonnait l'attente au bout de 30 s, **mais ne tuait
rien** : le thread de relecture poursuivait son insertion — et tenait le verrou
d'écriture — pendant que le cycle suivant démarrait. Le `TimeoutError` de votre
relevé n'était donc pas la fin du travail, seulement la fin de la *patience*.

### 2.3 Le piège « lire puis écrire » (reproduit)

Pysqlite ouvre ses transactions en `BEGIN` différé : nos transactions
**lisent d'abord** (véhicules, clé d'idempotence, suivi) et ne demandent le
verrou d'écriture qu'ensuite. Si une autre connexion a écrit entre-temps, SQLite
refuse définitivement la montée en écriture et le pilote réessaie jusqu'à son
`timeout` (30 s) avant de rendre « database is locked ».

Reproduction au laboratoire, scénario de production (relecture 10 000 points +
une passe qui écrit son checkpoint) :

```
AVANT :  ✗ la RELECTURE mourait sur son propre UPDATE vehicules.user
         (pas sur le checkpoint — la victime et le coupable étaient confondus)
APRÈS :  ✓ 10 000 points insérés, aucun écrivain en échec
```

---

## 3. Les correctifs

| # | Défaut | Correctif |
|---|---|---|
| 1 | **Transactions géantes** (44 s de verrou) | `CollectorBase.inserer` committe **par lots** de `COLLECTE_LOT_INSERTION` (250) : le verrou n'est tenu que ~0,3 s. Un verrou résiduel fait **rejouer le lot** (idempotent) au lieu de perdre la passe |
| 2 | **Le journal faisait tomber la collecte** | `_checkpoint_ouvre/_checkpoint_ferme` ne lèvent plus jamais : l'échec est journalisé, la collecte continue. *Une trace ne commande pas la collecte* |
| 3 | **Orphelin après timeout** | la passe est laissée se terminer (bornée par `COLLECTE_ATTENTE_SORTIE_S`, 60 s) au lieu d'être abandonnée en tenant le verrou |
| 4 | **Relecture sans plafond** | budget par cycle : `RELECTURE_N1_MAX_POINTS` (4000) + son propre délai `RELECTURE_N1_TIMEOUT_S` (180 s). La reprise est naturelle : les fenêtres sont recalculées à chaque cycle depuis ce qui **manque réellement** en base |
| 5 | **Équité entre écrivains** | souffle `COLLECTE_SOUFFLE_S` (10 ms) entre les lots : le verrou n'est plus repris en continu. **Mesuré : blocage continu 3 810 ms → 270 ms** |
| 6 | **Diagnostic trompeur** | les erreurs sont **catégorisées** : `locale` (base verrouillée, disque, budget) vs `portail` (auth, HTTP, réseau). `/api/sante` expose la cause ; l'écran distingue les deux silences |

### Ce qui a été essayé puis **écarté** (et pourquoi)

`BEGIN IMMEDIATE` (prendre le verrou d'écriture dès le début de transaction) est
la réponse classique au piège 2.3. Mesuré ici : **7 suites de tests cassées**,
parce qu'avec `IMMEDIATE`, même une transaction de **lecture** prend le verrou :
deux sessions vivantes du même service (un lecteur de l'API + la collecte)
s'attendent alors jusqu'à l'expiration du `busy_timeout` et échouent sur
`BEGIN IMMEDIATE`. Le levier réel était le point 1 — des transactions courtes.
Le mode reste disponible (`LSS_SQLITE_IMMEDIATE=1`) mais **désactivé par
défaut**, et explicitement coupé pour les outils de contrôle en lecture seule
(`verifier_*.py`), dont les longues analyses ne doivent pas gêner la collecte.

---

## 4. Vérification

| Contrôle | Résultat |
|---|---|
| `test_verrous_sqlite_v150.py` (nouvelle suite) | **18 OK / 0 KO** |
| 6 000 points + écrivain concurrent | aucun échec, plage bloquée maximale **270 ms** |
| Progrès monotone (panne au 3ᵉ lot) | les 2 lots déjà committés sont **conservés** |
| `_checkpoint_ouvre` sous verrou | retourne `None`, **ne lève plus** |
| Catégorisation | l'erreur exacte de votre relevé → **`locale`** |
| Suites v1.48 / v1.49 (non-régression) | 14/0 et 13/0 |
| Campagne complète | **35 verts / 46 suites** (v145 revient à 47/1 après avoir rendu son assertion de version robuste) |
| v113 (9 KO) | **identique sans mes modifications** (vérifié en stashant) : préexistant, dépendant de l'horloge — pas une régression |

---

## 5. Ce que `/api/sante` dit maintenant

```json
{
  "statut": "COLLECTE_BLOQUEE_LOCALEMENT",
  "sources_en_echec": ["MZONEX", "MZONEX_RELECTURE"],
  "sources_en_echec_detail": [
    {"source": "MZONEX", "categorie": "locale",
     "erreur": "OperationalError: (sqlite3.OperationalError) database is locked…"}
  ],
  "sources_bloquees_localement": ["MZONEX"],
  "sources_portail_en_panne": [],
  "collecte_bloquee_localement": true,
  "sqlite": {"journal_mode": "wal", "busy_timeout": 30000, "synchronous": 1},
  "version": "1.50"
}
```

Le statut ne vous envoie plus chercher une panne chez MZoneX quand la cause est
chez nous. Et à l'écran, trois silences, trois messages :

| Cause | Badge |
|---|---|
| Portail en panne (auth/HTTP) | 🔴 « source MZONEX en panne — collecte interrompue » |
| Collecte bloquée localement (base verrouillée) | 🔴 « collecte MZONEX bloquée localement — base verrouillée » |
| Boîtier muet, portail sain | 🟠 « boîtier muet — données en transit » |

---

## 6. Ce qu'il vous reste à faire

1. **Redémarrer le service** après le `git pull` (le correctif n'agit qu'au
   redémarrage). Le bandeau latéral doit afficher **v1.50**.
2. Vérifier `/api/sante` : `sqlite.journal_mode` doit être **`wal`** et
   `busy_timeout` **30000**. Si `sqlite.erreur` apparaît, la base est sur un
   support qui refuse le WAL (partage réseau) — dites-le moi.
3. Laisser le rattrapage se faire **seul** : la relecture travaille par tranches
   de 4 000 points par cycle (≈ 1 h), sans jamais bloquer le direct. Le compteur
   `portail_muet` et les journées « À REPRENDRE » de `verifier_collecte.py`
   montrent la progression.

### Et pour ne plus jamais revoir ce message

SQLite est une base **mono-écrivain** : ce genre de contention est structurel.
La plateforme accepte déjà PostgreSQL (`DATABASE_URL=postgresql+psycopg2://…`) ;
quand vous voudrez passer à 39 boîtiers + plusieurs postes qui saisissent en
même temps, c'est le chemin — et il ne demande **aucune** modification de code.
En attendant, les réglages (`COLLECTE_LOT_INSERTION`, `COLLECTE_SOUFFLE_S`,
`RELECTURE_N1_MAX_POINTS`) permettent de régler le compromis
« rattrapage rapide ↔ écran réactif » sans redéployer.

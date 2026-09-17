# Les heures de départ fausses — d'où elles viennent, comment elles sont corrigées

**Correctif v1.49** — établi le 17/09/2026 à partir de vos captures (Suivi
Journalier + portail MZoneX côte à côte). Répond aux deux constats :

> « 0916TBV, 5316TBU n'ont pas bougé du tout aujourd'hui. »
> « Pour certains camions, la plateforme a pris de mauvaises heures de départ. »

---

## 1. Ce que disent vos captures, mises côte à côte

| Camion | Portail MZoneX (source) | Plateforme LSS (écran) |
|---|---|---|
| **2746TCC** | trajet **14:08:56 → 15:47:18** (1h38m, 34,348 km) | départ **15:32**, TCC 0:54, TCJ 1:00 |
| **4006TBS** | trajets **12:19:40**, 12:35:22, **12:58:19 → 16:25:33** | départ **08:43**, TCC 7:40, TCJ 7:47 |
| **0916TBV** | 4 événements moteur 06:20→06:31, **0 km/h, odomètre 496 591,0 figé** | départ **08:43**, TCC 0:02, TCJ 7:46 |

Aucune de ces trois heures de départ n'existe chez le portail. Ce n'étaient pas
des valeurs « approchées » : c'étaient **les heures de nos propres photos**.

---

## 2. Défaut n° 1 — une photo n'est pas un événement

`api_mzonex.dernieres_positions()` lit l'endpoint `Vehicles` : c'est un
**instantané d'état** (« dernière position connue », dernière vitesse publiée).
Le collecteur le fusionnait avec les vrais événements :

```python
# app/scrapers.py — MZoneXApiCollector.normaliser (AVANT v1.49)
points = [point_depuis_evenement_api(v) for v in brut]   # les ÉVÉNEMENTS
points.extend(self.api.dernieres_positions())            # + les PHOTOS
```

et `ingest_event` (§0ter) ouvre une ligne dès que la vitesse dépasse le seuil,
**en la datant de l'horodatage du point** :

```python
roule = vitesse and vitesse > seuil          # > 3 km/h
if roule:
    if not trajets:
        trajets = [_nouveau_trajet(suivi, 1, ts, ...)]   # ← ts = heure de la PHOTO
```

**Conséquence mesurée** (reproduction sur base réelle, trajet MZoneX
14:08:56 → 15:47:18, photo prise à 15:32 pendant le trajet) :

```
AVANT :  trajet n°1 : 15:32:00 → EN COURS
         ⇒ « Heure de départ » affichée = 15:32:00      (MZoneX : 14:08:56)
APRÈS :  aucune ligne créée par la photo
         ⇒ la ligne garde la date de l'événement réel : 14:08:56
```

L'écart n'est pas un arrondi : c'est **l'âge de la photo** (24 min sur
2746TCC, 7h50 sur 4006TBS).

### Correctif

- `api_mzonex.dernieres_positions()` marque ses points `observation: True` ;
- `engine.ingest_event(..., observation=True)` **sort avant la machine à
  états** : la photo met à jour la trace, `last_lat/lng`, `last_vitesse`,
  `last_event_at` (donc `gps_age_s`, le badge, la carte, `verifier_collecte`)
  et **n'ouvre, ne ferme, ne soude jamais une ligne**, ne crédite aucun
  kilomètre, ne déclenche aucune alerte.

C'est la doctrine déjà écrite pour les « blips de contact » (§0quinquies du
14/08 : « un Début du trajet seul ne prouve rien »), étendue à l'instantané :
**une photo prouve OÙ est le camion et QUAND le portail en a parlé — jamais
QUAND il est parti.**

---

## 3. Défaut n° 2 — une trace fabriquée « il y a 2 minutes »

`scrapers._synchroniser_dernier_point_mzonex()` est appelé **précisément quand
MZoneX tombe** (timeout du cycle N1, bouton « Sync GPS »). Sa branche « le
portail ne publie rien pour ce véhicule » faisait ceci :

```python
else:
    v.last_event_at = now - timedelta(minutes=2)     # ← horodatage INVENTÉ
    ...
    ev_refresh = EvenementGPS(horodatage=now - timedelta(minutes=2),
                              latitude=dernier_ev.latitude,   # position RECOPIÉE
                              vitesse=dernier_ev.vitesse or 0.0)  # vitesse RECOPIÉE
# commentaire d'origine : « afin d'actualiser le timestamp de communication
#                          et lever le repère boîtier muet »
```

Quatre conséquences, toutes visibles dans vos captures :

1. **Le repère « boîtier muet » ne pouvait jamais s'afficher** : un camion muet
   depuis 8 h était réécrit « il y a 2 minutes ». Le mensonge contredisait
   `/api/sante` — c'est le faux vert que vous avez signalé.
2. **Le bornage v1.48 était neutralisé** : la borne des compteurs est
   `dernière trace + 30 min` ; la « dernière trace » étant fabriquée à
   maintenant − 2 min, la borne tombait après l'instant de consultation → les
   compteurs couraient encore jusqu'à minuit.
3. **R2 rouvrait des lignes fantômes** — `engine.rattraper_ouvertures` ouvre une
   ligne quand « signal ≤ 15 min **et** vitesse > 3 km/h ». La fausse trace
   satisfaisait toujours la première condition, la vitesse recopiée la seconde :
   la ligne était alors **datée du dernier événement connu de la journée**
   (08:43, 15:32…). C'est **la** source des mauvaises heures de départ.
4. Ces événements, écrits **sans clé d'idempotence**, s'empilaient à chaque
   cycle dans la trace.

### Correctif

La branche ne fabrique plus rien : elle **compte** le silence
(`portail_muet`, `portail_muet_plaques`, `statut: "PARTIEL"`) et laisse
l'horodatage réel. Un portail muet se voit désormais au lieu d'être maquillé.

---

## 4. Vérification (rejouée à l'identique sur base neuve)

| Contrôle | Résultat |
|---|---|
| `test_heure_depart_v149.py` (nouvelle suite) | **13 OK / 0 KO** |
| Photographie 15:32 pendant un trajet → aucune ligne | ✅ |
| Photographie après un vrai départ → la ligne garde 14:08:56 | ✅ |
| Portail muet → aucun événement fabriqué, horodatage intact | ✅ |
| Signal périmé (08:43) → R2 ne rouvre **aucune** ligne | ✅ |
| Roulage réel et frais → R2 ouvre toujours, datée du vrai début (R2 intact) | ✅ |
| Campagne complète des suites | voir §5 du guide de déploiement |

---

## 5. Ce qui change à l'écran pour vous

- Les heures de départ ne peuvent plus être celles d'une photo : elles viennent
  d'un **événement** (Début du trajet) ou du **trajet officiel** MZoneX ;
- un camion qui n'a pas bougé **n'obtient plus** de ligne (0916TBV reste à
  0:00, conforme à un odomètre figé et 0 km/h) ;
- un portail muet est **dit** (badge « source MZONEX en panne — collecte
  interrompue », compteur `portail_muet`) au lieu d'être masqué par une trace
  inventée.

## 6. Ce qui reste à faire côté exploitation

1. **Redémarrer le service** (les correctifs v1.48 + v1.49 ne s'appliquent
   qu'au redémarrage) ;
2. **Rétablir MZoneX** (le SSO corrigé est dans le commit `52dc843`) ;
3. laisser passer **un cycle de relecture** : les événements du trou
   08:43 → rétablissement sont rejoués depuis le portail (idempotent), et les
   trajets officiels du jour arrivent par le Niveau 2 (toutes les 15 min).
   Les lignes fantômes créées *avant* le redémarrage restent visibles sur la
   journée en cours : elles disparaîtront d'elles-mêmes à la consolidation de
   la journée, ou par `POST /api/suivi/recalculer-archive`.

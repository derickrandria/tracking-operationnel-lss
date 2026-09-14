"""v1.13 — CHAÎNES DE LA JOURNÉE (contrat grille strict, retour métier 04/08/2026).

Constat métier (captures 04/08, 10h13-10h19) : malgré v1.11/v1.12, l'écran
montrait encore des lignes oranges « en cours » AVANT des lignes noires au
MÊME début (4526TCC, 5316TBU, 0916TBV), des heures noires sans pause ≥ 20 min
derrière (4526 : fin 08:21 alors que le camion est reparti à 08:25), et des
lignes officielles séparées par des « pauses » qui n'ont jamais existé
(9856TCD : des manœuvres de 2-8 min interrompaient l'arrêt sans que la
structure le reflète — « 30 min » fantaisiste entre 09:15 et 09:46).

Faits de terrain mesurés sur le portail le 04/08 :
- l'onglet Trajets MZoneX ne publie une ligne qu'une fois le trajet TERMINÉ
  (pas de ligne « en cours » fiable en temps utile) ;
- le portail découpe les trajets à chaque micro-arrêt (2-8 min), ce que la
  règle métier interdit : une pause < 20 min est IGNORÉE, les segments
  adjacents forment UNE ligne (Addendum v1.5 §1.2/§5).

Règles v3 (AMÉLIORATIONS du 21/08/2026, arbitrages LSS C1→C4 inscrits §0nonies
le 22/08/2026 — elles PRIMENT et supplantent l'affichage par chaînes v1.18) :

  SEGMENTS   tous les trajets du suivi ; les segments REJETÉS (manœuvres
             < 0,3 km) sont IGNORÉS : conservés en base pour l'audit (R2),
             JAMAIS affichés en colonne, JAMAIS comptabilisés (ni l'heure ni
             le trajet — leur span est un arrêt comme un autre, AM-6).
  LIGNE      **UN TRAJET VALIDE = UNE LIGNE** (AM-2 : fini la fusion
             d'affichage des pauses < 20 min — chaque trajet publié par le
             portail garde ses propres début/fin à l'écran).
             Une ligne OUVERTE (trajet commencé, fin encore inconnue) reste
             TOUJOURS affichée en orange « en cours » (§0quater/v1.16 non
             supplantés : on ne juge la manœuvre qu'à la clôture) ; une ligne
             clôturée dont la distance connue est < 0,3 km est cachée
             (manœuvre — règle absolue §2 préservée).
  ÉTAT       · EN_COURS   : le dernier segment n'a pas de fin — orange
                            immédiat (v1.16) ;
             · EN_ATTENTE : fin connue constatée depuis < 20 min → ORANGE,
                            fin PROVISOIRE (`DUREE_MIN_PAUSE_VALIDE`,
                            mécanisme de couleur NON supplanté par v3) ;
             · OFFICIELLE : fin connue ET arrêt constaté depuis ≥ 20 min →
                            NOIR (jour consolidé : toujours NOIR, CA-4).
  PAUSES     **affichées seulement si ≥ 30 min** (`SEUIL_PAUSE_COUPURE_TCC`,
             AM-2) — cellule vide sinon ; l'écart BRUT réel reste calculé
             (`gap_brut_s`) pour les compteurs. Écran = export = archive
             (§A.2) : le même filtrage partout.
  COMPTEURS  (AM-1 + règle T1, §7 v3) :
             · DÉPART  = début du PREMIER trajet valide (AM-6 : une manœuvre
               de début de journée ne fixe jamais le départ) ;
             · TTJ     = fin_ref − départ (amplitude brute, point final) ;
             · ARRÊTS  = tout intervalle entre deux lignes (toute durée,
               spans de manœuvres inclus) ;
             · TCJ     = TTJ − Σ arrêts = Σ durées des lignes (la conduite
               où le véhicule ROULE) ;
             · TCC     = session de lignes depuis la dernière pause ≥ 30 min
               (manœuvre ≥ 30 min = pause qui coupe, AM-6 ; le TCC traverse
               minuit avec le segment splitté, R1 — ancrage calculé dans
               `engine.recalculer_temps`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

ETAT_EN_COURS = "EN_COURS"
ETAT_EN_ATTENTE = "EN_ATTENTE"
ETAT_OFFICIEL = "OFFICIEL"


@dataclass
class Segment:
    debut: datetime
    fin: datetime | None
    distance_km: float | None
    rejete: bool
    ref: object | None = None          # enregistrement Trajet d'origine


@dataclass
class LigneJournee:
    """Une ligne de la grille (départ / fin / pause) — v3 AM-2 : un trajet
    valide publié = une ligne."""
    debut: datetime
    fin: datetime | None               # None = en cours
    distance_km: float | None          # distance du trajet (telle que publiée)
    etat: str                          # EN_COURS | EN_ATTENTE | OFFICIEL
    pause_apres_s: int = 0             # AFFICHÉE : ≥ 30 min sinon 0 (AM-2)
    gap_brut_s: int = 0                # écart RÉEL à la ligne suivante
                                       # (compteurs AM-1/TCC — jamais masqué)
    travail_s: int = 0                 # (fin|maintenant) − début (conduite pure)
    premiere_ref: object | None = None
    nb_segments: int = 1


@dataclass
class JourneeChainee:
    """Journée vue v3 (AMÉLIORATIONS — arbitrages C1→C4 du 22/08/2026)."""
    lignes: list[LigneJournee] = field(default_factory=list)
    exclu_s: int = 0                   # v3 : conservé à 0 (l'amplitude TTJ ne
                                       # retranche plus rien — règle T1) ;
                                       # le champ reste pour compatibilité
    total_pause_s: int = 0             # Σ TOUS les arrêts (toute durée, AM-1)
    tcj_s: int = 0                     # durée d'UNION des lignes (F1 §0duodecies
                                       # — = Σ durées si aucune ligne ne se
                                       # recouvre ; double comptage impossible)
    ttj_s: int = 0                     # amplitude fin_ref − départ (T1)


def union_duree_s(intervalles) -> int:
    """§0duodecies F1 (arbitrage LSS du 25/08/2026) — GARDE ABSOLUE
    anti-double-comptage : durée couverte par l'UNION des intervalles
    (début, fin). Un même instant n'est jamais compté deux fois → quelles
    que soient les lignes en entrée, le résultat ne peut pas dépasser
    l'amplitude (TCJ ≤ TTJ devient structurel).

    Pour des lignes qui ne se recouvrent pas (journées saines), le résultat
    est STRICTEMENT identique à la somme des durées (E2 §0undecies préservé
    sur ce point) : la règle n'est observable qu'en présence d'anomalie.

    Fonction PURE : aucune mutation, entrée itérable de couples
    (début, fin) avec fin > début (les couples dégénérés sont ignorés).
    Idempotente."""
    pts = sorted((d, f) for d, f in intervalles
                 if d is not None and f is not None and f > d)
    total = 0
    cur_d = cur_f = None
    for d, f in pts:
        if cur_d is None:
            cur_d, cur_f = d, f
        elif d <= cur_f:
            cur_f = max(cur_f, f)            # recouvrement → union
        else:
            total += int((cur_f - cur_d).total_seconds())
            cur_d, cur_f = d, f
    if cur_d is not None:
        total += int((cur_f - cur_d).total_seconds())
    return total


def _fin_effective(seg: Segment, vivant_possible: bool, roule: bool,
                   fin_substitution: datetime | None) -> datetime | None:
    """Fin de travail d'un segment OUVERT (heure_fin NULL) :
    - `vivant_possible` (début le plus récent de la journée) → None : vrai
      « en cours ». v1.16 — SANS condition de fraîcheur du dernier événement :
      MZoneX n'émet rien entre « Début du trajet » et « Fin du trajet », la
      chaîne devait rester visible en orange pendant tout le trajet, fut-il
      long (retour métier 05/08 : un trajet commencé à 11:37 disparaissait
      après 15 min sans nouvel événement — TCC cassé). Un « Fin » manqué est
      rattrapé au cycle Niveau 2 suivant (l'officiel au même début supplante
      le provisoire périmé — balai anti-jumeaux), et la pré-consolidation de
      01h00 (Référence v2 §9) clôt tout ouvert de la veille : aucun
      « en cours » ne reste collé à l'écran indéfiniment.
    - sinon (défensif — les ouverts non terminaux sont écartés AVANT chaînage,
      cf. construire_journee) → fin de substitution (dernier événement connu).
    `roule` / `fin_substitution` restent acceptés pour compatibilité d'appel."""
    fin = seg.fin
    if fin is not None and fin < seg.debut:
        fin = seg.debut
    if fin is not None:
        return fin
    if vivant_possible:
        return None
    sub = fin_substitution or seg.debut
    return max(sub, seg.debut)


def construire_journee(segments: list[Segment], *, maintenant: datetime,
                       date_jour: date | None = None,
                       pause_min: float = 1200, seuil_km: float = 0.3,
                       roule: bool = False,
                       fin_substitution: datetime | None = None,
                       pause_affichee_min: float = 1800) -> JourneeChainee:
    """Assemble la journée v3 (AMÉLIORATIONS, arbitrages C1→C4 du 22/08/2026).

    AM-2 : UN TRAJET VALIDE = UNE LIGNE (plus de fusion d'affichage) ; les
    pauses ne sont AFFICHÉES que si ≥ 30 min (`pause_affichee_min` =
    SEUIL_PAUSE_COUPURE_TCC), cellule vide sinon — l'écart brut réel reste
    calculé (`gap_brut_s`) pour les compteurs ; le stockage conserve tout.
    AM-1 : TCJ = Σ durées des lignes ; TTJ = amplitude ; arrêts = tous les
    intervalles, toute durée. AM-6 : départ = premier trajet valide ; une
    manœuvre ne compte jamais (R2). Couleurs orange/noir inchangées
    (§0quater/v1.16 : `pause_min` = DUREE_MIN_PAUSE_VALIDE 20 min).
    """
    segs = [s for s in segments if s.debut is not None]
    res = JourneeChainee()
    if not segs:
        return res

    # Détermination de la date cible pour le bornage strict 00:00:00 - 23:59:59
    if date_jour is not None:
        target_date = date_jour
    else:
        valid_dates = [s.debut.date() for s in segs if s.debut and not s.rejete]
        if valid_dates:
            target_date = max(valid_dates)
        else:
            target_date = maintenant.date()

    debut_jour = datetime.combine(target_date, datetime.min.time())
    fin_jour = datetime.combine(target_date, datetime.max.time().replace(microsecond=0))

    if maintenant.date() > target_date:
        maintenant = fin_jour
        est_jour_passe = True
        roule = False
    else:
        est_jour_passe = False

    # Filtrage et découpage strict des segments dans la fenêtre de la journée cible [00:00:00 -> 23:59:59]
    segs_filtres: list[Segment] = []
    for s in segs:
        if s.debut is None:
            continue
        # Segment entièrement en dehors de la journée
        if s.fin is not None and s.fin <= debut_jour:
            continue
        if s.debut > fin_jour:
            continue

        # Bornage au jour
        deb_borne = max(s.debut, debut_jour)
        fin_borne = min(s.fin, fin_jour) if s.fin is not None else (fin_jour if est_jour_passe else None)
        if fin_borne is not None and fin_borne < deb_borne:
            continue

        segs_filtres.append(Segment(
            debut=deb_borne,
            fin=fin_borne,
            distance_km=s.distance_km,
            rejete=s.rejete,
            ref=s.ref
        ))

    if not segs_filtres:
        return res

    debut_max = max((s.debut for s in segs_filtres if not s.rejete), default=None)
    if debut_max is None:
        return res                        # que des manœuvres : aucune ligne

    segs_filtres = [s for s in segs_filtres if not (s.fin is None and s.debut < debut_max)]
    segs_filtres.sort(key=lambda s: (s.debut, s.fin or datetime.max))

    # ---- 1 · lignes = segments non rejetés (AM-2 : aucune fusion) ; une
    # ligne OUVERTE toujours affichée (v1.16 : verdict manœuvre à la clôture)
    for s in segs_filtres:
        if s.rejete:
            continue
        fin_s = _fin_effective(s, vivant_possible=((s.debut >= debut_max) and not est_jour_passe),
                               roule=(roule if not est_jour_passe else False),
                               fin_substitution=fin_substitution)
        if (fin_s is not None and s.distance_km is not None
                and s.distance_km < seuil_km):
            continue                      # manœuvre clôturée : cachée (R2)
        ouverte = s.fin is None and fin_s is None
        fin_travail = fin_s if not ouverte else maintenant
        dist = None if s.distance_km is None else round(s.distance_km, 3)
        res.lignes.append(LigneJournee(
            debut=s.debut, fin=None if ouverte else fin_s,
            distance_km=dist, etat=ETAT_EN_COURS,  # réévalué au pas 2
            travail_s=max(0, int((fin_travail - s.debut).total_seconds())),
            premiere_ref=s.ref, nb_segments=1))

    # ---- 2 · états + pauses : gap BRUT toujours mesuré ; AFFICHAGE ≥ 30 min
    for i, ligne in enumerate(res.lignes):
        if ligne.fin is None:
            ligne.etat = ETAT_EN_COURS
            continue
        if i + 1 < len(res.lignes):
            brut = max(0, int((res.lignes[i + 1].debut
                               - ligne.fin).total_seconds()))
            ligne.gap_brut_s = brut
            ligne.pause_apres_s = brut if brut >= pause_affichee_min else 0
            ligne.etat = ETAT_OFFICIEL
        else:
            age = (maintenant - ligne.fin).total_seconds()
            ligne.etat = ETAT_EN_ATTENTE if age < pause_min else ETAT_OFFICIEL

    # ---- 3 · compteurs v3 (AM-1 + T1) — départ = 1er mouvement valide (AM-6)
    # §0duodecies F1 (25/08/2026) UNION : TCJ = durée couverte par l'union
    # des intervalles des lignes — un instant n'est JAMAIS compté deux fois
    # (double comptage par recouvrement impossible, TCJ ≤ TTJ structurel ;
    # identique à Σ durées dès qu'aucune ligne ne se recouvre, E2 préservé).
    # fin_ref = fin la plus tardive (ou « maintenant » si une ligne est
    # ouverte) : l'amplitude englobe toujours toutes les lignes.
    if res.lignes:
        depart = res.lignes[0].debut
        fin_ref = max(lg.fin or maintenant for lg in res.lignes)
        raw_tcj = union_duree_s((lg.debut, lg.fin or maintenant)
                                for lg in res.lignes)
        raw_ttj = max(0, int((fin_ref - depart).total_seconds()))

        # Plafond strict à 24h (86400 s) par jour
        res.ttj_s = min(86400, raw_ttj)
        res.tcj_s = min(res.ttj_s, raw_tcj)
        res.total_pause_s = max(0, res.ttj_s - res.tcj_s)
    return res

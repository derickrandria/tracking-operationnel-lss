"""Addendum v1.4 — Stratégie hybride MZoneX : réconciliation Niveau 1 → Niveau 2.

Niveau 1 (temps réel)  : l'algorithme de reconstruction maison (Addendum v1.2
                         §3.2) produit des trajets « PROVISOIRE » à partir de
                         l'onglet Événements (DEMARRAGE/ARRET).
Niveau 2 (consolidé)   : dès qu'un trajet apparaît dans l'onglet Trajets de
                         MZoneX (calcul natif plateforme, mêmes seuils métier
                         0,3 km / 20 min), la donnée officielle REMPLACE la
                         donnée provisoire — sans doublon ni perte — et le
                         moteur recalcule TCC/TCJ/TTJ (§2.4, critères 16-21).

Garde-fous implémentés :
- tolérance de rapprochement horaire paramétrable
  (SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET, ±2 min par défaut) ;
- écart anormal (> SEUIL_DIVERGENCE_TRAJET, 10 min) : journal d'audit §11,
  SANS bloquer le remplacement (le trajet VALIDÉ reste toujours prioritaire) ;
- trajet validé sans équivalent provisoire : création + anomalie auditée ;
- trajets à cheval sur minuit : réconciliation possible sur la veille ARCHIVÉE
  pendant FENETRE_RECONCILIATION_APRES_MINUIT (2h par défaut, §3.3) — le JSON
  d'HistoriqueJournalier est resynchronisé avec les valeurs validées.

v1.9 — dernière ligne du rapport = EN COURS (retour métier 01/08/2026) :
  chez CamtrackPro (« Detail Trajet »), la dernière ligne du jour n'est PAS un
  trajet terminé si le véhicule roule encore — son « Date - Heure Fin » est la
  dernière position connue, pas une fin officielle. Règle appliquée à toutes
  les sources : dans chaque (véhicule, jour), la dernière ligne dont la fin
  est constatée depuis moins de DUREE_MIN_PAUSE_VALIDE (20 min) est importée
  PROVISOIRE / EN_ATTENTE (heure de fin PROVISOIRE affichée, jamais déclarée
  officielle), mise à jour à chaque sync tant qu'elle grandit, puis JUGÉE à
  sa clôture effective (pause ≥ 20 min derrière, ou ligne suivante apparue) :
  ≥ 0,3 km → VALIDE, sinon IGNORÉE comme manœuvre (Référence v2 §2.1/§7 —
  la règle « en mouvement » ≥ 20 min côté CamtrackPro est SUPPRIMÉE depuis
  l'arbitrage du 06/08, §11.2). Elle n'entre donc JAMAIS dans TCC/TCJ/TTJ de
  façon définitive.

v1.13 — CHAÎNES + UNICITÉ PAR DÉBUT (retour métier 04/08/2026) :
  · la GRILLE (écran/exports/archive) et TCC/TCJ/TTJ lisent la journée
    CHAÎNÉE (app/chaines.py) : fusion réelle des écarts < 20 min, manœuvres
    soudures, noir seulement après pause ≥ 20 min constatée derrière ;
  · le rapprochement officiel↔provisoire accepte un jumeau OUVERT au même
    début (plus de doublon « orange puis noir au même début ») ;
  · GARDE « chaîne en cours » : un jumeau ouvert n'est refermé par la fin
    officielle QUE si rien ne continue après elle (ni segment ultérieur du
    cycle/base, ni camion roulant) — sinon la ligne officielle est COUVERTE
    et reviendra refermer à la vraie fin ;
  · le balai FUSIONNE les jumeaux au même début (guérison de l'existant).

v1.18 — RÉFÉRENCE IA v2 / arbitrages du 06/08/2026 (SUPERSÈDE v1.13/v1.17
sur la manœuvre) : IGNORÉE partout (§7 — ne soude plus, ne prolonge plus,
n'interrompt plus ; jamais de segment posé), fin = PREMIER ARRÊT après
trajet valide (§5.1), règle CamtrackPro « en mouvement » SUPPRIMÉE (§11.2),
écart < 20 min entre trajets RÉELS seuls → même ligne (§2.2).
"""
import logging
import os
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from .config import jour_attribution, now_local
from .engine import (PUBLISH_ENABLED, _verifier_temps, appliquer_badge_et_eco,
                     creer_conducteur_auto, creer_vehicule_auto, ensure_suivi,
                     get_seuils, recalculer_temps, resoudre_badge)
from .event_bus import publish
from .models import (AuditLog, HistoriqueJournalier, StatutSourceTrajet,
                     StatutValidationTrajet, SuiviJournalier, Trajet, Vehicule)
from .serializers import iso, journee_suivi, s_ligne, s_suivi, s_trajet

log = logging.getLogger("lss.reconciliation")

SOURCE_SYSTEME = "systeme"
# §0nonies decies M1 (arbitrage LSS du 29/08/2026) — fenêtre souveraine de
# relecture des jours passés (réconciliabilité), identique au collecteur.
RELECTURE_TRAJETS_JOURS_DEF = int(os.getenv("RELECTURE_TRAJETS_JOURS", "7"))


# ------------------------------------------------------------------ entrées
def normaliser_valides(items: list[dict]) -> list[dict]:
    """Nettoie les trajets validés venant d'un connecteur (onglet Trajets) :
    horodatages typés, distance numérique, dédoublonnage (véhicule, début)."""
    vus = set()
    propres = []
    for it in items:
        try:
            if isinstance(it.get("debut"), str):
                it["debut"] = datetime.fromisoformat(it["debut"].replace(" ", "T", 1)[:19])
            if isinstance(it.get("fin"), str):
                it["fin"] = datetime.fromisoformat(it["fin"].replace(" ", "T", 1)[:19])
            if it.get("debut") is None:
                continue
            if it.get("fin") is not None and it["fin"] < it["debut"]:
                it["fin"] = None
            it["distance_km"] = (None if it.get("distance_km") in (None, "")
                                 else max(0.0, float(it["distance_km"])))
            cle = (str(it.get("vehicule_id") or it.get("gps_associe") or it.get("plaque") or ""),
                   it["debut"].isoformat())
            if cle in vus:
                continue
            vus.add(cle)
            it["source"] = str(it.get("source") or "MZONEX").upper()
            propres.append(it)
        except (TypeError, ValueError):
            log.warning("Trajet validé illisible écarté : %r", it)
    return propres


def _audit(db, action: str, entite_id: str | None, details: dict):
    db.add(AuditLog(username=SOURCE_SYSTEME, action=action, entite="trajet",
                    entite_id=entite_id, details=details))


def _badge_eco(db, trajet, suivi, vehicule, it, username):
    """§0septies B2/B3 (20/08/2026) — badge chauffeur + compteurs d'écoconduite
    officiels sur la ligne de trajet. Jamais d'exception vers la
    réconciliation (la donnée trajet prime, §10) : erreur tracée, pas propagée."""
    try:
        appliquer_badge_et_eco(db, trajet, suivi, vehicule, it,
                               username=username)
        db.flush()
    except Exception:
        log.exception("§0septies : application badge/éco en échec — %s",
                      getattr(vehicule, "plaque", "?"))


def _recalculer_pauses(trajets: list[Trajet]):
    """Pause après trajet i = début(i+1) − fin(i) — recalée après remplacement
    des horaires par les valeurs officielles (§2.4 point 2).
    v1.12 : le DERNIER trajet n'a pas de pause « après » → 0 (jamais de
    reliquat d'un ancien suivant supprimé, ex. « 33 min » fantôme).
    v1.17 : appariement entre lignes AFFICHÉES uniquement.
    v1.18 (Référence v2 §7) : les lignes marquées REJETÉ (provisoires Niveau
    1 au début d'une manœuvre ignorée, ou invalides historiques) ne volent
    plus la pause du trajet voisin — la pause affichée = l'écart RÉEL entre
    lignes affichées (0616TCC : pause T1 = 1:24, jamais 0 contre une
    manœuvre). Conforme à la grille : écart réel entre mouvements réels."""
    for t in trajets:
        t.pause_apres_s = 0
    affichees = [t for t in trajets
                 if t.statut_validation != StatutValidationTrajet.REJETE]
    for i in range(len(affichees) - 1):
        courant, suivant = affichees[i], affichees[i + 1]
        if courant.heure_fin and suivant.heure_debut:
            gap = (suivant.heure_debut - courant.heure_fin).total_seconds()
            courant.pause_apres_s = max(0, int(gap))


def _renumeroter(trajets: list[Trajet]):
    for i, t in enumerate(trajets, start=1):
        t.numero = i


def _dans_fenetre_minuit(seuils: dict, jour: date, maintenant: datetime) -> bool:
    """§3.3 + Référence v2 §8/§9 — un trajet reste réconciliable sur son JOUR
    D'ATTRIBUTION tant que celui-ci est le jour logistique courant (bascule
    01h00), et sur la journée qui vient de basculer pendant
    FENETRE_RECONCILIATION_APRES_MINUIT après la bascule de 01h00
    (trajets à cheval — la pré-consolidation de 01h00 fige ensuite
    l'historique, resynchronisé le cas échéant par §3.3).
    §0nonies decies M1 (arbitrage LSS du 29/08/2026) — RELECTURE : tout jour
    reste réconciliable tant qu'il n'a pas plus de RELECTURE_TRAJETS_JOURS
    (7 par défaut, aligné sur la collecte Ym@ne J-8→J déjà gravée) ; la règle
    historique « J-1 + 2 h après la bascule » est désormais ENGLOBÉE (J-1 est
    toujours dans la fenêtre)."""
    courant = jour_attribution(maintenant)
    if jour >= courant:
        return True
    limite = int(seuils.get("RELECTURE_TRAJETS_JOURS",
                            RELECTURE_TRAJETS_JOURS_DEF))
    return jour >= courant - timedelta(days=limite)


def _synchroniser_archive(db, suivi: SuiviJournalier):
    """§3.3 — si la journée est déjà archivée, resynchronise le snapshot JSON
    d'HistoriqueJournalier avec les valeurs désormais VALIDÉES.
    §0nonies decies M1 (29/08/2026) — amendement §A.2 : la grille archivée
    n'est régénérée QUE si son contenu change réellement, avec audit
    « archive.raffraichie » (avant → après) ; jamais de suppression de ligne
    hors remplacement provisoire → validé (§2.4 déjà adopté)."""
    h = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == suivi.date_jour,
        HistoriqueJournalier.vehicule_id == suivi.vehicule_id))
    if h is None:
        return False
    donnees = dict(h.donnees or {})
    # v1.13 — l'archive fige les LIGNES de la journée chaînée (écran =
    # archive = export), à jour des dernières valeurs officielles
    seuils = get_seuils(db)
    journee = journee_suivi(suivi, seuils)
    avant_lignes = donnees.get("trajets") or []
    donnees["trajets"] = [s_ligne(lg, i)
                          for i, lg in enumerate(journee.lignes, start=1)]
    change = avant_lignes != donnees["trajets"]
    # §0vicies decies N3 (31/08/2026) — les relevés de positions font aussi
    # partie du contenu archivé : un rattrapage de cellules VIDES (J-1→J-7)
    # régénère le snapshot de la même façon, audité `archive.raffraichie`.
    champs_pos = ("position_08h", "position_10h", "position_12h", "position_14h",
                  "position_16h", "position_18h", "position_20h", "position_22h")
    pos_apres = {c: getattr(suivi, c, None) for c in champs_pos}
    change_pos = any(donnees.get(c) != v for c, v in pos_apres.items())
    if change_pos:
        donnees.update(pos_apres)
        change = True
    if change:
        _audit(db, "archive.raffraichie", None, {
            "plaque": suivi.vehicule.plaque if suivi.vehicule else None,
            "jour": suivi.date_jour.isoformat(),
            "avant": {"nb_lignes": len(avant_lignes),
                      "nb_trajets": donnees.get("nb_trajets")},
            "apres": {"nb_lignes": len(donnees["trajets"]),
                      "nb_trajets": len(journee.lignes)},
            "positions": change_pos,
            "regle": "§0nonies decies M1 (29/08/2026) : relecture officielle "
                     "d'un jour passé → grille archivée régénérée EN PLACE "
                     "(audit avant → après, jamais de suppression)"})
        log.info("Archive régénérée (M1) — %s %s : %d → %d ligne(s)%s",
                 suivi.date_jour.isoformat(),
                 suivi.vehicule.plaque if suivi.vehicule else "?",
                 len(avant_lignes), len(donnees["trajets"]),
                 " (+ positions N3)" if change_pos else "")
    donnees["nb_trajets"] = len(journee.lignes)
    donnees["heure_depart"] = iso(suivi.heure_depart)
    donnees["tcc_s"], donnees["tcj_s"] = suivi.tcc_s, suivi.tcj_s
    donnees["ttj_s"], donnees["total_pause_s"] = suivi.ttj_s, suivi.total_pause_s
    h.donnees = donnees        # réaffectation → détection de changement JSON
    return True


def _propager(db, suivi: SuiviJournalier, vehicule: Vehicule,
              maintenant: datetime, jour_actif: bool):
    """§2.4 points 4-5 — recalcul TCC/TCJ/TTJ puis propagation de la chaîne de
    synchronisation standard (Suivi → Infractions → Alertes → temps réel)."""
    seuils = get_seuils(db)
    db.expire(suivi, ["trajets"])   # relecture forcée (session longue) avant recalcul
    recalculer_temps(db, suivi, maintenant)
    if jour_actif:
        _verifier_temps(db, suivi, vehicule, seuils, maintenant)


def _trouver_provisoire(trajets: list[Trajet], debut: datetime, fin,
                        tolerance: float):
    """Rapprochement par l'horodatage de DÉBUT (± tolérance) ; secours par la
    FIN si le début ne rapproche rien (§2.4 point 1).
    v1.13 — le rapprochement par le DÉBUT accepte aussi un provisoire OUVERT
    (heure_fin NULL) : c'est le même trajet, simplement encore vivant — sans
    cela chaque ligne officielle d'un trajet détecté en direct créait un
    DOUBLON « jumeau » (constat métier 04/08 : lignes orange et noire au MÊME
    début). Le secours par la FIN reste limité aux trajets clôturés."""
    candidats = [t for t in trajets
                 if t.statut_source == StatutSourceTrajet.PROVISOIRE]
    meilleur, delta_min = None, None
    for t in candidats:
        delta = abs((t.heure_debut - debut).total_seconds())
        if delta_min is None or delta < delta_min:
            meilleur, delta_min = t, delta
    if meilleur is not None and delta_min <= tolerance:
        return meilleur
    if fin is not None:
        meilleur, delta_min = None, None
        for t in candidats:
            if t.heure_fin is None:
                continue                     # secours par la fin : clôturés seuls
            delta = abs((t.heure_fin - fin).total_seconds())
            if delta_min is None or delta < delta_min:
                meilleur, delta_min = t, delta
        if meilleur is not None and delta_min <= tolerance:
            return meilleur
    return None


def _deja_valide(trajets: list[Trajet], debut: datetime, tolerance: float) -> bool:
    """Idempotence : un trajet déjà VALIDÉ aux mêmes horaires → ne rien faire
    (critère 17 : jamais de doublon, même en cas de double scraping).
    v1.17 — un SEGMENT REJETÉ (manœuvre soudée posée pour soudure/exclusion)
    n'est PAS une ligne officielle : il ne rend pas le début « déjà validé »."""
    for t in trajets:
        if t.statut_source == StatutSourceTrajet.VALIDE \
                and t.statut_validation != StatutValidationTrajet.REJETE \
                and abs((t.heure_debut - debut).total_seconds()) <= tolerance:
            return True
    return False


def _trouver_par_debut(trajets: list[Trajet], debut: datetime, tolerance: float):
    """Rapprochement LARGE par l'horodatage de DÉBUT (± tolérance), tout statut
    (PROVISOIRE en cours, VALIDÉ prématurément clôturé, REJETÉ). Un même début
    = une même session moteur/trajet : même enregistrement, jamais de doublon."""
    meilleur, delta_min = None, None
    for t in trajets:
        delta = abs((t.heure_debut - debut).total_seconds())
        if delta_min is None or delta < delta_min:
            meilleur, delta_min = t, delta
    if meilleur is not None and delta_min <= tolerance:
        return meilleur
    return None


def _trouver_precedent(trajets: list[Trajet], reference: datetime):
    """Dernier trajet AFFICHABLE (≠ REJETÉ) terminé AVANT `reference` — pour
    tester la fusion d'une ligne « en cours » avec le trajet qui la précède
    (pause < DUREE_MIN_PAUSE_VALIDE → le trajet continue, §1.2)."""
    precedents = [t for t in trajets
                  if t.statut_validation != StatutValidationTrajet.REJETE
                  and t.heure_fin is not None and t.heure_fin <= reference]
    return max(precedents, key=lambda t: t.heure_fin, default=None)


def _trouver_valide_par_debut(trajets: list[Trajet], debut: datetime,
                              tolerance: float):
    """VALIDÉ AFFICHABLE existant au même début — pour la mise à jour d'une
    donnée officielle tardive (fin/distance étendues après clôture prématurée).
    v1.17 — les SEGMENTS REJETÉS (manœuvres soudées, statut_source VALIDÉ mais
    masqués) ne doivent JAMAIS capter la ligne officielle fusionnée : sans
    cette exclusion, la chaîne retenue « mettait à jour » un segment invisible
    au lieu d'écrire la ligne affichée (bug du 05/08, 3046TBS)."""
    valides = [t for t in trajets
               if t.statut_source == StatutSourceTrajet.VALIDE
               and t.statut_validation != StatutValidationTrajet.REJETE]
    return _trouver_par_debut(valides, debut, tolerance)


def _trouver_par_fin_affiche(trajets: list[Trajet], debut: datetime,
                             fin: datetime, tolerance: float):
    """v1.10 — trajet AFFICHABLE (≠ REJETÉ) dont la FIN coïncide (± tolérance)
    avec la ligne officielle mais dont le DÉBUT diffère au-delà de la
    tolérance : c'est le MÊME trajet, vu plus COMPLÈTEMENT côté officiel
    (ex. fusion de lignes du matin lues en retard) → à absorber dans le même
    enregistrement au lieu de créer un doublon."""
    meilleur, dmin = None, None
    for t in trajets:
        if t.statut_validation == StatutValidationTrajet.REJETE \
                or t.heure_fin is None:
            continue
        if abs((t.heure_debut - debut).total_seconds()) <= tolerance:
            continue                          # déjà couvert par le rapprochement début
        d = abs((t.heure_fin - fin).total_seconds())
        if d <= tolerance and (dmin is None or d < dmin):
            meilleur, dmin = t, d
    return meilleur


# ------------------------------------------------------------------ cœur
# ============================================================================
# v1.11 — BALAI ANTI-FANTÔMES (constat métier 03/08/2026, écrans de 16:13) :
# la journée construite par les anciennes versions a laissé des lignes
# ORPHELINES : doublons d'un même horaire, sous-intervalles d'un trajet
# officiel (ex. ancien « ouvert » ensuite absorbé par une fusion), horaires
# corrompus (fin AVANT le début). Les rapprochements par début/fin ne les
# voient jamais (elles ne correspondent à aucune ligne officielle) → elles
# restaient à l'écran. Comme les lignes officielles REVIENNENT à chaque
# cycle, on balaie après chaque réconciliation : tout enregistrement non
# rattaché qui est un doublon / strictement contenu / corrompu est PURGÉ
# (audit conservé) et les pauses sont recalculées. Auto-réparant.
# ============================================================================
def _epurer_orphelins(db, suivi, vehicule, intervalles, ids_conserves,
                      maintenant, tolerance, pause_min, jour, jour_actuel) -> int:
    trajets = list(db.scalars(select(Trajet).where(
        Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all())
    epures = 0
    conserves = [x for x in trajets if x.id in ids_conserves]

    def _snap(x):
        return {"id": x.id, "debut": iso(x.heure_debut), "fin": iso(x.heure_fin),
                "distance_km": x.distance_km,
                "statut": (x.statut_source.value if hasattr(x.statut_source, "value")
                           else str(x.statut_source))}

    for t in list(trajets):
        if t.id in ids_conserves:
            continue                                     # ligne officielle
        if t.statut_validation == StatutValidationTrajet.REJETE:
            continue                                     # déjà invisible
        # ============ v1.13 — FUSION DES JUMEAUX AU MÊME DÉBUT ============
        # Guérison des lignes « orange puis noire au MÊME début » écrites par
        # les anciennes versions (constat métier 04/08) : un même début = une
        # même session de trajet = UN seul enregistrement. Le jumeau VIVANT
        # (pas de fin ET chaîne réellement en cours : début le plus récent du
        # jour ou camion qui bouge maintenant) reste maître et hérite la
        # distance officielle ; l'ouvert PÉRIMÉ ou le doublon clôturé est
        # purgé — la ligne officielle fait foi. Les lignes officielles
        # reviennent à chaque cycle : le jumeau vivant sera refermé à la
        # vraie fin de chaîne (garde « chaîne en cours »).
        jumeau, dmin = None, None
        for c in conserves:
            d = abs((t.heure_debut - c.heure_debut).total_seconds())
            if d <= tolerance and (dmin is None or d < dmin):
                jumeau, dmin = c, d
        if jumeau is not None:
            # VIVANT réel = ouvert ET camion en mouvement MAINTENANT (signal
            # frais ≤ 15 min, vitesse > 3) — sinon l'ouvert est un débris
            # périmé : purgé, la ligne officielle (clôturée) fait foi.
            vivant_reel = (t.heure_fin is None
                           and vehicule.last_event_at is not None
                           and (maintenant - vehicule.last_event_at).total_seconds() <= 900
                           and (vehicule.last_vitesse or 0) > 3)
            if t.heure_fin is None and vivant_reel:
                if jumeau.distance_km is not None:
                    t.distance_km = jumeau.distance_km
                if jumeau.source_plateforme:
                    t.source_plateforme = jumeau.source_plateforme
                _audit(db, "trajet.jumeau_fusionne", jumeau.id, {
                    "plaque": vehicule.plaque, "jour": jour.isoformat(),
                    "garde": _snap(t), "retire": _snap(jumeau),
                    "regle": "même début = même trajet : le jumeau VIVANT "
                             "reste maître (chaîne en cours), le doublon "
                             "officiel est retiré — il refermera à la vraie "
                             "fin (garde « chaîne en cours »)"})
                log.info("Jumeau fusionné (vivant conservé) — %s %s",
                         vehicule.plaque, iso(t.heure_debut))
                db.delete(jumeau)
                trajets.remove(jumeau)
                conserves.remove(jumeau)
            else:
                if jumeau.distance_km is None and t.distance_km is not None:
                    jumeau.distance_km = t.distance_km
                _audit(db, "trajet.jumeau_fusionne", t.id, {
                    "plaque": vehicule.plaque, "jour": jour.isoformat(),
                    "garde": _snap(jumeau), "retire": _snap(t),
                    "regle": "même début = même trajet : doublon (ouvert "
                             "périmé ou clôturé) purgé, le trajet maître de "
                             "l'écran est conservé"})
                log.info("Jumeau fusionné (officiel conservé) — %s %s",
                         vehicule.plaque, iso(t.heure_debut))
                db.delete(t)
                trajets.remove(t)
            epures += 1
            continue
        fin_affichee = t.heure_fin or maintenant
        # un trajet MOTEUR (Niveau 1) vraiment vivant — fin provisoire fraîche
        # (< pause_min) ou pas encore de fin — n'est jamais touché ici
        vivant = (t.statut_source != StatutSourceTrajet.VALIDE
                  and (t.heure_fin is None
                       or (maintenant - fin_affichee).total_seconds() < pause_min))
        raison = None
        if t.heure_fin is not None and \
                (t.heure_debut - t.heure_fin).total_seconds() > tolerance:
            raison = "corrompu"          # fin AVANT le début : jamais réel
        elif not vivant:
            for d, f in intervalles:
                if (t.heure_fin is not None and f is not None
                        and abs((t.heure_debut - d).total_seconds()) <= tolerance
                        and abs((t.heure_fin - f).total_seconds()) <= tolerance):
                    raison = "doublon"   # même horaire officiel, autre enregistrement
                    break
                f_eff = f or maintenant
                if (t.heure_debut >= d - timedelta(seconds=tolerance)
                        and fin_affichee <= f_eff + timedelta(seconds=tolerance)):
                    # sous-intervalle STRICT d'une ligne officielle : les
                    # lignes officielles ne se chevauchent jamais → fantôme
                    raison = "contenu"
                    break
        if raison is None:
            continue
        _audit(db, "trajet.orphelin_purge", t.id, {
            "plaque": vehicule.plaque, "jour": jour.isoformat(),
            "debut": iso(t.heure_debut), "fin": iso(t.heure_fin),
            "distance_km": t.distance_km,
            "statut_source": (t.statut_source.value
                              if hasattr(t.statut_source, "value")
                              else str(t.statut_source)),
            "raison": raison,
            "regle": "ligne fantôme (doublon / sous-intervalle / fin avant "
                     "début) non rattachée aux lignes officielles → purgée "
                     "de l'écran (audit conservé)"})
        log.info("Ligne fantôme purgée (%s) — %s %s→%s", raison,
                 vehicule.plaque, iso(t.heure_debut), iso(t.heure_fin))
        db.delete(t)
        trajets.remove(t)
        epures += 1
    if epures:
        trajets.sort(key=lambda x: x.heure_debut)
        _renumeroter(trajets)
        _recalculer_pauses(trajets)
        db.flush()
        _propager(db, suivi, vehicule, maintenant,
                  jour_actif=(jour == jour_actuel))
        _synchroniser_archive(db, suivi)
    return epures


# ============================================================================
# v1.12 — CONTRAT GRILLE STRICT (retour métier 03/08/2026) : chaque ligne
# affichée doit se lire début/fin/pause/début/fin… STRICTEMENT croissante ;
# chaque pause affichée = écart réel ET ≥ 20 min (sinon les deux trajets
# auraient dû être fusionnés, §1.2) ; jamais d'inversion, de recouvrement,
# d'heure répétée ni de pause < 20 min entre deux lignes CLOSES.
# Ce correcteur impose le contrat en base à chaque cycle :
#   · une ligne OFFICIELLE (rattachée ce cycle) n'est JAMAIS mutée — le
#     portail fait foi ; le résidu d'en face est purgé ;
#   · entre débris (« contenu » déjà balayé en v1.11), le recouvrement purge
#     le suivant et une pause < 20 min FUSIONNE dans le précédent ;
#   · un trajet moteur VIVANT (pas de fin) n'est jamais touché.
# ============================================================================
def _corriger_structure(db, suivi, vehicule, ids_conserves, maintenant,
                        tolerance, pause_min, jour, jour_actuel) -> int:
    trajets = [t for t in db.scalars(select(Trajet).where(
        Trajet.suivi_id == suivi.id).order_by(
            Trajet.heure_debut, Trajet.heure_fin)).all()
        if t.statut_validation != StatutValidationTrajet.REJETE]
    trajets.sort(key=lambda t: (t.heure_debut, t.heure_fin or datetime.max))
    corrections = 0

    def _snap(t):
        return {"id": t.id, "debut": iso(t.heure_debut), "fin": iso(t.heure_fin),
                "distance_km": t.distance_km,
                "statut": (t.statut_validation.value
                           if hasattr(t.statut_validation, "value")
                           else str(t.statut_validation))}

    def _purge(cible, gardien, cause):
        _audit(db, "trajet.structure_corrige", cible.id, {
            "plaque": vehicule.plaque, "jour": jour.isoformat(),
            "action": cause, "gardien": _snap(gardien), "purge": _snap(cible),
            "regle": {"purge_residu": "ligne résiduelle face à une ligne "
                                      "officielle → purgée (l'officiel fait foi)",
                      "purge_recouvrement": "ligne recouvrante/contenue dans la "
                                            "précédente → purgée"}[cause]})
        log.info("Structure corrigée (%s) — %s %s→%s", cause, vehicule.plaque,
                 iso(cible.heure_debut), iso(cible.heure_fin))
        db.delete(cible)
        trajets.remove(cible)

    i = 0
    while i < len(trajets) - 1:
        a, b = trajets[i], trajets[i + 1]
        if a.heure_fin is None or b.heure_fin is None:
            i += 1                                   # trajet vivant : sacré
            continue
        a_cons, b_cons = a.id in ids_conserves, b.id in ids_conserves
        recouvrant = (b.heure_debut <= a.heure_fin + timedelta(seconds=tolerance)
                      and b.heure_fin <= a.heure_fin + timedelta(seconds=tolerance))
        pause_courte = ((b.heure_debut - a.heure_fin).total_seconds() < pause_min)
        if not recouvrant and not pause_courte:
            i += 1
            continue
        if a_cons and b_cons:
            i += 1                           # l'officiel fait foi des deux côtés
            continue
        if a_cons != b_cons:
            # une seule ligne officielle : l'autre est un résidu → purgée,
            # l'officielle n'est JAMAIS mutée
            _purge(a if b_cons else b, b if b_cons else a, "purge_residu")
            corrections += 1
            continue                     # paire réévaluée au même rang
        if recouvrant:
            _purge(b, a, "purge_recouvrement")
            corrections += 1
            continue
        # v3 AM-2 (22/08/2026) : PLUS de fusion « pause courte » — deux lignes
        # non officielles séparées par un arrêt < 20 min restent DEUX lignes :
        # l'arrêt est réel (déduit du TCJ, AM-1) et chaque trajet valide a sa
        # ligne à l'écran. Le stockage conserve tout, rien n'est absorbé.
        i += 1
    if corrections:
        _renumeroter(trajets)
        _recalculer_pauses(trajets)
        db.flush()
        _propager(db, suivi, vehicule, maintenant,
                  jour_actif=(jour == jour_actuel))
        _synchroniser_archive(db, suivi)
    return corrections


# ============================================================================
# §0nonies decies M2 (arbitrage LSS du 29/08/2026) — DÉCOUPAGE SOUVERAIN des
# « géants » : une ligne PROVISOIRE (fabriquée en direct pendant que le
# boîtier était muet, ex. 10:20→18:11 pour le 2746TCC du 28/08) recouverte
# par la série OFFICIELLE publiée plus tard est un résidu : marquée REJETÉE
# (flag conservé §3.2 — JAMAIS de suppression physique), audit avant → après.
# La série officielle — déjà posée par la boucle principale (remplacement
# en place de la 1ʳᵉ ligne rapprochée, création des suivantes) — découpe
# ainsi définitivement le provisoire, pauses réelles retrouvées.
# ============================================================================
def _rejeter_geants_recouverts(db, suivi, vehicule, intervalles, ids_conserves,
                               maintenant, tolerance, pause_min, jour,
                               jour_actuel) -> int:
    if not intervalles:
        return 0
    trajets = list(db.scalars(select(Trajet).where(
        Trajet.suivi_id == suivi.id).order_by(Trajet.heure_debut)).all())
    debuts_officiels = [d for d, _f in intervalles if d is not None]
    bornes = [(d, f) for d, f in intervalles if d is not None and f is not None]
    rejetes = 0
    for t in trajets:
        if t.id in ids_conserves:
            continue                          # ligne officielle de ce cycle
        if t.statut_validation == StatutValidationTrajet.REJETE:
            continue
        if t.statut_source != StatutSourceTrajet.PROVISOIRE:
            continue
        if t.heure_debut is None or t.heure_fin is None:
            continue                          # ligne VIVANTE : sacrée (jamais)
        # rapprochable par le début → traitée par la boucle §2.4 elle-même
        if any(abs((t.heure_debut - d).total_seconds()) <= tolerance
               for d in debuts_officiels):
            continue
        # absorbable par la fin → traitée par la règle v1.10 elle-même
        fins_officielles = [f for _d, f in bornes]
        if any(abs((t.heure_fin - f).total_seconds()) <= tolerance
               for f in fins_officielles):
            continue
        # « recouvre » = AU MOINS 2 lignes officielles couvertes à moins de
        # pause_min près aux bords (pauses réelles entre officiels admises) ;
        # tête/queue hors série ≤ pause_min (au-delà : conduite possible, on
        # ne touche à rien — on n'invalide jamais du réel)
        couvertes = []
        marge = timedelta(seconds=tolerance)
        for d, f in bornes:
            if f <= t.heure_debut - marge or d >= t.heure_fin + marge:
                continue
            inter = (min(t.heure_fin, f) - max(t.heure_debut, d))
            if inter.total_seconds() >= (f - d).total_seconds() - pause_min:
                couvertes.append((d, f))
        if len(couvertes) < 2:
            continue
        if (min(d for d, _ in couvertes) - t.heure_debut).total_seconds() \
                > pause_min:
            continue
        if (t.heure_fin - max(f for _d, f in couvertes)).total_seconds() \
                > pause_min:
            continue
        avant = {"debut": iso(t.heure_debut), "fin": iso(t.heure_fin),
                 "distance_km": t.distance_km}
        t.statut_validation = StatutValidationTrajet.REJETE
        rejetes += 1
        _audit(db, "trajet.geant_rejete", t.id, {
            "plaque": vehicule.plaque, "jour": jour.isoformat(),
            "avant": avant,
            "regle": "§0nonies decies M2 (29/08/2026) : géant provisoire "
                     "« boîtier muet » recouvert par les trajets officiels "
                     "publiés → REJETÉ (la série officielle découpe ; flag "
                     "conservé, jamais de suppression)"})
        log.info("§0nonies M2 : géant provisoire rejeté — %s %s→%s "
                 "(officiel souverain)", vehicule.plaque,
                 iso(t.heure_debut), iso(t.heure_fin))
    if rejetes:
        restants = [t for t in trajets
                    if t.statut_validation != StatutValidationTrajet.REJETE]
        restants.sort(key=lambda t: (t.heure_debut,
                                     t.heure_fin or datetime.max))
        _renumeroter(restants)
        _recalculer_pauses(restants)
        db.flush()
        _propager(db, suivi, vehicule, maintenant,
                  jour_actif=(jour == jour_actuel))
        _synchroniser_archive(db, suivi)
    return rejetes


# ============================================================================
# RÉFÉRENCE IA v2 (loi) — arbitrages métier du 06/08/2026, v1.18.
# Ce module SUPERSÈDE la soudure v1.13 et le chaînage officiel v1.17 :
#  §7   : une manœuvre (< 0,3 km) est IGNORÉE PARTOUT — jamais de ligne,
#         jamais de segment REJETÉ posé, elle ne SOUDE plus ses voisins, ne
#         PROLONGE plus aucune fin, n'INTERROMPT jamais une pause et n'entre
#         JAMAIS dans TCC/TCJ/TTJ (compteurs §2) ;
#  §5.1 : la FIN officielle d'une ligne = le PREMIER ARRÊT après le trajet
#         valide = fin du DERNIER SEGMENT RÉEL (0616TCC : Fin T1 08:24,
#         pause 1:24 — les 4 manœuvres qui suivent sont ignorées) ;
#  §2.2 : écart < 20 min entre deux trajets RÉELS → même ligne (mesuré de
#         fin à début RÉELS, à travers d'éventuelles manœuvres ignorées) ;
#  §11.2: la règle CamtrackPro « en mouvement ≥ 20 min » est SUPPRIMÉE —
#         valide dès 0,3 km de distance seule (arbitrage Q3 du 06/08 ;
#         5716TBS : R4 redevient valide, T2 garde sa propre fin 13:46) ;
#  §6.2 : phase 1 inchangée — la dernière ligne « en cours » du rapport
#         reste PROVISOIRE / EN_ATTENTE jusqu'à sa clôture effective (v1.9).
# La ligne Niveau 1 reconstruite au MÊME début qu'une manœuvre ignorée est
# toujours marquée REJETÉE (garde anti-géants v1.10, conservation §3.2).
# ============================================================================
def _filtrer_et_fusionner_v15(db, items: list[dict], seuils: dict,
                              mapping: dict, maintenant: datetime) -> tuple[list[dict], int, set]:
    """Retourne (items retenus, nb rejets, suivi_ids recalculés).
    v1.18 : « retenus » = lignes RÉELLES uniquement (§7) ; « rejets » =
    manœuvres ignorées (compteur + audit)."""
    seuil_km = float(seuils.get("SEUIL_DISTANCE_MIN_TRAJET_KM", 0.3))
    pause_min = float(seuils.get("DUREE_MIN_PAUSE_VALIDE", 1200))
    rejets = 0
    suivis_recalcules: set[str] = set()
    # travail sur des COPIES : les passes ci-dessous enrichissent les lignes
    # (fusion des fins/distances, marqueur « ouvert ») — jamais d'effet de
    # bord sur les objets soumis par l'appelant (idempotence des rejeux)
    items = [dict(it) for it in items]

    # ---- PASSE 0 · marquage « EN COURS » (v1.9, retour métier 01/08) --------
    # Dans chaque (véhicule, jour), la DERNIÈRE ligne du rapport n'est pas un
    # trajet terminé tant qu'une pause ≥ 20 min n'est pas constatée derrière
    # (chez CamtrackPro, le véhicule est littéralement « en mouvement » : la
    # colonne Fin = dernière position connue, PAS une heure de fin officielle).
    # Elle n'est donc NI rejetée (elle peut encore grandir) NI validée : elle
    # sera importée PROVISOIRE / EN_ATTENTE avec heure de fin provisoire, puis
    # jugée à sa clôture effective.
    groupes0: dict[tuple, list[dict]] = {}
    for it in items:
        cle = (str(it.get("vehicule_id") or it.get("gps_associe")
                   or it.get("plaque") or "").strip().upper(),
               it["debut"].date())
        groupes0.setdefault(cle, []).append(it)
    for groupe in groupes0.values():
        dernier = max(groupe, key=lambda t: (t["debut"], t.get("fin") or t["debut"]))
        fin_d = dernier.get("fin")
        if fin_d is None:
            dernier["ouvert"] = True            # pas de fin du tout → en cours
            continue
        age = (maintenant - fin_d).total_seconds()
        # tolérance ±5 min sur l'avenir (décalage d'horloge) ; au-delà, la
        # ligne est considérée comme une donnée passée normale (clôturée)
        if -300 <= age < pause_min:
            dernier["ouvert"] = True
            log.info("Ligne « en cours » détectée (%s %s) — fin PROVISOIRE %s",
                     dernier.get("gps_associe") or dernier.get("plaque")
                     or dernier.get("vehicule_id"),
                     iso(dernier["debut"]), iso(fin_d))

    # ---- PASSE 1 · FILTRAGE RÉFÉRENCE v2 (v1.18 — arbitrages du 06/08) ----
    # Une seule passe chronologique par (véhicule, jour officiel) :
    #  · ligne « en cours » (PASSE 0) → ligne PROVISOIRE propre (§6.2) ;
    #  · distance connue < seuil → manœuvre IGNORÉE (§7) : collectée en
    #    « run » consécutif pour le marquage Niveau 1 et UN audit anti-bruit,
    #    JAMAIS posée en base, ne soude ni ne prolonge rien ;
    #  · segment RÉEL (distance ≥ seuil ou inconnue) → prolonge la ligne en
    #    cours si l'écart RÉEL depuis sa fin est < 20 min (§2.2), sinon
    #    nouvelle ligne. La FIN reste la fin du dernier segment RÉEL
    #    (§5.1 : premier arrêt — les manœuvres ne la décalent jamais).

    def _cle(it_):
        # véhicule de la ligne : id DB, puis identifiant boîtier, puis plaque.
        # SANS l'id DB, les lignes du flux simulé (qui ne portent QUE
        # vehicule_id) tombaient toutes dans le MÊME groupe « plaque vide » et
        # se chaînaient entre camions (journal 13/08 : chaîne Σ 15 517 km).
        return str(it_.get("vehicule_id") or it_.get("gps_associe")
                   or it_.get("plaque") or "").strip().upper()

    marques_faits: dict[str, tuple] = {}   # suivi_id → (suivi, vehicule, jour)

    def _marquer_provisoire_rejete(cle_v: str, x: dict):
        """Marque REJETÉE la ligne Niveau 1 au MÊME début qu'une manœuvre
        officielle ignorée (Référence v2 §7) : un même début = une même
        session (géants fantômes v1.10), conservation audit §3.2 — jamais
        affichée, JAMAIS dans TCC/TCJ/TTJ. Idempotent."""
        vehicule = mapping.get(cle_v)
        if vehicule is None:
            return None
        jour = jour_attribution(x["debut"])
        if not _dans_fenetre_minuit(seuils, jour, maintenant):
            return None
        suivi = ensure_suivi(db, vehicule, jour)
        trajets = list(db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all())
        tolerance = float(seuils.get("SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET", 120))
        # rapprochement par le DÉBUT uniquement (v1.10 — jamais « par la
        # fin » : frapperait un trajet FUSIONNÉ légitime)
        cible = _trouver_provisoire(trajets, x["debut"], None, tolerance)
        if cible is None \
                or cible.statut_validation == StatutValidationTrajet.REJETE \
                or _deja_valide(trajets, x["debut"], tolerance):
            return None
        # §0quater R1 (arbitrage LSS du 14/08/2026) — VÉTO : une ligne « en
        # cours » VIVANTE (pas encore de fin) n'est JAMAIS rejetée pour cause
        # de manœuvre au même début — le verdict manœuvre/trajet se rend à la
        # clôture seulement (§5.1). Cas réel : manœuvre de dépôt 07:00→07:02
        # prolongée en vrai trajet (pause < 20 min fusionnée) alors que le
        # portail n'a publié que la manœuvre et taira le vrai trajet jusqu'à
        # sa fin. La garde anti-géants reste PLEINE sur les lignes clôturées.
        if cible.heure_fin is None:
            _audit(db, "trajet.veto_rejet_en_cours", cible.id, {
                "plaque": vehicule.plaque,
                "debut": iso(cible.heure_debut),
                "manoeuvre_officielle": iso(x["debut"]),
                "regle": "§0quater R1 (14/08/2026) : ligne « en cours » "
                         "conservée — verdict rendu à la clôture (§5.1)"})
            log.info("§0quater R1 : ligne « en cours » %s conservée malgré "
                     "la manœuvre officielle au même début", iso(cible.heure_debut))
            return None
        cible.statut_validation = StatutValidationTrajet.REJETE
        marques_faits[suivi.id] = (suivi, vehicule, jour)
        suivis_recalcules.add(suivi.id)
        return cible.id

    def _clore_run(cle_v: str, run: list[dict]) -> None:
        """Manœuvres officielles consécutives (Référence v2 §7) : marquage
        Niveau 1 au même début (garde anti-géants) + UN audit anti-bruit
        par série — JAMAIS de segment posé en base : une manœuvre ne crée
        RIEN, ne soude RIEN, ne prolonge RIEN (compteurs §2 intouchés)."""
        nonlocal rejets
        if not run:
            return
        premier_marque_id = None
        for x in run:
            rejets += 1
            mid = _marquer_provisoire_rejete(cle_v, x)
            if premier_marque_id is None and mid is not None:
                premier_marque_id = mid
        debut0 = run[0]["debut"]
        deja_note = db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "trajet.rejet_distance",
            AuditLog.details.like(f'%"{cle_v}"%'),
            AuditLog.details.like(f'%{iso(debut0)}%'))) or 0
        if not deja_note:
            fins = [x["fin"] for x in run if x.get("fin") is not None]
            dist_max = round(max((x.get("distance_km") or 0.0) for x in run), 3)
            _audit(db, "trajet.rejet_distance", premier_marque_id, {
                "plaque": cle_v, "raison": "DISTANCE_INSUFFISANTE",
                "nb_manoeuvres": len(run), "valeur": dist_max,
                "seuil_km": seuil_km, "debut": iso(debut0),
                "fin": iso(max(fins) if fins else None),
                "source": run[0].get("source"),
                "regle": "Référence v2 §7 : manœuvre < 0,3 km IGNORÉE — "
                         "jamais de ligne, JAMAIS dans TCC/TCJ/TTJ, ne soude "
                         "ni ne prolonge ni n'interrompt rien"})
        log.info("Manœuvre(s) ignorée(s) §7 (%s) : %d segment(s) < %s km, "
                 "%s→%s (%d rejet total)", cle_v, len(run), seuil_km,
                 iso(debut0), iso(run[-1].get("fin") or run[-1]["debut"]),
                 rejets)

    # ---- PASSE 2 · lignes RÉELLES (écart RÉEL < 20 min → même ligne) ----
    groupes: dict[tuple, list[dict]] = {}
    for it in items:
        groupes.setdefault((_cle(it), it["debut"].date()), []).append(it)

    retenues: list[dict] = []
    for (cle_v, jour_off), groupe in groupes.items():
        groupe.sort(key=lambda t: (t["debut"], t.get("fin") or t["debut"]))
        courante: dict | None = None      # ligne réelle en construction
        run: list[dict] = []              # manœuvres consécutives (audit)

        def _clore_ligne():
            nonlocal courante
            if courante is not None:
                retenues.append(courante)
                courante = None

        for it in groupe:
            if it.get("ouvert"):
                # ligne « en cours » : jamais filtrée ni fusionnée — elle
                # démarre sa propre ligne PROVISOIRE (v1.9 / §6.2 phase 1)
                _clore_run(cle_v, run)
                run.clear()
                _clore_ligne()
                courante = dict(it)
                continue
            dist = it.get("distance_km")
            if dist is not None and dist < seuil_km:
                run.append(it)            # manœuvre → IGNORÉE (§7)
                continue
            _clore_run(cle_v, run)
            run.clear()
            # segment RÉEL (distance ≥ seuil OU inconnue — une distance
            # absente ne vaut JAMAIS 0 km : on ne juge pas ce qu'on ne
            # mesure pas, le portail l'a publiée)
            # v3 AM-2 (arbitrage LSS 22/08/2026) : PLUS DE FUSION d'affichage
            # — chaque trajet publié par le portail garde SA ligne (début/fin
            # propres, compteurs écoconduite §0septies propres) ; l'intervalle
            # qui le sépare du précédent est un arrêt déduit du TCJ quelle
            # que soit sa durée (AM-1), affiché seulement si ≥ 30 min (AM-2).
            _clore_ligne()                # chaque trajet valide = sa ligne
            courante = dict(it)
        _clore_run(cle_v, run)
        _clore_ligne()

    # propagation unique par journée dont une ligne Niveau 1 a été marquée
    for suivi, vehicule, jour_m in marques_faits.values():
        trajets_m = list(db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all())
        _renumeroter(sorted(trajets_m, key=lambda t: t.heure_debut))
        db.flush()
        _propager(db, suivi, vehicule, maintenant,
                  jour_actif=(jour_m == jour_attribution(maintenant)))
        _synchroniser_archive(db, suivi)

    retenues.sort(key=lambda t: t["debut"])
    return retenues, rejets, suivis_recalcules


def _spliter_minuit(items: list[dict]) -> list[dict]:
    """v3 AM-3/C1 (arbitrage LSS 22/08/2026) — découpe d'ÉCRITURE des trajets
    publiés qui franchissent minuit (A au jour du début, B au lendemain)."""
    out: list[dict] = []
    for it in items:
        debut, fin = it["debut"], it.get("fin")
        if (fin is None or it.get("ouvert") or debut is None
                or fin.date() == debut.date()):
            out.append(it)
            continue
        cloture = datetime.combine(debut.date(), datetime.min.time()) \
            + timedelta(seconds=86399)
        minuit = cloture + timedelta(seconds=1)
        a = dict(it)
        a["fin"] = cloture
        b = dict(it)
        b["debut"] = minuit
        for k in ("distance_km", "duree_mouvement_s", "v_max", "ralenti_s",
                  "exc_vitesse", "exc_freinage", "exc_accel", "exc_ralenti",
                  "exc_surregime", "exc_autres"):
            b[k] = None                     # non répartissable → non mesuré
        b["suite_minuit"] = True
        out.extend([a, b])
        log.info("v3 AM-3 : trajet %s→%s franchit minuit — split A [%s→%s] / "
                 "B [%s→%s]", iso(debut), iso(fin), iso(a["debut"]),
                 iso(a["fin"]), iso(b["debut"]), iso(b["fin"]))
    return out


def reconcilier_trajets_valides(db, items: list[dict], username: str = SOURCE_SYSTEME,
                                maintenant: datetime | None = None) -> dict:
    """Algorithme §2.4 — pour chaque trajet officiel (onglet Trajets MZoneX /
    rapport CamtrackPro) : rapprocher → remplacer → valider → recalculer →
    propager ; ou créer + journaliser l'anomalie si aucun provisoire connu.
    Addendum v1.5 : pré-filtrage validité (rejet < 0,3 km, fusion < 20 min).
    v1.9 : dernière ligne du rapport « en cours » → PROVISOIRE / EN_ATTENTE
    (fin PROVISOIRE, jamais officielle) jusqu'à sa clôture effective."""
    seuils = get_seuils(db)
    tolerance = float(seuils.get("SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET", 120))
    divergence_max = float(seuils.get("SEUIL_DIVERGENCE_TRAJET", 600))
    pause_min = float(seuils.get("DUREE_MIN_PAUSE_VALIDE", 1200))
    seuil_km = float(seuils.get("SEUIL_DISTANCE_MIN_TRAJET_KM", 0.3))
    maintenant = maintenant or now_local()
    jour_actuel = jour_attribution(maintenant)

    stats = {"recus": len(items), "remplaces": 0, "crees": 0, "maj": 0,
             "ouverts": 0, "divergences": 0, "ignores": 0, "erreurs": 0,
             "rejets": 0, "epures": 0, "corrections": 0, "couverts": 0,
             "geants": 0}

    mapping: dict[str, Vehicule] = {}
    for v in db.scalars(select(Vehicule)).all():
        mapping[v.plaque.strip().upper()] = v
        mapping[str(v.id).strip().upper()] = v          # flux par vehicule_id (sim)
        if v.gps_associe:
            mapping[str(v.gps_associe).strip().upper()] = v

    # §0quater D1/D2 (arbitrage 14/08/2026) — DÉCOUVERTE AUTOMATIQUE : tout
    # identifiant véhicule du rapport inconnu en base → fiche créée
    # (plateforme = portail de la ligne, exploitation immédiate) ; conducteur
    # inconnu (colonne Conducteur MZoneX) → fiche créée aussi. Les deux sans
    # attendre une saisie manuelle — alerte ⓘ posée pour revue admin.
    for it in items:
        ident = str(it.get("gps_associe") or it.get("plaque") or "") \
            .strip().upper()
        if ident and ident not in mapping:
            v = creer_vehicule_auto(db, ident, it.get("source") or "MZONEX")
            if v is not None:
                mapping[v.plaque.strip().upper()] = v
                mapping[str(v.id).strip().upper()] = v
                if v.gps_associe:
                    mapping[str(v.gps_associe).strip().upper()] = v
        if it.get("conducteur") or it.get("badge_code"):
            # §0septies B2 (20/08/2026) : les clés de SERVICE (« Nouveau
            # conducteur », « garage LSS ») ne créent JAMAIS de fiche — la
            # saisie manuelle fait le travail sur ces lignes-là
            resoudre_badge(db, it.get("conducteur"), badge_code=it.get("badge_code"),
                           plateforme=it.get("source"))

    suivi_touches: set[str] = set()

    # v1.11 — mémo des intervalles OFFICIELS traités par journée (pour le
    # balai anti-fantômes) : ils reviennent à chaque cycle, donc le balayage
    # est auto-réparant (un fantôme purgé ne réapparaît jamais)
    officiels: dict[str, dict] = {}

    def _note(suivi, vehicule, jour, debut, fin, id_conserve) -> None:
        ctx = officiels.setdefault(suivi.id, {
            "suivi": suivi, "vehicule": vehicule, "jour": jour,
            "intervalles": [], "ids": set()})
        ctx["intervalles"].append((debut, fin))
        if id_conserve:
            ctx["ids"].add(id_conserve)

    # ----- Référence v2 §2/§5/§7 (v1.18) · pré-filtrage : manœuvres < 0,3 km
    # IGNORÉES (marquage Niveau 1 + audit) — v3 AM-2 (22/08/2026) : PLUS de
    # fusion d'affichage, chaque trajet valide publié garde SA ligne
    items, nb_rejets, suivis_rejetes = _filtrer_et_fusionner_v15(
        db, items, seuils, mapping, maintenant)
    stats["rejets"] = nb_rejets
    suivi_touches |= suivis_rejetes
    if nb_rejets:
        db.commit()

    # v3 AM-3/C1 (22/08/2026) — SPLIT minuit à l'écriture : un trajet publié
    # DÉJÀ CLÔTURÉ qui franchit minuit (début jour J, fin jour J+1) est découpé
    # en segment A [début → 23:59:59] au jour J et segment B [00:00 → fin] au
    # jour J+1. La distance publiée (mesure du trajet ENTIER) reste portée par
    # le segment A ; le segment B ne porte pas de distance (on ne juge et
    # n'invente pas ce qu'on ne mesure pas, §10/R2) ; le conducteur/badge suit
    # sur B (même trajet physique). Les trajets « ouverts » ne sont PAS
    # découpés ici : leur split arrive à la consolidation 23:59:59 (daily).
    items = _spliter_minuit(items)

    # v3 AM-3/C1 — « ouvert » À CHEVAL sur minuit : sa portion vivante est le
    # segment B du jour J+1. La veille (consolidée à 23:59:59) n'est JAMAIS
    # rouverte : on re-pointe l'ouvert sur minuit pile — son A fermé reviendra
    # quand le portail publiera la CLÔTURE (alors splittée par _spliter_minuit).
    jour_courant_attr = jour_attribution(maintenant)
    repointes = 0
    for k, it in enumerate(items):
        if it.get("ouvert") and jour_attribution(it["debut"]) < jour_courant_attr:
            it = dict(it)
            it["debut"] = datetime.combine(jour_courant_attr,
                                           datetime.min.time())
            it["suite_minuit"] = True
            items[k] = it
            repointes += 1
    if repointes:
        log.info("v3 C1 : %d ligne(s) « en cours » à cheval re-pointée(s) à "
                 "minuit (segment B au jour J+1, veille non rouverte)",
                 repointes)

    # v1.13 — index des débuts du cycle par véhicule résolu (garde « chaîne
    # en cours » : une ligne officielle ne referme un jumeau OUVERT que si
    # rien ne continue après sa fin — sinon elle est COUVERTE et reviendra
    # au cycle suivant pour refermer à la vraie fin)
    debuts_cycle: dict[str, list[datetime]] = {}
    for it0 in items:
        vid = None
        if it0.get("vehicule_id"):
            vid = str(it0["vehicule_id"]).strip().upper()
        else:
            v0 = mapping.get(str(it0.get("gps_associe") or it0.get("plaque")
                                 or "").strip().upper())
            vid = str(v0.id).upper() if v0 is not None else None
        if vid:
            debuts_cycle.setdefault(vid, []).append(it0["debut"])
    for _lst in debuts_cycle.values():
        _lst.sort()

    for it in items:
        try:
            debut, fin = it["debut"], it.get("fin")
            # v146 — GARDE D'INTÉGRITÉ : une `fin` antérieure au `debut` est
            # toujours impossible (ex. fin recopiée d'un voisin par un mauvais
            # rapprochement). On n'écrit jamais une telle heure : la ligne est
            # traitée « en cours » (fin None). Non destructif (AM-2/R2).
            if fin is not None and fin < debut:
                fin = None
            # Référence v2 §8.2/§8.3 — jour d'ATTRIBUTION du trajet (début
            # < 01h00 → veille ; la journée logistique court de 01h00 à 01h00)
            jour = jour_attribution(debut)
            if not _dans_fenetre_minuit(seuils, jour, maintenant):
                stats["ignores"] += 1
                log.info("Trajet validé %s hors fenêtre de réconciliation (%s) — ignoré",
                         iso(debut), jour)
                continue

            vehicule = None
            if it.get("vehicule_id"):
                vehicule = db.get(Vehicule, it["vehicule_id"])
            if vehicule is None:
                cle = str(it.get("gps_associe") or it.get("plaque") or "").strip().upper()
                vehicule = mapping.get(cle)
            if vehicule is None:
                stats["ignores"] += 1
                log.info("Trajet validé sans véhicule connu (%r) — ignoré",
                         it.get("gps_associe") or it.get("plaque"))
                continue

            suivi = ensure_suivi(db, vehicule, jour)
            # requête explicite : toujours l'état courant de la base, même
            # dans une session longue (expire_on_commit=False)
            trajets = list(db.scalars(select(Trajet).where(
                Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all())
            source = it["source"]
            distance = it.get("distance_km")

            # ============================================================
            # v1.9 — LIGNE « EN COURS » : pas d'heure de fin OFFICIELLE.
            # Le trajet reste PROVISOIRE / EN_ATTENTE (fin PROVISOIRE
            # affichée en italique orange à l'écran) ; il sera JUGÉ à sa
            # clôture effective et n'entre jamais définitivement dans les
            # compteurs (règle absolue §2 / retour métier 01/08).
            # ============================================================
            if it.get("ouvert"):
                garde_id = None
                cible_ouverte = _trouver_par_debut(trajets, debut, tolerance)
                if cible_ouverte is not None:
                    garde_id = cible_ouverte.id
                    # même session → même enregistrement : la fin PROVISOIRE
                    # et la distance cumulée officielle sont mises à jour ;
                    # un trajet clôturé prématurément (VALIDE/REJETE) est
                    # RÉOUVERT (donnée tardive plus complète, jamais doublon)
                    if cible_ouverte.statut_source == StatutSourceTrajet.VALIDE or \
                            cible_ouverte.statut_validation == StatutValidationTrajet.REJETE:
                        _audit(db, "trajet.reouverture", cible_ouverte.id, {
                            "plaque": vehicule.plaque, "jour": jour.isoformat(),
                            "debut": iso(debut),
                            "regle": "trajet en fait toujours en cours "
                                     "(véhicule en mouvement) → réouvert"})
                    if fin is not None:
                        cible_ouverte.heure_fin = fin
                    if distance is not None:
                        cible_ouverte.distance_km = distance
                    cible_ouverte.statut_source = StatutSourceTrajet.PROVISOIRE
                    cible_ouverte.statut_validation = StatutValidationTrajet.EN_ATTENTE
                    cible_ouverte.source_plateforme = source
                    _badge_eco(db, cible_ouverte, suivi, vehicule, it, username)
                    stats["ouverts"] += 1
                else:
                    # v3 AM-2 (22/08/2026) : PLUS de fusion de la ligne « en
                    # cours » au trajet précédent — chaque trajet publié par
                    # le portail (ou détecté en direct, C2) a SA ligne ; une
                    # reprise après un court arrêt est une nouvelle ligne
                    # orange (la pause, déduite du TCJ, n'est affichée que si
                    # ≥ 30 min).
                    if sum(1 for t in trajets if t.statut_validation
                             != StatutValidationTrajet.REJETE) >= 25:
                        # plafond grille = 25 lignes AFFICHABLES (v1.17 : les
                        # segments REJETÉS masqués n'occupent pas de colonne)
                        stats["ignores"] += 1
                        _audit(db, "trajet.valide_refuse", None, {
                            "plaque": vehicule.plaque, "jour": jour.isoformat(),
                            "debut": iso(debut), "regle": "plafond 25 trajets atteint"})
                        continue
                    else:
                        nouveau = Trajet(suivi_id=suivi.id, numero=len(trajets) + 1,
                                         heure_debut=debut, heure_fin=fin,
                                         statut_source=StatutSourceTrajet.PROVISOIRE,
                                         source_plateforme=source,
                                         distance_km=distance,
                                         statut_validation=StatutValidationTrajet.EN_ATTENTE,
                                         suite_minuit=bool(it.get("suite_minuit")))
                        db.add(nouveau)
                        trajets.append(nouveau)
                        db.flush()
                        garde_id = nouveau.id
                        _badge_eco(db, nouveau, suivi, vehicule, it, username)
                        stats["ouverts"] += 1
                        log.info("Trajet « en cours » créé (fin PROVISOIRE %s) "
                                 "— %s (%s)", iso(fin), vehicule.plaque, source)
                _note(suivi, vehicule, jour, debut, fin, garde_id)
                trajets = sorted(trajets, key=lambda t: t.heure_debut)
                _renumeroter(trajets)
                _recalculer_pauses(trajets)
                db.flush()
                _propager(db, suivi, vehicule, maintenant, jour_actif=(jour == jour_actuel))
                _synchroniser_archive(db, suivi)
                suivi_touches.add(suivi.id)
                db.commit()
                continue

            # ----- v1.9 — VALIDÉ existant au même début : idempotence, OU
            # mise à jour si la donnée officielle tardive est plus complète
            # (fin/distance étendues après une clôture prématurée) — même
            # enregistrement, jamais de doublon
            existant = _trouver_valide_par_debut(trajets, debut, tolerance)
            if existant is not None:
                delta_fin = (abs((existant.heure_fin - fin).total_seconds())
                             if (fin is not None and existant.heure_fin is not None)
                             else (0.0 if fin == existant.heure_fin else float("inf")))
                delta_dist = (abs((existant.distance_km or 0) - distance)
                              if (distance is not None and existant.distance_km is not None)
                              else 0.0)
                if delta_fin <= tolerance and delta_dist <= 0.05:
                    stats["ignores"] += 1
                    # v1.11 — la ligne officielle revient identique à chaque
                    # cycle : elle sert de vérité au balai anti-fantômes
                    _note(suivi, vehicule, jour, debut, fin, existant.id)
                    # §0septies B2/B3 — horaires identiques ne veulent pas dire
                    # badge/compteurs à jour (1ʳᵉ arrivée du badge sur une
                    # ligne stable) : application idempotente + silencieuse
                    _badge_eco(db, existant, suivi, vehicule, it, username)
                    db.commit()
                    continue
                avant_maj = {"fin": iso(existant.heure_fin),
                             "distance_km": existant.distance_km}
                if fin is not None:
                    existant.heure_fin = fin
                if distance is not None:
                    existant.distance_km = distance
                _badge_eco(db, existant, suivi, vehicule, it, username)
                stats["maj"] += 1
                _audit(db, "trajet.valide_mis_a_jour", existant.id, {
                    "plaque": vehicule.plaque, "jour": jour.isoformat(),
                    "debut": iso(debut), "avant": avant_maj,
                    "apres": {"fin": iso(fin), "distance_km": distance},
                    "source": source,
                    "regle": "donnée officielle tardive plus complète → mise à "
                             "jour du même trajet VALIDÉ (sans doublon)"})
                log.info("Trajet VALIDÉ mis à jour (donnée tardive) — %s %s",
                         vehicule.plaque, iso(debut))
                _note(suivi, vehicule, jour, debut, fin, existant.id)
                trajets = sorted(trajets, key=lambda t: t.heure_debut)
                _renumeroter(trajets)
                _recalculer_pauses(trajets)
                db.flush()
                _propager(db, suivi, vehicule, maintenant, jour_actif=(jour == jour_actuel))
                _synchroniser_archive(db, suivi)
                suivi_touches.add(suivi.id)
                db.commit()
                continue

            # ----- v1.10 — ABSORPTION PAR LA FIN : le même trajet existe déjà
            # mais la version officielle est plus COMPLÈTE (début plus tôt,
            # ex. fusion des lignes du matin lues en retard) → mise à jour du
            # MÊME enregistrement, jamais de doublon (guérison automatique
            # des données écrites par les versions défectueuses)
            if fin is not None:
                memefin = _trouver_par_fin_affiche(trajets, debut, fin, tolerance)
                # v1.11 — garde-fou : n'absorber que si le début officiel
                # reste ≤ la fin de l'enregistrement (sinon on créerait un
                # horaire corrompu « fin avant début »)
                if memefin is not None and \
                        (debut - memefin.heure_fin).total_seconds() <= tolerance:
                    avant_f = {"debut": iso(memefin.heure_debut),
                               "fin": iso(memefin.heure_fin),
                               "distance_km": memefin.distance_km}
                    memefin.heure_debut = debut
                    memefin.heure_fin = fin
                    if distance is not None:
                        memefin.distance_km = distance
                    memefin.statut_source = StatutSourceTrajet.VALIDE
                    memefin.statut_validation = StatutValidationTrajet.VALIDE
                    memefin.source_plateforme = source
                    _badge_eco(db, memefin, suivi, vehicule, it, username)
                    stats["maj"] += 1
                    _audit(db, "trajet.fusion_historique", memefin.id, {
                        "plaque": vehicule.plaque, "jour": jour.isoformat(),
                        "avant": avant_f,
                        "apres": {"debut": iso(debut), "fin": iso(fin),
                                  "distance_km": distance},
                        "source": source,
                        "regle": "même fin (± tolérance), début officiel plus "
                                 "tôt → absorption du même trajet, sans doublon"})
                    log.info("Trajet absorbé (fin officielle commune) — %s %s→%s",
                             vehicule.plaque, iso(debut), iso(fin))
                    _note(suivi, vehicule, jour, debut, fin, memefin.id)
                    trajets = sorted(trajets, key=lambda t: t.heure_debut)
                    _renumeroter(trajets)
                    _recalculer_pauses(trajets)
                    db.flush()
                    _propager(db, suivi, vehicule, maintenant,
                              jour_actif=(jour == jour_actuel))
                    _synchroniser_archive(db, suivi)
                    suivi_touches.add(suivi.id)
                    db.commit()
                    continue

            cible = _trouver_provisoire(trajets, debut, fin, tolerance)

            # v3 AM-2/C2 (22/08/2026) — la garde « chaîne en cours » est
            # SUPPRIMÉE : elle servait à fondre en une ligne des trajets
            # publiés séparés par la portail avec des reprises < 20 min. Sous
            # la nouvelle loi, chaque trajet valide affiché garde SA ligne :
            # la fin officielle referme le provisoire ouvert à son vrai début
            # (remplacement §2.4 ci-dessous), et une éventuelle reprise réelle
            # ouvre sa propre ligne orange en direct (machine 3 km/h, C2).
            # Jamais de doublon : le jumeau au même début est REMPLACÉ.

            if cible is not None:
                # ----- REMPLACEMENT (§2.4 point 2) — même enregistrement, pas de doublon
                ecart_debut = abs((cible.heure_debut - debut).total_seconds())
                ecart_fin = (abs((cible.heure_fin - fin).total_seconds())
                             if (fin and cible.heure_fin) else 0)
                ecart = max(ecart_debut, ecart_fin)
                avant = {"debut": iso(cible.heure_debut), "fin": iso(cible.heure_fin),
                         "distance_km": cible.distance_km}
                cible.heure_debut = debut
                if fin is not None:
                    cible.heure_fin = fin
                if distance is not None:
                    cible.distance_km = distance
                cible.statut_source = StatutSourceTrajet.VALIDE
                cible.source_plateforme = source
                # Addendum v1.5 §7.1 : confirmation officielle ≥ 0,3 km → VALIDE
                cible.statut_validation = StatutValidationTrajet.VALIDE
                stats["remplaces"] += 1

                if ecart > divergence_max:
                    # §4 garde-fou qualité : divergence anormale journalisée,
                    # remplacement maintenu (le VALIDÉ reste prioritaire).
                    stats["divergences"] += 1
                    _audit(db, "trajet.divergence", cible.id, {
                        "plaque": vehicule.plaque, "jour": jour.isoformat(),
                        "ecart_s": int(ecart), "provisoire": avant,
                        "valide": {"debut": iso(debut), "fin": iso(fin),
                                   "distance_km": distance},
                        "regle": "remplacement effectué malgré l'écart (§4)"})
                    log.warning("Divergence trajet %s T%s : %ds (%s)",
                                vehicule.plaque, cible.numero, ecart, jour)
            else:
                # ----- CRÉATION (cas rare §2.4 « sinon ») + anomalie auditée
                if sum(1 for t in trajets if t.statut_validation
                       != StatutValidationTrajet.REJETE) >= 25:
                    # plafond grille = 25 lignes AFFICHABLES (v1.17 : les
                    # segments REJETÉS masqués n'occupent pas de colonne)
                    stats["ignores"] += 1
                    _audit(db, "trajet.valide_refuse", None, {
                        "plaque": vehicule.plaque, "jour": jour.isoformat(),
                        "debut": iso(debut), "regle": "plafond 25 trajets atteint"})
                    continue
                nouveau = Trajet(suivi_id=suivi.id, numero=len(trajets) + 1,
                                 heure_debut=debut, heure_fin=fin,
                                 statut_source=StatutSourceTrajet.VALIDE,
                                 source_plateforme=source, distance_km=distance,
                                 statut_validation=StatutValidationTrajet.VALIDE,
                                 suite_minuit=bool(it.get("suite_minuit")))
                db.add(nouveau)
                db.flush()          # UUID généré à l'INSERT → id connu ici
                trajets.append(nouveau)
                stats["crees"] += 1
                _audit(db, "trajet.valide_sans_provisoire", nouveau.id, {
                    "plaque": vehicule.plaque, "jour": jour.isoformat(),
                    "debut": iso(debut), "fin": iso(fin), "distance_km": distance,
                    "source": source,
                    "anomalie": "Trajet validé sans équivalent provisoire "
                                "(coupure de collecte Événements ?)"})
                log.info("Trajet validé orphelin créé — %s %s (%s)",
                         vehicule.plaque, iso(debut), source)

            # ----- recalage global puis propagation (§2.4 points 4-5)
            trajets = sorted(trajets, key=lambda t: t.heure_debut)
            _renumeroter(trajets)
            _recalculer_pauses(trajets)
            db.flush()
            # v1.11 — la note suit le flush : l'id d'un NOUVEAU trajet
            # (UUID généré à l'INSERT) n'est connu qu'après celui-ci
            _note(suivi, vehicule, jour, debut, fin,
                  cible.id if cible is not None else nouveau.id)
            _badge_eco(db, cible if cible is not None else nouveau,
                       suivi, vehicule, it, username)
            _propager(db, suivi, vehicule, maintenant, jour_actif=(jour == jour_actuel))
            _synchroniser_archive(db, suivi)
            suivi_touches.add(suivi.id)
            db.commit()
        except Exception:
            db.rollback()
            stats["erreurs"] = stats.get("erreurs", 0) + 1
            log.exception("Échec réconciliation d'un trajet validé : %r",
                          {k: str(v) for k, v in it.items()})

    # ----- v1.11 — BALAI ANTI-FANTÔMES : pour chaque journée touchée, toute
    # ligne non rattachée aux intervalles officiels (doublon, sous-intervalle
    # STRICT, fin avant début) est purgée avec audit, puis pauses recalculées
    # (guérison des écritures intermédiaires des anciennes versions)
    for sid, ctx in officiels.items():
        try:
            n = _epurer_orphelins(db, ctx["suivi"], ctx["vehicule"],
                                  ctx["intervalles"], ctx["ids"], maintenant,
                                  tolerance, pause_min, ctx["jour"],
                                  jour_actuel)
            if n:
                stats["epures"] += n
            # §0nonies decies M2 (29/08/2026) — découpage souverain : les
            # géants provisoires recouverts par l'officiel sont marqués
            # REJETÉ (flag conservé), jamais supprimés
            m2 = _rejeter_geants_recouverts(
                db, ctx["suivi"], ctx["vehicule"], ctx["intervalles"],
                ctx["ids"], maintenant, tolerance, pause_min, ctx["jour"],
                jour_actuel)
            if m2:
                stats["geants"] += m2
            # v1.12 — CONTRAT GRILLE STRICT : après le balai, imposer
            # début < fin < début suivant, pauses réelles ≥ 20 min (fusion
            # §1.2), jamais de recouvrement/répétition entre lignes closes
            m = _corriger_structure(db, ctx["suivi"], ctx["vehicule"],
                                    ctx["ids"], maintenant, tolerance,
                                    pause_min, ctx["jour"], jour_actuel)
            if n or m2 or m:
                stats["corrections"] += m
                suivi_touches.add(sid)
                db.commit()
        except Exception:
            db.rollback()
            stats["erreurs"] += 1
            log.exception("Échec épuration/correction des lignes — %s",
                          ctx["vehicule"].plaque)

    # ----- v1.9 — clôture des trajets « en cours » restés sur un jour PASSÉ
    # (la fin PROVISOIRE date de plus de DUREE_MIN_PAUSE_VALIDE : fin devient
    # officielle, jugement de validité, recalcul, archive resynchronisée)
    stats["clotures"] = _clore_en_attente_obsoletes(db, maintenant, seuils)

    if suivi_touches:
        _audit(db, "trajet.sync_niveau2", None, {
            "validateur": username, **stats,
            "suivis": len(suivi_touches)})
        db.commit()
        if PUBLISH_ENABLED["on"]:
            for sid in suivi_touches:
                s = db.get(SuiviJournalier, sid)
                if s is not None:
                    publish("suivi.update", {"suivi": s_suivi(s, seuils)})

    if (stats["remplaces"] or stats["crees"] or stats["rejets"]
            or stats.get("ouverts") or stats.get("maj") or stats.get("clotures")
            or stats.get("epures") or stats.get("corrections")
            or stats.get("couverts") or stats.get("geants")):
        log.info("Réconciliation Niveau 2 : %d remplacés, %d créés, %d mis à "
                 "jour, %d « en cours », %d couverts par la chaîne en cours, "
                 "%d clôtures v1.9, %d fantômes/jumeaux purgés, %d géants M2 "
                 "rejetés, %d corrections structure, %d divergences, "
                 "%d ignorés, %d rejetés (%d reçus)",
                 stats["remplaces"], stats["crees"], stats.get("maj", 0),
                 stats.get("ouverts", 0), stats.get("couverts", 0),
                 stats.get("clotures", 0),
                 stats.get("epures", 0), stats.get("geants", 0),
                 stats.get("corrections", 0),
                 stats["divergences"], stats["ignores"], stats["rejets"],
                 stats["recus"])
    return stats


def _clore_en_attente_obsoletes(db, maintenant: datetime, seuils: dict) -> int:
    """v1.9 — clôture les trajets restés PROVISOIRE / EN_ATTENTE sur un jour
    PASSÉ (véhicule encore en mouvement en fin de journée, aucun rapport la
    veille n'ayant pu les juger). La fin PROVISOIRE date de plus de
    DUREE_MIN_PAUSE_VALIDE → pause validée → fin devient OFFICIELLE ;
    jugement de validité : ≥ SEUIL_DISTANCE_MIN_TRAJET_KM → VALIDE, sinon
    REJETE (+ audit §7.3) ; puis recalcul TCC/TCJ/TTJ et resynchronisation
    de l'archive. Distance inconnue : conservée telle quelle (prudence).
    Limité aux trajets sourcés CAMTRACKPRO : les PROVISOIRE MZoneX (Niveau 1)
    restent gérés par le moteur temps réel (réouverture/finalisation §5) et
    par la réconciliation de la veille (§3.3, fenêtre paramétrable)."""
    pause_min = float(seuils.get("DUREE_MIN_PAUSE_VALIDE", 1200))
    seuil_km = float(seuils.get("SEUIL_DISTANCE_MIN_TRAJET_KM", 0.3))
    n = 0
    candidats = db.scalars(select(Trajet).join(SuiviJournalier).where(
        SuiviJournalier.date_jour < jour_attribution(maintenant),
        Trajet.statut_validation == StatutValidationTrajet.EN_ATTENTE,
        Trajet.source_plateforme == "CAMTRACKPRO",
        Trajet.heure_fin.isnot(None))).all()
    for t in candidats:
        if (maintenant - t.heure_fin).total_seconds() < pause_min:
            continue
        suivi = db.get(SuiviJournalier, t.suivi_id)
        if suivi is None:
            continue
        vehicule = db.get(Vehicule, suivi.vehicule_id)
        if vehicule is None:
            continue
        if t.distance_km is not None and t.distance_km < seuil_km:
            t.statut_validation = StatutValidationTrajet.REJETE
            _audit(db, "trajet.rejet_distance", t.id, {
                "plaque": vehicule.plaque, "raison": "DISTANCE_INSUFFISANTE",
                "valeur": t.distance_km, "seuil": seuil_km,
                "debut": iso(t.heure_debut), "fin": iso(t.heure_fin),
                "regle": "clôture v1.9 d'un « en cours » obsolète — jamais "
                         "dans TCC/TCJ/TTJ (§2)"})
        else:
            t.statut_validation = StatutValidationTrajet.VALIDE
        t.statut_source = StatutSourceTrajet.VALIDE
        trajets = list(db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all())
        _renumeroter(sorted(trajets, key=lambda x: x.heure_debut))
        db.flush()
        _propager(db, suivi, vehicule, maintenant, jour_actif=False)
        _synchroniser_archive(db, suivi)
        n += 1
        log.info("Trajet « en cours » obsolète clôturé (fin %s → %s) — %s %s",
                 iso(t.heure_fin), t.statut_validation.value,
                 vehicule.plaque, suivi.date_jour)
    if n:
        db.commit()
    return n


def nb_trajets_provisoire_ouverts(db, jour: date) -> int:
    """Indicateur de supervision : provisoires encore non validés pour un jour."""
    return db.scalar(select(func.count(Trajet.id)).join(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour,
        Trajet.statut_source == StatutSourceTrajet.PROVISOIRE)) or 0

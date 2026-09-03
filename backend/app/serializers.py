"""Sérialisation des entités vers les réponses JSON de l'API."""
from datetime import datetime

from .chaines import (ETAT_OFFICIEL, LigneJournee, Segment,
                      construire_journee)
from .config import jour_attribution, now_local
from .models import (Alerte, Conducteur, HistoriqueJournalier, Infraction,
                     Mission, StatutValidationTrajet, SuiviJournalier, Trajet,
                     Vehicule)


def iso(dt):
    return dt.isoformat() if dt else None


def fmt_hms(secondes) -> str | None:
    """16200 -> '04:30' (affiche aussi > 24 h : '26:05')."""
    if secondes is None:
        return None
    s = int(secondes)
    signe = "-" if s < 0 else ""
    s = abs(s)
    return f"{signe}{s // 3600:02d}:{(s % 3600) // 60:02d}"


def s_conducteur(c: Conducteur | None, court=False):
    if c is None:
        return None
    d = {
        "id": c.id,
        "nom_prenom": c.nom_prenom,
        "prenom_usuel": c.prenom_usuel,
        "matricule": c.matricule,
        "nom_normalise": getattr(c, "nom_normalise", None),
        "tokens_set": getattr(c, "tokens_set", None),
        "code_badge_mzonex": getattr(c, "code_badge_mzonex", None),
        "telephone": c.telephone,
        "statut": c.statut.value if c.statut else None,
    }
    if hasattr(c, "aliases") and c.aliases:
        d["aliases"] = [{"id": a.id, "alias_brut": a.alias_brut} for a in c.aliases]
    else:
        d["aliases"] = []
    if not court:
        d["date_creation"] = iso(c.date_creation)
    return d


def s_vehicule(v: Vehicule, avec_conducteur=True):
    return {
        "id": v.id,
        "plaque": v.plaque,
        "description": v.description,
        "marque": v.marque,
        "capacite": v.capacite,
        "statut": v.statut.value if v.statut else None,
        "gps_associe": v.gps_associe,
        "plateforme_gps": getattr(v, "plateforme_gps", None) or "MZONEX",
        "conducteur_actuel_id": v.conducteur_actuel_id,
        "conducteur": s_conducteur(v.conducteur_actuel, court=True) if avec_conducteur else None,
        "date_creation": iso(v.date_creation),
        "position": {
            "lat": v.last_lat, "lng": v.last_lng,
            "vitesse": v.last_vitesse, "adresse": v.last_adresse,
            "maj": iso(v.last_event_at), "moteur": v.moteur_on,
        },
    }


def s_trajet(t: Trajet):
    return {
        "id": t.id,
        "numero": t.numero,
        "heure_debut": iso(t.heure_debut),
        "heure_fin": iso(t.heure_fin),
        "pause_apres_s": t.pause_apres_s,
        # Addendum v1.4 §3.1 — statut de fiabilité + traçabilité plateforme
        "statut_source": (t.statut_source.value if t.statut_source else "VALIDÉ"),
        "source_plateforme": t.source_plateforme,
        "distance_km": t.distance_km,
        # Addendum v1.5 §7.1 — validité métier (REJETE = jamais dans TCC/TCJ/TTJ)
        "statut_validation": (t.statut_validation.value
                              if t.statut_validation else "EN_ATTENTE"),
    }


def trajets_affichables(s: SuiviJournalier) -> list[Trajet]:
    """Addendum v1.5 §2 — les trajets REJETÉS (manœuvres < 0,3 km) sont
    conservés en base pour audit mais n'apparaissent JAMAIS dans les grilles
    (écran, historique, exports) ni dans « Nb trajets ».

    NB (v1.13) : la GRILLE n'utilise plus cette liste brute — elle affiche
    les LIGNES de la journée chaînée (`lignes_journee`) ; cette fonction
    reste pour les usages d'audit au niveau enregistrement."""
    return [t for t in (s.trajets or [])
            if t.statut_validation != StatutValidationTrajet.REJETE]


# ------------------------------------------------------------------ v1.13
def _segments_de(s: SuiviJournalier) -> list[Segment]:
    """Tous les enregistrements du suivi vus comme segments de chaîne,
    REJETÉS compris — v1.18 (Référence v2 §7) : une ligne rejetée (ex.
    manœuvre marquée au Niveau 1) n'est JAMAIS affichée ni comptée, et ne
    soude plus les lignes voisines ; son seul rôle ici = audit/exclusion."""
    return [Segment(debut=t.heure_debut, fin=t.heure_fin,
                    distance_km=t.distance_km,
                    rejete=(t.statut_validation == StatutValidationTrajet.REJETE),
                    ref=t)
            for t in (s.trajets or []) if t.heure_debut is not None]


def etat_roulage(vehicule, maintenant, seuils: dict | None = None) -> tuple[bool, object]:
    """(roule, fin_substitution) — le camion est-il EN TRAIN de bouger ?
    Signal = dernier événement GPS récent (≤ 15 min, cadence de collecte
    Niveau 1 de 7 min en production) avec vitesse > SEUIL_VITESSE_ARRET
    (3 km/h — arbitrage LSS du 14/08/2026, embouteillages : un camion qui
    avance à 5-11 km/h reste « en route » ; avant : 5 km/h le 06/08, 3 km/h
    à l'origine).
    La fin de substitution (dernier événement connu) empêche un « en cours »
    périmé de rester collé à l'écran quand le camion s'est arrêté."""
    if vehicule is None or vehicule.last_event_at is None:
        return False, None
    seuil_v = float((seuils or {}).get("SEUIL_VITESSE_ARRET", 3))
    age = (maintenant - vehicule.last_event_at).total_seconds()
    roule = age <= 900 and (vehicule.last_vitesse or 0) > seuil_v
    return roule, vehicule.last_event_at


def journee_suivi(s: SuiviJournalier, seuils: dict | None = None,
                  maintenant=None):
    """Journée chaînée du suivi (v1.13) — source UNIQUE de la grille, des
    exports et de l'archive pour la partie trajets.
    Addendum v1.8 §4 (critère CA-4) : une journée PASSÉE (jour d'attribution
    antérieur au jour logistique courant, pré-consolidée à 01h00) est 100 %
    OFFICIELLE — jamais de PROVISOIRE dans l'historique."""
    seuils = seuils or {}
    maintenant = maintenant or now_local()
    roule, fin_sub = etat_roulage(s.vehicule, maintenant, seuils)
    journee = construire_journee(
        _segments_de(s), maintenant=maintenant,
        pause_min=float(seuils.get("DUREE_MIN_PAUSE_VALIDE", 1200)),
        seuil_km=float(seuils.get("SEUIL_DISTANCE_MIN_TRAJET_KM", 0.3)),
        roule=roule, fin_substitution=fin_sub,
        pause_affichee_min=float(seuils.get("SEUIL_PAUSE_COUPURE_TCC", 1800)))
    if s.date_jour and s.date_jour < jour_attribution(maintenant):
        for lg in journee.lignes:
            lg.etat = ETAT_OFFICIEL
    return journee


def s_ligne(lg: LigneJournee, numero: int):
    """Forme JSON d'une LIGNE de grille — strictement la même structure que
    `s_trajet` (écran, exports et archive inchangés), la couleur étant portée
    par `statut_source` : VALIDÉ uniquement si la ligne est OFFICIELLE
    (trajet terminé ET pause ≥ 20 min constatée derrière) — jamais de noir
    prématuré, jamais d'orange après du noir (contrat grille strict v1.13)."""
    officielle = lg.etat == ETAT_OFFICIEL
    ref = lg.premiere_ref
    return {
        "id": getattr(ref, "id", None) or f"ligne-{numero}",
        "numero": numero,
        "heure_debut": iso(lg.debut),
        "heure_fin": iso(lg.fin),
        "pause_apres_s": lg.pause_apres_s,
        "statut_source": "PROVISOIRE" if not officielle else "VALIDÉ",
        "source_plateforme": getattr(ref, "source_plateforme", None),
        "distance_km": lg.distance_km,
        "statut_validation": "VALIDE" if officielle else "EN_ATTENTE",
        # information de transparence (modale « tous les trajets »)
        "segments": lg.nb_segments,
    }


# ------------------------------------------------------------------ §0undecies
# E1 (arbitrage LSS 24/08/2026) — FUSION D'AFFICHAGE des ruptures courtes :
# purement d'affichage (compteurs AM-1/TCC intacts, E2/F1), idempotente,
# jamais de mutation du stockage ni des snapshots d'archive (§A.2 préservé :
# aucune réécriture — les archives antérieures s'affichent avec la même règle).
# §0tricies decies G1/G2 (arbitrage LSS 25/08/2026) — E3 ABROGÉE : le seuil de
# fusion d'affichage passe de 20 à 30 min. < 30 min → UNE ligne (pas de case
# pause) ; ≥ 30 min → deux lignes + pause affichée. Le réglage vit dans les
# seuils (`SEUIL_FUSION_AFFICHAGE_S`) ; la constante ci-dessous est le repli
# utilisé aussi pour la relecture des archives (sans session/seuils à portée).
FUSION_AFFICHAGE_S = 1800.0    # §0tricies decies G1 : rupture < 30 min → UNE ligne
# Seuil d'affichage des pauses : 30 min, inchangé (`SEUIL_PAUSE_COUPURE_TCC`)
PAUSE_AFFICHAGE_S = 1800.0


def _dt_iso(texte) -> datetime | None:
    if not texte:
        return None
    try:
        return datetime.fromisoformat(str(texte))
    except ValueError:
        return None


def fusionner_trajets_affichage(trajets, seuil_fusion_s: float = FUSION_AFFICHAGE_S,
                                seuil_pause_aff_s: float = PAUSE_AFFICHAGE_S) -> list[dict]:
    """§0undecies E1, amendée §0tricies decies G1/G2 (E3 abrogée le 25/08/2026)
    — deux lignes séparées par un arrêt STRICTEMENT < `seuil_fusion_s`
    (désormais 30 min) sont affichées comme UNE seule ligne (« sans bonder
    les colonnes ») :

    début = début de la 1re composante ; fin = fin de la dernière (vide si
    en cours) ; statut/couleur = ceux de la dernière composante ; distance =
    somme ; segments = somme ; numérotation re-séquencée. Les pauses sont
    RECALCULÉES exactement depuis les heures (l'écart court d'une ligne
    fusionnée disparaît de l'affichage ; celui ≥ seuil reste affiché s'il
    atteint `seuil_pause_aff_s` — G2 : 30 min inchangé, automatiquement
    cohérent puisque deux lignes affichées séparées sont ≥ seuil de fusion).

    Entrée : lignes déjà sérialisées (`s_ligne` / snapshots d'archive).
    Sortie : NOUVEAUX dicts — aucune mutation de l'entrée. Idempotente.
    """
    res: list[dict] = []
    for t in (trajets or []):
        nt = dict(t)
        deb, fin = _dt_iso(nt.get("heure_debut")), _dt_iso(nt.get("heure_fin"))
        if (res and deb is not None and res[-1]["_fin_dt"] is not None
                and (deb - res[-1]["_fin_dt"]).total_seconds() < seuil_fusion_s):
            # rupture courte → absorbée dans la ligne précédente
            m = res[-1]
            m["heure_fin"] = nt.get("heure_fin")
            m["_fin_dt"] = fin
            m["statut_source"] = nt.get("statut_source")
            m["statut_validation"] = nt.get("statut_validation")
            d1, d2 = m.get("distance_km"), nt.get("distance_km")
            m["distance_km"] = (None if d1 is None and d2 is None
                                else round((d1 or 0) + (d2 or 0), 3))
            m["segments"] = (m.get("segments") or 1) + (nt.get("segments") or 1)
        else:
            nt["_deb_dt"], nt["_fin_dt"] = deb, fin
            res.append(nt)
    for i, m in enumerate(res):
        if i + 1 < len(res):
            brut = (max(0, int((res[i + 1]["_deb_dt"] - m["_fin_dt"]).total_seconds()))
                    if m["_fin_dt"] is not None and res[i + 1]["_deb_dt"] is not None
                    else 0)
            m["pause_apres_s"] = brut if brut >= seuil_pause_aff_s else 0
        elif m["_fin_dt"] is not None:
            # dernière ligne clôturée : pas de pause après la fin de journée
            m["pause_apres_s"] = 0
    for i, m in enumerate(res, start=1):
        m["numero"] = i
        m.pop("_deb_dt", None)
        m.pop("_fin_dt", None)
    return res


def fusionner_snapshot(donnees: dict | None) -> dict:
    """Copie d'un snapshot (s_suivi / archive) avec trajets fusionnés E1 —
    jamais de mutation de `donnees` (les archives ne sont pas réécrites).
    §0vicies decies N1 (31/08/2026) : un snapshot est par construction une
    journée TERMINÉE → le TCC y est masqué (« 0:00 » ; chrono temps réel sans
    sens une fois la journée closes). Copie seulement : la valeur interne
    reste stockée dans l'archive pour la contre-vérification (§A.2 respecté,
    jamais de réécriture)."""
    d = dict(donnees or {})
    bruts = d.get("trajets") or []
    if bruts:
        fusionnes = fusionner_trajets_affichage(bruts)
        d["trajets"] = fusionnes
        d["nb_trajets"] = len(fusionnes)
    d["tcc_s"] = 0   # N1 — TCC masqué hors temps réel (cellule « 0:00 »)
    return d


def s_suivi(s: SuiviJournalier, seuils: dict | None = None):
    seuils = seuils or {}
    journee = journee_suivi(s, seuils)
    # §0undecies E1 (24/08/2026), amendée §0tricies decies G1 (25/08/2026) —
    # fusion d'AFFICHAGE des ruptures < 30 min (E3 abrogée : plus de bande
    # 20-29 min à pause masquée ; DUREE_MIN_PAUSE_VALIDE garde ses rôles
    # MOTEUR à 20 min — couleur orange/noir, réouverture R1/F2, ingestion).
    lignes_json = fusionner_trajets_affichage(
        [s_ligne(lg, i) for i, lg in enumerate(journee.lignes, start=1)],
        seuil_fusion_s=float(seuils.get("SEUIL_FUSION_AFFICHAGE_S",
                                        FUSION_AFFICHAGE_S)),
        seuil_pause_aff_s=float(seuils.get("SEUIL_PAUSE_COUPURE_TCC",
                                           PAUSE_AFFICHAGE_S)))
    tcc_max = seuils.get("SEUIL_TCC_MAX", 16200)
    tcj_max = seuils.get("SEUIL_TCJ_MAX", 36000)
    ttj_max = seuils.get("SEUIL_TTJ_MAX", 43200)
    return {
        "id": s.id,
        "date_jour": s.date_jour.isoformat(),
        # Partie A
        "vehicule_id": s.vehicule_id,
        "plaque": s.vehicule.plaque if s.vehicule else None,
        "description": s.vehicule.description if s.vehicule else None,
        "conducteur_id": s.conducteur_id,
        "conducteur": s_conducteur(s.conducteur, court=True),
        # Partie B
        "situation": s.situation,
        "statut_camion": s.statut_camion.value if s.statut_camion else None,
        "depot_recepteur": s.depot_recepteur,
        "distributeur": s.distributeur,
        "produit": s.produit,
        "numero_ot": s.numero_ot,
        # Partie C
        "emplacement_j_moins_1": s.emplacement_j_moins_1,
        "position_08h": s.position_08h, "position_10h": s.position_10h,
        "position_12h": s.position_12h, "position_14h": s.position_14h,
        "position_16h": s.position_16h, "position_18h": s.position_18h,
        # §0vicies decies N2 (31/08/2026) — relevés automatiques du soir
        "position_20h": s.position_20h, "position_22h": s.position_22h,
        # Partie D
        "heure_depart": iso(s.heure_depart),
        "arret_final": s.arret_final,
        # §0nonies decies M4 (29/08/2026) — repère « boîtier muet — données en
        # transit » : âge du dernier signal GPS en secondes (None si inconnu) ;
        # l'écran badge orange dès SEUIL_GPS_HORS_LIGNE (30 min) dépassé.
        "gps_age_s": (int((now_local() - s.vehicule.last_event_at)
                          .total_seconds())
                      if (s.vehicule and s.vehicule.last_event_at) else None),
        # Addendum v1.9 §4.2 — colonne « Lieu Arrêt » : lieu du dernier arrêt
        # (journée en cours = dernière position connue ; figée à l'archivage)
        "lieu_arret": (s.vehicule.last_adresse if s.vehicule else None),
        "tcc_s": s.tcc_s, "tcj_s": s.tcj_s, "ttj_s": s.ttj_s,
        "total_pause_s": s.total_pause_s,
        "km_parcourus": round(s.km_parcourus or 0, 1),
        # v1.13 — CONTRAT GRILLE STRICT : la grille affiche les LIGNES de la
        # journée chaînée — écran = export = archive, une seule source.
        # §0undecies E1 (24/08/2026) : fusion d'AFFICHAGE des ruptures < 20 min
        # (« sans bonder les colonnes » ; compteurs AM-1/TCC intacts, E2).
        "trajets": lignes_json,
        "nb_trajets": len(lignes_json),
        "mission_id": s.mission_id,
        # drapeaux de dépassement (pour coloration frontend)
        "flag_tcc": bool(s.tcc_s and s.tcc_s > tcc_max),
        "flag_tcj": bool(s.tcj_s and s.tcj_s > tcj_max),
        "flag_ttj": bool(s.ttj_s and s.ttj_s > ttj_max),
        "updated_at": iso(s.updated_at),
    }


def s_mission(m: Mission):
    return {
        "id": m.id,
        "date_jour": m.date_jour.isoformat(),
        "conducteur_id": m.conducteur_id,
        "conducteur": s_conducteur(m.conducteur, court=True),
        "vehicule_id": m.vehicule_id,
        "plaque": m.vehicule.plaque if m.vehicule else None,
        "numero_mission_du_jour": m.numero_mission_du_jour,
        "statut": m.statut.value if m.statut else None,
        "heure_debut": iso(m.heure_debut),
        "heure_fin": iso(m.heure_fin),
        "duree_s": m.duree_s,
        "numero_ot": m.numero_ot,
        "produit": m.produit,
        "depot": m.depot,
        "distributeur": m.distributeur,
        "kilometrage": round(m.kilometrage or 0, 1),
        "origine": m.origine,
        "etapes": m.etapes or [],
    }


_TYPES_INF_LIB = {
    "EXCES_VITESSE": "Excès de vitesse",
    "ACCELERATION_BRUSQUE": "Accélération brusque",
    "FREINAGE_BRUSQUE": "Freinage brusque",
    "DEPASSEMENT_TCC": "Dépassement TCC",
    "DEPASSEMENT_TCJ": "Dépassement TCJ",
    "DEPASSEMENT_TTJ": "Dépassement TTJ",
}


def _seuil_libelle(i: Infraction) -> str | None:
    """§0quinquies decies I3 + I5 (exécution 26/08/2026) — la colonne
    « Seuil » porte le seuil de référence quelle que soit la famille :
    limite « 90 km/h » (vitesse), borne horaire « 04:30 » (durées),
    ou seuil BRUT VERBATIM du portail (« 18:00:00 to 05:30:00 » pour la
    conduite de nuit, « 6.00 » indice d'accélération) — jamais d'unité
    inventée."""
    if i.seuil_unite == "brut":
        if i.seuil_texte:
            return i.seuil_texte[:80]
        if i.seuil_reference is None:
            return None
        brut = f"{i.seuil_reference:g}"
        return brut
    if i.seuil_reference is None:
        return None
    if i.seuil_unite == "kmh":
        return f"{i.seuil_reference:.0f} km/h"
    return fmt_hms(i.seuil_reference)


def s_infraction(i: Infraction):
    # §0quinquies decies I3/I4 : nom publié Ym@ne, niveau, seuil libellé,
    # coordonnées GPS (vitesse), workflow de validation complet.
    type_v = i.type.value if i.type else None
    nom = i.nom_ymane or _TYPES_INF_LIB.get(type_v, type_v)
    chauffeur = (i.conducteur.nom_prenom if i.conducteur else None) \
        or i.chauffeur_brut
    coords = (f"{i.latitude:.5f}, {i.longitude:.5f}"
              if i.latitude is not None and i.longitude is not None else None)
    return {
        "id": i.id,
        "date_jour": i.date_jour.isoformat(),
        "heure": i.heure.strftime("%H:%M:%S") if i.heure else None,
        # §0octies decies L2 (27/08/2026) — fin verbatim Ym@ne (peut être
        # au J+1) ; None → « — » à l'écran, jamais d'invention.
        "date_fin": i.date_fin.isoformat() if i.date_fin else None,
        "heure_fin": i.heure_fin.strftime("%H:%M:%S") if i.heure_fin else None,
        "conducteur_id": i.conducteur_id,
        "conducteur": s_conducteur(i.conducteur, court=True),
        "chauffeur_affiche": chauffeur,
        "vehicule_id": i.vehicule_id,
        "plaque": i.vehicule.plaque if i.vehicule else None,
        "type": type_v,
        "gravite": i.gravite.value if i.gravite else None,
        "duree_s": i.duree_s,
        "valeur_mesuree": i.valeur_mesuree,
        "seuil_reference": i.seuil_reference,
        "mission_id": i.mission_id,
        "source": i.source.value if i.source else None,
        "latitude": i.latitude, "longitude": i.longitude,
        "adresse": i.adresse,
        # --- I3/I4 (v1.35)
        "nom": nom,
        "niveau": i.niveau,
        "seuil_unite": i.seuil_unite,
        "seuil_texte": i.seuil_texte,
        "seuil_libelle": _seuil_libelle(i),
        "coordonnees": coords,
        "validation": i.validation or "NON_TRAITEE",
        "observation": i.observation,
        "validee_par": i.validee_par,
        "validee_le": iso(i.validee_le),
        "ymane_id": i.ymane_id,
    }


def s_alerte(a: Alerte):
    return {
        "id": a.id,
        "date_heure": iso(a.date_heure),
        "type": a.type.value if a.type else None,
        "gravite": a.gravite.value if a.gravite else None,
        "vehicule_id": a.vehicule_id,
        "plaque": a.vehicule.plaque if a.vehicule else None,
        "conducteur_id": a.conducteur_id,
        "conducteur": s_conducteur(a.conducteur, court=True),
        "message": a.message,
        "statut": a.statut.value if a.statut else None,
        "lien_module": a.lien_module,
        "infraction_id": a.infraction_id,
    }


def s_historique(h: HistoriqueJournalier, detail=False):
    # §0undecies E1 — la MÊME fusion d'affichage s'applique aux snapshots
    # d'archive (dont ceux d'avant v1.31, stockés non fusionnés) : copie,
    # jamais de mutation ; aucune archive n'est réécrite (§A.2).
    d = fusionner_snapshot(h.donnees)
    out = {
        "id": h.id,
        "date_jour": h.date_jour.isoformat(),
        "annee": h.annee, "mois": h.mois,
        "vehicule_id": h.vehicule_id,
        "plaque": h.vehicule.plaque if h.vehicule else d.get("plaque"),
        "conducteur": s_conducteur(h.conducteur, court=True) if h.conducteur else d.get("conducteur"),
        "situation": d.get("situation"),
        "statut_camion": d.get("statut_camion"),
        "depot_recepteur": d.get("depot_recepteur"),
        "produit": d.get("produit"),
        "numero_ot": d.get("numero_ot"),
        "heure_depart": d.get("heure_depart"),
        "arret_final": d.get("arret_final"),
        "km_parcourus": d.get("km_parcourus"),
        "tcc_s": d.get("tcc_s"), "tcj_s": d.get("tcj_s"), "ttj_s": d.get("ttj_s"),
        "nb_trajets": d.get("nb_trajets"),
        "nb_infractions": h.nb_infractions,
        "nb_alertes": h.nb_alertes,
        "archive_le": iso(h.archive_le),
    }
    if detail:
        out["donnees"] = d
    return out

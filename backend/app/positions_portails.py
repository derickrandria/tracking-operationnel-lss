"""§0unvicies decies O1 (arbitrage LSS du 01/09/2026, mandant) — Positions
EXACTES 18h/20h/22h lues dans l'HISTORIQUE DES PORTAILS.

Pourquoi : le serveur étant souvent éteint le soir, la base locale ne contient
aucun événement GPS après ~17 h ; le rattrapage v1.44 répétait alors le même
point (avant 18 h) dans les trois cases. Les portails, eux, gardent TOUT :
les vraies positions de la soirée y sont — y compris pour les boîtiers « muets »
qui remontent leur tampon en retard.

Sources (lecture seule, aucune écriture portail) :
  • MZoneX      : ensemble OData ``Events`` (fil horodaté à la seconde) ;
  • CamtrackPro : messages Wialon (``messages/load_interval`` +
                  ``messages/get_messages``) — seuls les messages AVEC position
                  sont retenus.

Point retenu par heure H ∈ {18, 20, 22} : le DERNIER point horodaté
strictement AVANT ou À H pile (sémantique exacte du portail, instruction
exploitant déjà inscrite §0vicies decies N2 : « le camion est affiché là où
il est à ce moment, même si le boîtier n'émet pas » — un camion arrêté à
19h30 affiche à 20h et 22h le même point de 19h30 : c'est la vérité terrain).

Aucune exception ne remonte à l'appelant (§10) : un portail en panne prive
simplement sa flotte de valeurs pour ce passage (repli base locale côté
appelant, nouvelle tentative au cycle suivant).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

from .api_mzonex import ApiMZoneX, depuis_utc, plaque_depuis_ligne_api
from .api_wialon import ApiWialon
from .config import TZ, plaque_depuis_libelle_portail

log = logging.getLogger("lss.positions_portails")

HEURES = (18, 20, 22)
ACTIVE = os.getenv("POSITIONS_PORTAILS_ACTIVE", "1") == "1"
# §0unvicies decies O1 — fenêtre MZoneX : [04h00 → 22h30 locales] (volumétrie
# mesurée en direct le 01/09 : ~4 400 événements pour 18 h de fenêtre, loin du
# plafond de pagination 9 000). Un point avant 04h00 (boîtier muet depuis la
# veille) relève du repli base locale « sans limite d'âge » (N2 v1.44).
_MZ_DEBUT_H, _MZ_FIN_H, _MZ_FIN_MIN = 4, 22, 30
# Wialon : journée locale complète [00:00 → 23:30] — la pagination ci-dessous
# absorbe les camions très bavards (~7 000 messages jours mesurés).
_PAGE_MSG = 2000
_DELAI_REQUETE_S = 0.15       # politesse inter-appels (aucune rafale)


def _bornes_local_utc(jour: date) -> dict[int, float]:
    """Epoch UTC de chaque borne H:00:00 LOCALE du jour donné."""
    return {h: datetime.combine(jour, dtime(h, 0), tzinfo=TZ).timestamp()
            for h in HEURES}


def _derniers_avant(series: list[tuple[float, float, float]],
                    bornes: dict[int, float]) -> dict[int, tuple[float, float]]:
    """series = [(t_epoch_utc, lat, lng) triée croissant] → par heure, le
    dernier point ≤ la borne (O1). Retourne {heure: (lat, lng)}."""
    out: dict[int, tuple[float, float]] = {}
    for h, b in bornes.items():
        meilleur: tuple[float, float] | None = None
        for t, lat, lng in series:
            if t > b:
                break                      # série triée : inutile de poursuivre
            meilleur = (lat, lng)
        if meilleur is not None:
            out[h] = meilleur
    return out


# ------------------------------------------------------------------ CamtrackPro
def _positions_wialon(jour: date) -> dict[str, dict[int, tuple[float, float]]]:
    """Derniers messages positionnés avant 18h/20h/22h pour chaque unité
    CamtrackPro (Wialon). {plaque: {heure: (lat, lng)}}. Lève une exception
    si le portail est injoignable (l'appelant bascule en dégradé v1.44)."""
    bornes = _bornes_local_utc(jour)
    t0 = int(datetime.combine(jour, dtime(0, 0), tzinfo=TZ).timestamp())
    t1 = int(datetime.combine(jour, dtime(23, 30), tzinfo=TZ).timestamp())
    api = ApiWialon()
    api.connecter()
    try:
        unites = api.unites()
        resultat: dict[str, dict[int, tuple[float, float]]] = {}
        for u in unites:
            plaque = plaque_depuis_libelle_portail(str(u.get("nm") or ""))
            if not plaque:
                continue
            r = api._appel("messages/load_interval", {
                "itemId": int(u["id"]), "timeFrom": t0, "timeTo": t1,
                "flags": 0, "flagsMask": 0xFF00, "loadCount": 0})
            total = int((r or {}).get("count", 0) or 0)
            series: list[tuple[float, float, float]] = []
            saute = 0
            while saute < total:
                lot = api._appel("messages/get_messages", {
                    "indexFrom": saute,
                    "indexTo": min(total, saute + _PAGE_MSG)})
                msgs = lot if isinstance(lot, list) else lot.get("messages", [])
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    pos = m.get("pos")
                    if pos and pos.get("y") is not None and pos.get("x") is not None:
                        series.append((float(m.get("t", 0)),
                                       float(pos["y"]), float(pos["x"])))
                if not msgs:
                    break
                saute += _PAGE_MSG
                time.sleep(_DELAI_REQUETE_S)
            series.sort(key=lambda t: t[0])
            trouve = _derniers_avant(series, bornes)
            if trouve:
                resultat[plaque] = trouve
            time.sleep(_DELAI_REQUETE_S)
        return resultat
    finally:
        api.fermer()


# ------------------------------------------------------------------ MZoneX
def _positions_mzonex(jour: date) -> dict[str, dict[int, tuple[float, float]]]:
    """Derniers événements avant 18h/20h/22h pour chaque véhicule MZoneX.
    {plaque: {heure: (lat, lng)}}. Même contrat d'exception que Wialon."""
    bornes = _bornes_local_utc(jour)
    api = ApiMZoneX()
    vehs = api._pages("Vehicles?$orderby=description")
    guid_plaque = {}
    for v in vehs:
        plaque = plaque_depuis_ligne_api(str(v.get("description") or ""))
        if plaque and v.get("id"):
            guid_plaque[str(v["id"])] = plaque
    debut_utc = datetime.combine(jour, dtime(_MZ_DEBUT_H, 0), tzinfo=TZ) \
        .astimezone(timezone.utc).replace(tzinfo=None)
    fin_utc = datetime.combine(jour, dtime(_MZ_FIN_H, _MZ_FIN_MIN), tzinfo=TZ) \
        .astimezone(timezone.utc).replace(tzinfo=None)
    evs = api.evenements(debut_utc, fin_utc)
    series_par_plaque: dict[str, list[tuple[float, float, float]]] = {}
    for e in evs:
        plaque = guid_plaque.get(str(e.get("vehicle_Id")))
        if not plaque:
            continue
        lat, lng = e.get("latitude"), e.get("longitude")
        if lat is None or lng is None:
            continue
        dt_loc = depuis_utc(str(e.get("utcTimestamp") or ""))
        if dt_loc is None:
            continue
        ts = dt_loc.replace(tzinfo=TZ).timestamp()
        series_par_plaque.setdefault(plaque, []).append(
            (ts, float(lat), float(lng)))
    resultat: dict[str, dict[int, tuple[float, float]]] = {}
    for plaque, series in series_par_plaque.items():
        series.sort(key=lambda t: t[0])
        trouve = _derniers_avant(series, bornes)
        if trouve:
            resultat[plaque] = trouve
    return resultat


# ------------------------------------------------------------------ union
def positions_du_jour(jour: date) -> dict[str, dict[int, tuple[float, float]]]:
    """Union des deux portails : {plaque: {18/20/22: (lat, lng)}}.

    Un portail en panne (§10) n'empêche pas l'autre ; si LES DEUX échouent,
    une exception remonte pour que l'appelant bascule en dégradé (rattrapage
    v1.44, base locale) et réessaie au cycle suivant.
    """
    if not ACTIVE:
        return {}
    donnees: dict[str, dict[int, tuple[float, float]]] = {}
    erreurs = 0
    for source, nom in ((_positions_mzonex, "MZoneX"),
                        (_positions_wialon, "CamtrackPro")):
        try:
            lot = source(jour)
            for plaque, par_heure in lot.items():
                donnees.setdefault(plaque, {}).update(par_heure)
            log.info("Positions portails (%s, %s) : %d plaque(s) servie(s)",
                     nom, jour.isoformat(), len(lot))
        except Exception:
            erreurs += 1
            log.exception("Positions portails (%s, %s) en échec — l'autre "
                          "portail et le repli base prennent le relais",
                          nom, jour.isoformat())
    if erreurs == 2:
        raise RuntimeError("positions portails : les DEUX portails sont "
                           "injoignables pour " + jour.isoformat())
    return donnees

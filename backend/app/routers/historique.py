"""Module 6 — Historique : archives mensuelles, recherche, exports, stats.
Addendum v1.1 §4 — sous-onglet Historique Suivi Journalier : plage de dates
(Du/Au), exports multi-jours au format strict du Suivi Journalier."""
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import now_local
from ..database import get_db
from ..exporters import export_excel, export_pdf
from ..models import (Alerte, Conducteur, HistoriqueJournalier, Infraction,
                      Vehicule)
from ..security import TOUS, audit, require_roles
from ..serializers import (fmt_hms, fusionner_snapshot, s_conducteur,
                           s_historique)

router = APIRouter(prefix="/api/historique", tags=["historique"])


@router.get("/mois")
def mois_disponibles(db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    """Liste des mois archivés + mois en cours."""
    mois = set()
    for a, m in db.execute(select(HistoriqueJournalier.annee, HistoriqueJournalier.mois)
                           .distinct()).all():
        mois.add((a, m))
    auj = now_local().date()
    mois.add((auj.year, auj.month))
    return {"mois": [{"annee": a, "mois": m, "label": f"{m:02d}/{a}"}
                     for a, m in sorted(mois, reverse=True)]}


@router.get("")
def liste_historique(annee: int | None = None, mois: int | None = None,
                     q: str | None = None, vehicule_id: str | None = None,
                     db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    auj = now_local().date()
    annee = annee or auj.year
    mois = mois or auj.month
    query = select(HistoriqueJournalier).where(
        HistoriqueJournalier.annee == annee, HistoriqueJournalier.mois == mois)
    if vehicule_id:
        query = query.where(HistoriqueJournalier.vehicule_id == vehicule_id)
    query = query.join(Vehicule, HistoriqueJournalier.vehicule_id == Vehicule.id)
    if q:
        motif = f"%{q.strip()}%"
        query = query.outerjoin(Conducteur, HistoriqueJournalier.conducteur_id == Conducteur.id) \
            .where(or_(Vehicule.plaque.ilike(motif),
                       Conducteur.nom_prenom.ilike(motif),
                       Conducteur.prenom_usuel.ilike(motif)))
    items = db.scalars(query.order_by(HistoriqueJournalier.date_jour.desc(),
                                      Vehicule.plaque)).all()
    return {"annee": annee, "mois": mois, "total": len(items),
            "items": [s_historique(h) for h in items]}


@router.get("/detail/{hid}")
def detail_historique(hid: str, db: Session = Depends(get_db),
                      _=Depends(require_roles(*TOUS))):
    h = db.get(HistoriqueJournalier, hid)
    if h is None:
        raise HTTPException(404, "Archive introuvable")
    # infractions et alertes liées au même jour (§5.7)
    infractions = db.scalars(select(Infraction).where(
        Infraction.date_jour == h.date_jour, Infraction.vehicule_id == h.vehicule_id)).all()
    from ..serializers import s_infraction
    return {**s_historique(h, detail=True),
            "infractions": [s_infraction(i) for i in infractions]}


def _periode(du: str | None, au: str | None) -> tuple[date, date]:
    """Valide la plage Du/Au (§4.3 : « Au » ne peut pas précéder « Du » ; max 366 j)."""
    if not du or not au:
        raise HTTPException(422, "Paramètres 'du' et 'au' obligatoires (AAAA-MM-JJ).")
    d1, d2 = date.fromisoformat(du), date.fromisoformat(au)
    if d2 < d1:
        raise HTTPException(422, "La date « Au » ne peut pas être antérieure à « Du ».")
    if (d2 - d1).days > 366:
        raise HTTPException(422, "Plage trop large (maximum 366 jours).")
    return d1, d2


def _archives_periode(db: Session, d1: date, d2: date, q: str | None,
                      vehicule_id: str | None = None):
    query = select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour >= d1, HistoriqueJournalier.date_jour <= d2)
    if vehicule_id:
        query = query.where(HistoriqueJournalier.vehicule_id == vehicule_id)
    query = query.join(Vehicule, HistoriqueJournalier.vehicule_id == Vehicule.id)
    if q and q.strip():
        motif = f"%{q.strip()}%"
        query = query.outerjoin(Conducteur, HistoriqueJournalier.conducteur_id == Conducteur.id) \
            .where(or_(Vehicule.plaque.ilike(motif),
                       Conducteur.nom_prenom.ilike(motif),
                       Conducteur.prenom_usuel.ilike(motif)))
    return db.scalars(query.order_by(HistoriqueJournalier.date_jour, Vehicule.plaque)).all()


@router.get("/suivi")
def historique_suivi_journalier(du: str, au: str, q: str | None = None,
                                vehicule_id: str | None = None,
                                detail: bool = False,
                                db: Session = Depends(get_db),
                                _=Depends(require_roles(*TOUS))):
    """Addendum v1.1 §4.3 — archives du Suivi Journalier sur plage de dates.
    `detail=1` : inclut le snapshot complet (donnees — trajets 1→9+, parties
    A/B/C/D) pour afficher la GRILLE IDENTIQUE à l'onglet Suivi Journalier
    lorsqu'une journée précise est sélectionnée (exigence audit « 27 colonnes »)."""
    d1, d2 = _periode(du, au)
    items = _archives_periode(db, d1, d2, q, vehicule_id)
    return {"du": d1.isoformat(), "au": d2.isoformat(), "total": len(items),
            "items": [s_historique(h, detail=detail) for h in items]}


@router.get("/stats")
def stats_mensuelles(annee: int | None = None, mois: int | None = None,
                     du: str | None = None, au: str | None = None,
                     db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    """Récapitulatif KPI sur la période sélectionnée — mois (§6.6) ou plage
    de dates Du/Au (Addendum v1.1 §4.3 : recalcul automatique sur la période)."""
    auj = now_local().date()
    if du and au:
        debut, d2 = _periode(du, au)
        fin = d2 + timedelta(days=1)
        annee, mois = debut.year, debut.month
        archives = db.scalars(select(HistoriqueJournalier).where(
            HistoriqueJournalier.date_jour >= debut,
            HistoriqueJournalier.date_jour < fin)).all()
    else:
        annee = annee or auj.year
        mois = mois or auj.month
        debut = date(annee, mois, 1)
        fin = date(annee + (mois == 12), (mois % 12) + 1, 1)
        archives = db.scalars(select(HistoriqueJournalier).where(
            HistoriqueJournalier.annee == annee, HistoriqueJournalier.mois == mois)).all()
    nb_jours = len({h.date_jour for h in archives})
    km_total = round(sum((h.donnees or {}).get("km_parcourus") or 0 for h in archives), 1)
    tcjs = [(h.donnees or {}).get("tcj_s") for h in archives]
    tcjs = [t for t in tcjs if t]
    tcj_moyen = int(sum(tcjs) / len(tcjs)) if tcjs else 0
    pauses = [(h.donnees or {}).get("total_pause_s") for h in archives]
    pauses = [p for p in pauses if p]
    pause_moyenne = int(sum(pauses) / len(pauses)) if pauses else 0

    depots = defaultdict(int)
    produits = defaultdict(int)
    km_par_chauffeur = defaultdict(float)
    for h in archives:
        d = h.donnees or {}
        if d.get("depot_recepteur"):
            depots[d["depot_recepteur"]] += 1
        if d.get("produit"):
            produits[d["produit"]] += 1
        if h.conducteur_id:
            km_par_chauffeur[h.conducteur_id] += d.get("km_parcourus") or 0

    # infractions / alertes du mois (tables vivantes indexées par date)
    infractions = db.scalars(select(Infraction).where(
        Infraction.date_jour >= debut, Infraction.date_jour < fin)).all()
    par_type = defaultdict(int)
    par_gravite_inf = defaultdict(int)
    inf_par_chauffeur = defaultdict(int)
    for i in infractions:
        par_type[i.type.value] += 1
        par_gravite_inf[i.gravite.value] += 1
        if i.conducteur_id:
            inf_par_chauffeur[i.conducteur_id] += 1

    alertes_par_gravite = dict(db.execute(
        select(Alerte.gravite, func.count(Alerte.id)).where(
            Alerte.date_heure >= debut, Alerte.date_heure < fin)
        .group_by(Alerte.gravite)).all())
    alertes_par_gravite = {k.value if hasattr(k, "value") else k: v
                           for k, v in alertes_par_gravite.items()}

    candidats = [(cid, km) for cid, km in km_par_chauffeur.items() if km > 5]
    candidats.sort(key=lambda t: (inf_par_chauffeur.get(t[0], 0), -t[1]))
    top_chauffeurs = []
    for cid, km in candidats[:5]:
        c = db.get(Conducteur, cid)
        if c:
            top_chauffeurs.append({"prenom_usuel": c.prenom_usuel, "km": round(km, 1),
                                   "infractions": inf_par_chauffeur.get(cid, 0)})

    return {
        "annee": annee, "mois": mois,
        "periode": {"du": debut.isoformat(), "au": (fin - timedelta(days=1)).isoformat()},
        "nb_jours_archives": nb_jours,
        "nb_lignes": len(archives),
        "km_total": km_total,
        "tcj_moyen_s": tcj_moyen,
        "pause_moyenne_s": pause_moyenne,
        "infractions_total": len(infractions),
        "infractions_par_type": [{"label": k, "value": v} for k, v in par_type.items()],
        "infractions_par_gravite": [{"label": k, "value": v} for k, v in par_gravite_inf.items()],
        "alertes_par_gravite": [{"label": k, "value": v} for k, v in alertes_par_gravite.items()],
        "depots": [{"label": k, "value": v} for k, v in sorted(depots.items())],
        "produits": [{"label": k, "value": v} for k, v in sorted(produits.items())],
        "top_chauffeurs": top_chauffeurs,
    }


def _lignes_export(items):
    out = []
    for h in items:
        # §0undecies E1 — « Nb trajets » = lignes fusionnées comme à l'écran
        d = fusionner_snapshot(h.donnees)
        out.append([
            h.date_jour.isoformat(),
            h.vehicule.plaque if h.vehicule else "—",
            (h.conducteur.prenom_usuel if h.conducteur else None) or (d.get("conducteur") or {}).get("prenom_usuel") or "—",
            d.get("situation") or "—", d.get("statut_camion") or "—",
            d.get("depot_recepteur") or "—", d.get("produit") or "—",
            d.get("numero_ot") or "—",
            d.get("heure_depart", "—")[11:16] if d.get("heure_depart") else "—",
            d.get("arret_final") or "—",
            # §0vicies decies N1 — TCC masqué « 0:00 » (snapshot déjà masqué à la lecture)
            "0:00", fmt_hms(d.get("tcj_s") or 0), fmt_hms(d.get("ttj_s") or 0),
            d.get("nb_trajets") or 0, round(d.get("km_parcourus") or 0, 1),
            h.nb_infractions, h.nb_alertes,
        ])
    return out


HEADERS = ["Date", "Plaque", "Conducteur", "Situation", "Statut", "Dépôt",
           "Produit", "N° OT", "Départ", "Arrêt final", "TCC", "TCJ", "TTJ",
           "Trajets", "Km", "Infractions", "Alertes"]


@router.get("/export.xlsx")
def export_historique_xlsx(annee: int | None = None, mois: int | None = None,
                           db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    auj = now_local().date()
    annee, mois = annee or auj.year, mois or auj.month
    items = db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.annee == annee, HistoriqueJournalier.mois == mois)
        .order_by(HistoriqueJournalier.date_jour.desc())).all()
    contenu = export_excel(f"Historique {mois:02d}/{annee}", HEADERS, _lignes_export(items))
    return Response(contenu, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=historique_{annee}_{mois:02d}.xlsx"})


@router.get("/export.pdf")
def export_historique_pdf(annee: int | None = None, mois: int | None = None,
                          db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    auj = now_local().date()
    annee, mois = annee or auj.year, mois or auj.month
    items = db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.annee == annee, HistoriqueJournalier.mois == mois)
        .order_by(HistoriqueJournalier.date_jour.desc()).limit(1500)).all()
    contenu = export_pdf(f"Historique de {mois:02d}/{annee}", HEADERS, _lignes_export(items))
    return Response(contenu, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=historique_{annee}_{mois:02d}.pdf"})


# =============================================================================
# ADDENDUM v1.1 §4.4 — Exports multi-jours au format Suivi Journalier
# =============================================================================
def _jours_et_synthese(archives) -> tuple[list, list]:
    """Groupe les archives par jour : lignes au format s_suivi (snapshot §4.5)
    + synthèse quotidienne (camions, TCJ/TTJ moyens, infractions, alertes)."""
    par_jour: dict[date, list] = defaultdict(list)
    for h in archives:
        par_jour[h.date_jour].append(h)
    jours, synthese = [], []
    for jour in sorted(par_jour):
        hs = sorted(par_jour[jour],
                    key=lambda h: h.vehicule.plaque if h.vehicule else "")
        lignes = []
        for h in hs:
            # §0undecies E1 — export-jour identique à l'écran (lignes fusionnées)
            d = fusionner_snapshot(dict(h.donnees or {}))
            d.setdefault("plaque", h.vehicule.plaque if h.vehicule else "—")
            if not d.get("conducteur") and h.conducteur:
                d["conducteur"] = s_conducteur(h.conducteur, court=True)
            lignes.append(d)
        tcjs = [d.get("tcj_s") for d in lignes if d.get("tcj_s")]
        ttjs = [d.get("ttj_s") for d in lignes if d.get("ttj_s")]
        synthese.append({
            "feuille": f"{jour:%d-%m-%Y}", "date_label": f"{jour:%d/%m/%Y}",
            "nb_camions": len(lignes),
            "tcj_moyen_s": int(sum(tcjs) / len(tcjs)) if tcjs else 0,
            "ttj_moyen_s": int(sum(ttjs) / len(ttjs)) if ttjs else 0,
            "nb_infractions": sum(h.nb_infractions for h in hs),
            "nb_alertes": sum(h.nb_alertes for h in hs),
        })
        jours.append((jour, lignes))
    return jours, synthese


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _lignes_jour_archive(db: Session, jour: date) -> list[dict]:
    """Lignes s_suivi d'une journée ARCHIVÉE (snapshots §4.5) — utilisées pour
    exporter une journée précise avec le format strict de l'onglet Suivi."""
    archives = _archives_periode(db, jour, jour, None)
    jours, _ = _jours_et_synthese(archives)
    return jours[0][1] if jours else []


@router.get("/suivi/export-jour.xlsx")
def export_jour_archive_xlsx(date_jour: str = Query(alias="date"), mode: str = "detail",
                             db: Session = Depends(get_db),
                             user=Depends(require_roles(*TOUS))):
    """Export Excel d'UNE journée archivée — fichier identique à l'export de
    l'onglet Suivi Journalier (§3.3 : écran = export), produit depuis l'archive."""
    from ..exporters import export_suivi_excel
    jour = date.fromisoformat(date_jour)
    lignes = _lignes_jour_archive(db, jour)
    detail = mode != "compact"
    audit(db, user, "historique.export_jour_excel", "historique", str(jour),
          {"mode": "detail" if detail else "compact", "nb_lignes": len(lignes)})
    db.commit()
    contenu = export_suivi_excel(jour.strftime("%d/%m/%Y"), lignes, detail,
                                 utilisateur=user.nom_complet)
    nom = f"Suivi_Journalier_{jour.isoformat()}.xlsx"
    return Response(contenu, media_type=XLSX_MIME,
                    headers={"Content-Disposition": f"attachment; filename={nom}"})


@router.get("/suivi/export-jour.pdf")
def export_jour_archive_pdf(date_jour: str = Query(alias="date"), mode: str = "detail",
                            db: Session = Depends(get_db),
                            user=Depends(require_roles(*TOUS))):
    """Export PDF d'UNE journée archivée — même rendu que l'export de l'onglet
    Suivi Journalier (paysage A3 en détaillé, en-tête/pied de page)."""
    from ..exporters import export_suivi_pdf
    jour = date.fromisoformat(date_jour)
    lignes = _lignes_jour_archive(db, jour)
    detail = mode != "compact"
    audit(db, user, "historique.export_jour_pdf", "historique", str(jour),
          {"mode": "detail" if detail else "compact", "nb_lignes": len(lignes)})
    db.commit()
    contenu = export_suivi_pdf(jour.strftime("%d/%m/%Y"), lignes, detail,
                               utilisateur=user.nom_complet)
    nom = f"Suivi_Journalier_{jour.isoformat()}.pdf"
    return Response(contenu, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={nom}"})


@router.get("/suivi/export.xlsx")
def export_historique_suivi_xlsx(du: str, au: str, q: str | None = None,
                                 mode: str = "detail",
                                 db: Session = Depends(get_db),
                                 user=Depends(require_roles(*TOUS))):
    """Classeur Excel : feuille « Synthèse » + une feuille par jour (JJ-MM-AAAA),
    chaque feuille au format strict du Suivi Journalier — décision validée §6."""
    from ..exporters import export_historique_excel
    d1, d2 = _periode(du, au)
    archives = _archives_periode(db, d1, d2, q)
    jours, synthese = _jours_et_synthese(archives)
    detail = mode != "compact"
    audit(db, user, "historique.export_excel", "historique", f"{d1}_{d2}",
          {"du": str(d1), "au": str(d2), "recherche": q,
           "mode": "detail" if detail else "compact",
           "nb_jours": len(jours), "nb_lignes": len(archives)})
    db.commit()
    contenu = export_historique_excel(d1, d2, jours, synthese, detail=detail,
                                      utilisateur=user.nom_complet)
    nom = f"Historique_Suivi_Journalier_{d1.isoformat()}_au_{d2.isoformat()}.xlsx"
    return Response(contenu, media_type=XLSX_MIME,
                    headers={"Content-Disposition": f"attachment; filename={nom}"})


@router.get("/suivi/export.pdf")
def export_historique_suivi_pdf(du: str, au: str, q: str | None = None,
                                mode: str = "detail",
                                db: Session = Depends(get_db),
                                user=Depends(require_roles(*TOUS))):
    """PDF unique : page de garde récapitulative + une section par jour
    (saut de page), chaque section au format strict du Suivi Journalier."""
    from ..exporters import export_historique_pdf
    d1, d2 = _periode(du, au)
    archives = _archives_periode(db, d1, d2, q)
    jours, synthese = _jours_et_synthese(archives)
    detail = mode != "compact"
    audit(db, user, "historique.export_pdf", "historique", f"{d1}_{d2}",
          {"du": str(d1), "au": str(d2), "recherche": q,
           "mode": "detail" if detail else "compact",
           "nb_jours": len(jours), "nb_lignes": len(archives)})
    db.commit()
    contenu = export_historique_pdf(d1, d2, jours, synthese, detail=detail,
                                    utilisateur=user.nom_complet)
    nom = f"Historique_Suivi_Journalier_{d1.isoformat()}_au_{d2.isoformat()}.pdf"
    return Response(contenu, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={nom}"})

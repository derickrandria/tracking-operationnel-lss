"""Exports Excel / PDF (§6.6 — Historique, Infractions, Alertes ;
Addendum v1.1 — Suivi Journalier détaillé Trajet 1…10 + Historique multi-jours)."""
import io
import os
from datetime import datetime

from .config import now_local

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BLEU = "1F4E79"
GRIS = "F2F2F2"

# Logo LSS (identité visuelle) — même fichier pour Excel et PDF ; présent en
# option propre : si le fichier est absent, les exports restent fonctionnels.
_LOGO = os.path.join(os.path.dirname(__file__), "static", "logo-lss.png")
_LOGO_RATIO = 3.15          # largeur / hauteur du fichier logo


def _logo_excel(hauteur_px: int = 30):
    """Image openpyxl du logo LSS (None si Pillow/fichier indisponible)."""
    if not os.path.exists(_LOGO):
        return None
    try:
        from openpyxl.drawing.image import Image as XLImage
        img = XLImage(_LOGO)
        img.height = hauteur_px
        img.width = int(hauteur_px * _LOGO_RATIO)
        return img
    except Exception:
        return None


def export_excel(titre: str, headers: list[str], rows: list[list]) -> bytes:
    import re
    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\\/*?:\[\]]", "-", titre)[:31]  # caractères interdits Excel

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1, value=f"{titre} — exporté le {datetime.now():%d/%m/%Y %H:%M}")
    c.font = Font(bold=True, size=13, color="FFFFFF")
    for k in range(1, len(headers) + 1):
        ws.cell(row=1, column=k).fill = PatternFill("solid", fgColor=BLEU)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    logo = _logo_excel(30)
    if logo is not None:
        ws.add_image(logo, "A1")

    for j, h in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=j, value=h)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor=GRIS)
        cell.alignment = Alignment(horizontal="center")

    for i, row in enumerate(rows, start=3):
        for j, val in enumerate(row, start=1):
            ws.cell(row=i, column=j, value=val)

    for j, h in enumerate(headers, start=1):
        largeur = max([len(str(h))] + [len(str(r[j - 1] or "")) for r in rows[:300]]) + 2
        ws.column_dimensions[get_column_letter(j)].width = min(46, max(10, largeur))
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{len(rows) + 2}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_pdf(titre: str, headers: list[str], rows: list[list]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=12 * mm, bottomMargin=10 * mm, title=titre)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{titre}</b>", styles["Title"]),
        Paragraph(f"Exporté le {datetime.now():%d/%m/%Y %H:%M} — Tracking Opérationnel LSS",
                  styles["Normal"]),
        Spacer(1, 6),
    ]
    data = [headers] + [["" if v is None else str(v) for v in r] for r in rows]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    doc.build(story)
    return buf.getvalue()


# =============================================================================
# ADDENDUM v1.1 — Suivi Journalier (§3) et Historique multi-jours (§4)
# =============================================================================
# Exigence audit (retour métier) : 27 colonnes — Heure de départ (= Début T1),
# Fin T1 / Pause 1, puis Début/Fin/Pause pour T2 → T9. Stockage non plafonné :
# les trajets 10+ restent en base et sont signalés « +N » dans « Nb trajets ».
NB_TRAJETS_AFFICHES = 9

# couleurs des 4 parties (cohérentes avec la légende de l'écran Suivi)
_COUL_A, _COUL_B, _COUL_C, _COUL_D = "64748B", "2563EB", "D97706", "059669"


def _hhmm(iso_):
    """2026-07-30T06:09:00 -> '06:09'."""
    return iso_[11:16] if iso_ and len(iso_) >= 16 else ("—" if not iso_ else iso_)


def _fmt_duree_txt(secondes):
    """Durée au format H:MM (Addendum v1.8 §CA-6) : 16200 -> '4:30',
    2100 -> '0:35' ; les HEURES restent en HH:MM (voir _hhmm)."""
    if secondes is None:
        return "—"
    s = int(secondes)
    signe = "-" if s < 0 else ""
    s = abs(s)
    return f"{signe}{s // 3600}:{(s % 3600) // 60:02d}"


def _entetes_suivi(detail: bool) -> list[str]:
    """Colonnes exportées — STRICTEMENT identiques à l'écran Suivi Journalier
    (Parties A, B, C, D ; mode détaillé : Trajet 1→10 Début/Fin/Pause, §3.3).
    Addendum v1.9 §4 : TCC/TCJ/TTJ EN PREMIER dans la partie D (alerte en
    priorité) et colonne « Lieu Arrêt » après « Arrêt final »."""
    cols = ["Plaque", "Description", "Chauffeur", "Téléphone",
            "Situation", "Statut", "Dépôt", "Distributeur", "Produit", "N° OT",
            "Emplacement J-1", "Pos. 08h", "Pos. 10h", "Pos. 12h",
            "Pos. 14h", "Pos. 16h", "Pos. 18h",
            # §0vicies decies N2 (31/08/2026) — relevés automatiques du soir
            "Pos. 20h", "Pos. 22h",
            "TCC", "TCJ", "TTJ", "Heure départ"]
    if detail:
        cols += ["Fin T1", "Pause 1"]  # « Heure départ » ci-dessus = Début T1
        for i in range(2, NB_TRAJETS_AFFICHES + 1):
            cols += [f"Début T{i}", f"Fin T{i}", f"Pause {i}"]
    cols += ["Arrêt final", "Lieu Arrêt", "Nb trajets", "Km"]
    return cols


class _Duree(float):
    """Sentinelle : valeur de durée en secondes → cellule Excel au format [h]:mm."""
    __slots__ = ()


# Addendum v1.4 §3.2 — distinction visuelle PROVISOIRE / VALIDÉ identique à
# l'écran Suivi Journalier (orange italique) + note de légende (§3.3 cohérence
# écran/export).
COULEUR_PROVISOIRE = "B45309"   # ambre-700
NOTE_LEGENDE_PROVISOIRE = (
    "Heures en orange italique = trajet PROVISOIRE (reconstruction temps réel "
    "depuis l'onglet Événements) — confirmé automatiquement dès que le trajet "
    "apparaît dans l'onglet Trajets MZoneX / rapport CamtrackPro (Addendum v1.4). "
    "Trajet en cours : colonne Fin vide — la durée de conduite courante est "
    "dans la colonne TCC (Addendum v1.9)."
)


def _est_provisoire(t: dict | None) -> bool:
    return bool(t) and (t.get("statut_source") or "VALIDÉ") == "PROVISOIRE"


# §0vicies decies N2 (31/08/2026) — 21 → 23 : Pos. 20h/22h décalent le bloc
# TCC/TCJ/TTJ → « Heure départ » (et donc tous les indices trajets) de +2.
_DEF_DECAL_TRAJETS = 2


def _cols_provisoires(l: dict, detail: bool) -> set[int]:
    """Indices (1-based) des colonnes de la ligne correspondant à des trajets
    encore PROVISOIRE (Début/Fin/Pause T1…T9) — Addendum v1.9 §4 : décalés de
    +3 par les colonnes TCC/TCJ/TTJ placées avant le premier trajet."""
    if not detail:
        return set()
    cols = set()
    trajets = l.get("trajets") or []
    for i in range(NB_TRAJETS_AFFICHES):
        if i >= len(trajets) or not _est_provisoire(trajets[i]):
            continue
        if i == 0:                      # Heure départ / Fin T1 / Pause 1
            cols |= {21 + _DEF_DECAL_TRAJETS, 22 + _DEF_DECAL_TRAJETS,
                     23 + _DEF_DECAL_TRAJETS}
        else:                           # Début Ti / Fin Ti / Pause i
            base = 24 + _DEF_DECAL_TRAJETS + 3 * (i - 1)
            cols |= {base, base + 1, base + 2}
    return cols


def _valeurs_suivi(l: dict, detail: bool, excel: bool) -> list:
    """Une ligne de suivi (dict `s_suivi`, vivant ou snapshot archivé) dans
    l'ordre des colonnes visibles à l'écran (§3.3 — respect du mode)."""
    cond = l.get("conducteur") or {}
    trajets = l.get("trajets") or []

    def duree(s):
        if excel:
            return _Duree((s or 0) / 86400.0)
        return _fmt_duree_txt(s or 0)

    vals = [
        l.get("plaque") or "—", l.get("description") or "—",
        cond.get("prenom_usuel") or cond.get("nom_prenom") or "—",
        cond.get("telephone") or "—",
        l.get("situation") or "—", l.get("statut_camion") or "—",
        l.get("depot_recepteur") or "—", l.get("distributeur") or "—",
        l.get("produit") or "—", l.get("numero_ot") or "—",
        l.get("emplacement_j_moins_1") or "—",
        l.get("position_08h") or "—", l.get("position_10h") or "—",
        l.get("position_12h") or "—", l.get("position_14h") or "—",
        l.get("position_16h") or "—", l.get("position_18h") or "—",
        # §0vicies decies N2 — Pos. 20h/22h (relevés automatiques du soir)
        l.get("position_20h") or "—", l.get("position_22h") or "—",
        # Addendum v1.9 §4 — TCC/TCJ/TTJ en premier (alerte en priorité)
        duree(l.get("tcc_s")), duree(l.get("tcj_s")), duree(l.get("ttj_s")),
        _hhmm(l.get("heure_depart")),
    ]
    if detail:
        for i in range(NB_TRAJETS_AFFICHES):
            t = trajets[i] if i < len(trajets) else None
            if t is None:
                vals += ["—", "—"] if i == 0 else ["—", "—", "—"]
            else:
                # le Début du trajet 1 est déjà rendu par la colonne « Heure départ »
                if i > 0:
                    vals.append(_hhmm(t.get("heure_debut")))
                if t.get("heure_fin"):
                    vals.append(_hhmm(t.get("heure_fin")))
                elif t.get("heure_debut"):
                    # Addendum v1.9 §3.3 + décision métier 05/08 — colonne Fin
                    # VIDE pour le trajet en cours : sa durée vit déjà dans la
                    # colonne TCC ; jamais de texte « en cours » à l'export
                    vals.append("")
                else:
                    vals.append("—")
                vals.append(duree(t.get("pause_apres_s")) if t.get("pause_apres_s") else "—")
    nb = l.get("nb_trajets") or len(trajets)
    extra = f" (+{len(trajets) - NB_TRAJETS_AFFICHES})" if len(trajets) > NB_TRAJETS_AFFICHES else ""
    vals += [
        l.get("arret_final") or "—",
        # Addendum v1.9 §4.2 — colonne « Lieu Arrêt »
        l.get("lieu_arret") or "—",
        f"{nb}{extra}", round(l.get("km_parcourus") or 0, 1),
    ]
    return vals


def _blocs_groupes(detail: bool):
    """(libellé, span, couleur) de la ligne d'en-tête « groupe ».
    Addendum v1.9 §4 : bloc TCC/TCJ/TTJ avant les trajets ; « Lieu Arrêt »
    dans le bloc de clôture."""
    blocs = [("A — Véhicule & Chauffeur", 4, _COUL_A),
             ("B — Données métier", 6, _COUL_B),
             # §0vicies decies N2 — 7 → 9 relevés (Emplacement J-1 + 08h…22h)
             ("C — Relevés de la journée", 9, _COUL_C),
             ("D — TCC · TCJ · TTJ", 3, _COUL_D)]
    if detail:
        for i in range(1, NB_TRAJETS_AFFICHES + 1):
            blocs.append((f"Trajet {i}", 3, _COUL_D))  # Trajet 1 inclut « Heure départ »
    else:
        blocs.append(("Heure départ", 1, _COUL_D))
    blocs.append(("D — Arrêt & totaux", 4, _COUL_D))
    return blocs


def _nom_feuille(nom: str) -> str:
    import re
    return re.sub(r"[\\/*?:\[\]]", "-", nom)[:31]


def _ecrire_feuille_suivi(wb: Workbook, nom: str, ligne_titre: str,
                          lignes: list[dict], detail: bool):
    """Feuille Excel complète au format Suivi Journalier : titre, en-têtes à
    2 niveaux (groupe / sous-colonne) figés, durées au format horaire [h]:mm
    réellement exploitables dans Excel (§3.4), largeurs auto, filtres."""
    vierge = (len(wb.sheetnames) == 1 and wb.sheetnames[0] == "Sheet"
              and wb["Sheet"].max_row <= 1 and wb["Sheet"]["A1"].value is None)
    ws = wb["Sheet"] if vierge else wb.create_sheet()
    ws.title = _nom_feuille(nom)

    entetes = _entetes_suivi(detail)
    ncols = len(entetes)
    bord_fin = Border(*[Side(style="thin", color="D3DAE3")] * 4)

    # --- ligne 1 : bandeau titre
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1, value=ligne_titre)
    c.font = Font(bold=True, size=13, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="1F4E79")
    for k in range(1, ncols + 1):
        ws.cell(row=1, column=k).fill = PatternFill("solid", fgColor="1F4E79")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    logo = _logo_excel(30)                       # identité visuelle LSS (§3.4)
    if logo is not None:
        ws.add_image(logo, "A1")

    # --- ligne 2 : groupes (A / B / C / Trajet i / D)
    col = 1
    sous_libelles = ["Plaque", "Description", "Chauffeur", "Téléphone",
                     "Situation", "Statut", "Dépôt", "Distributeur", "Produit", "N° OT",
                     "Emplacement J-1", "08h", "10h", "12h", "14h", "16h", "18h",
                     # §0vicies decies N2 — relevés automatiques du soir
                     "20h", "22h",
                     # Addendum v1.9 §4 — TCC/TCJ/TTJ avant « Départ »
                     "TCC", "TCJ", "TTJ", "Départ"]
    sous_apres_trajets = ["Arrêt final", "Lieu Arrêt", "Nb", "Km"]
    for libelle, span, couleur in _blocs_groupes(detail):
        if span > 1:
            ws.merge_cells(start_row=2, start_column=col, end_row=2, end_column=col + span - 1)
        gc = ws.cell(row=2, column=col, value=libelle)
        gc.font = Font(bold=True, size=9, color="FFFFFF")
        gc.fill = PatternFill("solid", fgColor=couleur)
        gc.alignment = Alignment(horizontal="center", vertical="center")
        for k in range(col, col + span):
            ws.cell(row=2, column=k).fill = PatternFill("solid", fgColor=couleur)
        col += span

    # --- ligne 3 : sous-en-têtes
    sous = sous_libelles + (["Fin", "Pause"] + ["Début", "Fin", "Pause"] * (NB_TRAJETS_AFFICHES - 1)
                            if detail else []) + sous_apres_trajets
    for j, h in enumerate(sous, start=1):
        cell = ws.cell(row=3, column=j, value=h)
        cell.font = Font(bold=True, size=9)
        cell.fill = PatternFill("solid", fgColor="EDF1F6")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = bord_fin
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 16

    # --- données
    for i, l in enumerate(lignes, start=4):
        provisoires = _cols_provisoires(l, detail)      # Addendum v1.4 §3.2
        for j, val in enumerate(_valeurs_suivi(l, detail, excel=True), start=1):
            cell = ws.cell(row=i, column=j)
            if isinstance(val, _Duree):
                cell.value = float(val)
                cell.number_format = "[h]:mm"
                cell.alignment = Alignment(horizontal="center")
            else:
                cell.value = val
            cell.border = bord_fin
            if j in provisoires and val not in (None, "—"):
                cell.font = Font(size=9, italic=True, color=COULEUR_PROVISOIRE)
            else:
                cell.font = Font(size=9)
        if i % 2 == 0:
            for j in range(1, ncols + 1):
                ws.cell(row=i, column=j).fill = PatternFill("solid", fgColor="F7F9FC")

    # --- note de légende PROVISOIRE/VALIDÉ (Addendum v1.4 §3.2, modes détaillé)
    if detail:
        ligne_note = len(lignes) + 4
        ws.merge_cells(start_row=ligne_note, start_column=1,
                       end_row=ligne_note, end_column=ncols)
        note = ws.cell(row=ligne_note, column=1,
                       value="ⓘ  " + NOTE_LEGENDE_PROVISOIRE)
        note.font = Font(size=8.5, italic=True, color=COULEUR_PROVISOIRE)
        note.alignment = Alignment(horizontal="left")

    # --- finitions : largeurs auto, figeage, filtres (§3.4)
    echantillon_txt = [_valeurs_suivi(l, detail, excel=False) for l in lignes[:120]]
    for j, h in enumerate(entetes, start=1):
        largeur = len(str(h))
        for vals in echantillon_txt:
            largeur = max(largeur, len(str(vals[j - 1])))
        ws.column_dimensions[get_column_letter(j)].width = min(34, max(7, largeur + 2))
    ws.freeze_panes = "E4"  # lignes d'en-tête + colonnes Partie A figées
    ws.auto_filter.ref = f"A3:{get_column_letter(ncols)}{len(lignes) + 3}"
    return ws


def export_suivi_excel(jour_label: str, lignes: list[dict], detail: bool,
                       utilisateur: str = "") -> bytes:
    """Addendum v1.1 §3.4 — Export Excel du Suivi Journalier (vue affichée)."""
    wb = Workbook()
    titre = (f"LSS — Suivi Journalier du {jour_label} · {len(lignes)} camions · "
             f"exporté le {datetime.now():%d/%m/%Y %H:%M}"
             + (f" par {utilisateur}" if utilisateur else ""))
    _ecrire_feuille_suivi(wb, "Suivi Journalier", titre, lignes, detail)
    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------- PDF
def _styles_pdf():
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    base = getSampleStyleSheet()
    return base, ParagraphStyle("petit", parent=base["Normal"], fontSize=7, leading=8.5)


def _table_suivi_pdf(lignes: list[dict], detail: bool):
    """Table reportlab au même format de colonnes que l'écran (§3.5/§4.4) —
    trajets PROVISOIRE en orange italique (Addendum v1.4 §3.2)."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle
    entetes = _entetes_suivi(detail)
    taille = 5.4 if detail else 6.6
    st_provisoire = ParagraphStyle("provisoire", fontName="Helvetica-Oblique",
                                   fontSize=taille, leading=taille + 1,
                                   textColor=colors.HexColor("#B45309"))
    data = [entetes]
    for l in lignes:
        valeurs = [str(v) for v in _valeurs_suivi(l, detail, excel=False)]
        provisoires = _cols_provisoires(l, detail)
        if provisoires:
            valeurs = [Paragraph(v, st_provisoire)
                       if (j + 1) in provisoires and v != "—" else v
                       for j, v in enumerate(valeurs)]
        data.append(valeurs)
    t = Table(data, repeatRows=1, splitByRow=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 5.8 if detail else 7),
        ("FONTSIZE", (0, 1), (-1, -1), 5.4 if detail else 6.6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
    ]
    # rappel visuel : la partie D (TCC/TCJ/TTJ → totaux, Addendum v1.9 §4)
    # est légèrement teintée vert d'eau sur les lignes de données
    style.append(("BACKGROUND", (17, 1), (-1, -1), colors.HexColor("#EFF7F2")))
    t.setStyle(TableStyle(style))
    return t


def _pied_page(titre: str, utilisateur: str):
    """En-tête/pied de page obligatoires (§3.5) : nom, titre, exporté le/par, page."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    def decore(canvas, doc):
        canvas.saveState()
        largeur, hauteur = doc.pagesize
        canvas.setFillColor(colors.HexColor("#1F4E79"))
        canvas.rect(0, hauteur - 9 * mm, largeur, 9 * mm, fill=1, stroke=0)
        # identité LSS : logo sur vignette blanche, en haut à gauche
        if os.path.exists(_LOGO):
            canvas.setFillColor(colors.white)
            canvas.roundRect(8 * mm, hauteur - 8.3 * mm, 21.5 * mm, 7.3 * mm,
                             1.2 * mm, fill=1, stroke=0)
            canvas.drawImage(_LOGO, 9.2 * mm, hauteur - 7.6 * mm,
                             width=19.3 * mm, height=19.3 * mm / _LOGO_RATIO,
                             preserveAspectRatio=True, mask="auto")
        else:
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 8.5)
            canvas.drawString(10 * mm, hauteur - 6 * mm, "LSS Tracking")
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(largeur - 10 * mm, hauteur - 6 * mm, titre)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(10 * mm, 5 * mm,
                          f"Exporté le {datetime.now():%d/%m/%Y %H:%M} par {utilisateur or '—'}")
        canvas.drawRightString(largeur - 10 * mm, 5 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return decore


def _note_provisoire_pdf():
    """Note de légende PROVISOIRE/VALIDÉ (Addendum v1.4 §3.2) — même texte
    que la légende affichée sous la grille à l'écran."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    from reportlab.platypus import Spacer
    st = ParagraphStyle("noteProvisoire", fontName="Helvetica-Oblique", fontSize=6.5,
                        leading=8, textColor=colors.HexColor("#B45309"))
    return [Spacer(1, 3), Paragraph("ⓘ  " + NOTE_LEGENDE_PROVISOIRE, st)]


def export_suivi_pdf(jour_label: str, lignes: list[dict], detail: bool,
                     utilisateur: str = "") -> bytes:
    """Addendum v1.1 §3.5 — Export PDF du Suivi Journalier : paysage, A3 en
    mode détaillé / A4 en compact, en-tête + pied de page numéroté."""
    from reportlab.lib.pagesizes import A3, A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer, Paragraph

    titre = f"Suivi Journalier — {jour_label}"
    page = landscape(A3) if detail else landscape(A4)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=8 * mm, rightMargin=8 * mm,
                            topMargin=14 * mm, bottomMargin=11 * mm, title=titre,
                            author="LSS Tracking")
    styles, petit = _styles_pdf()
    story = [
        Paragraph(f"<b>{titre}</b> — {len(lignes)} camions · "
                  f"{'mode détaillé (Trajet 1 à 10)' if detail else 'mode compact'}", styles["Title"]),
        Spacer(1, 4),
        _table_suivi_pdf(lignes, detail),
    ]
    if detail:
        story += _note_provisoire_pdf()
    doc.build(story, onFirstPage=_pied_page(titre, utilisateur),
              onLaterPages=_pied_page(titre, utilisateur))
    return buf.getvalue()


# =============================================================================
# ADDENDUM v1.1 §4.4 — Historique multi-jours au format Suivi Journalier
# =============================================================================
def export_historique_excel(du, au, jours: list, synthese: list[dict],
                            detail: bool = True, utilisateur: str = "") -> bytes:
    """Classeur multi-jours : feuille « Synthèse » en première position (avec
    liens de navigation), puis une feuille par jour nommée JJ-MM-AAAA dans
    l'ordre chronologique, chacune au format complet du Suivi Journalier."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Synthèse"

    entetes = ["Date", "Feuille du jour", "Camions suivis", "TCJ moyen",
               "TTJ moyen", "Infractions", "Alertes"]
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(entetes))
    c = ws.cell(row=1, column=1,
                value=f"LSS — Historique du Suivi Journalier du {du:%d/%m/%Y} au {au:%d/%m/%Y}"
                      f" · {len(jours)} jours · exporté le {datetime.now():%d/%m/%Y %H:%M}"
                      + (f" par {utilisateur}" if utilisateur else ""))
    c.font = Font(bold=True, size=13, color="FFFFFF")
    for k in range(1, len(entetes) + 1):
        ws.cell(row=1, column=k).fill = PatternFill("solid", fgColor="1F4E79")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    logo = _logo_excel(30)
    if logo is not None:
        ws.add_image(logo, "A1")

    for j, h in enumerate(entetes, start=1):
        cell = ws.cell(row=2, column=j, value=h)
        cell.font = Font(bold=True, size=10)
        cell.fill = PatternFill("solid", fgColor="EDF1F6")
        cell.alignment = Alignment(horizontal="center")

    for i, s in enumerate(synthese, start=3):
        nom_feuille = s["feuille"]
        cell_date = ws.cell(row=i, column=1, value=s["date_label"])
        cell_date.hyperlink = f"#'{nom_feuille}'!A1"   # lien de navigation (§4.4)
        cell_date.font = Font(size=9, color="0563C1", underline="single")
        ws.cell(row=i, column=2, value=nom_feuille)
        ws.cell(row=i, column=3, value=s["nb_camions"])
        cell_tcj = ws.cell(row=i, column=4, value=(s["tcj_moyen_s"] or 0) / 86400.0)
        cell_ttj = ws.cell(row=i, column=5, value=(s["ttj_moyen_s"] or 0) / 86400.0)
        cell_tcj.number_format = cell_ttj.number_format = "[h]:mm"
        cell_tcj.alignment = cell_ttj.alignment = Alignment(horizontal="center")
        ws.cell(row=i, column=6, value=s["nb_infractions"])
        ws.cell(row=i, column=7, value=s["nb_alertes"])
        for j in range(2, len(entetes) + 1):
            ws.cell(row=i, column=j).font = Font(size=9)

    for j, h in enumerate(entetes, start=1):
        ws.column_dimensions[get_column_letter(j)].width = max(14, len(h) + 4)
    ws.freeze_panes = "A3"

    # --- une feuille par jour (format strict de l'onglet Suivi Journalier)
    for jour, lignes in jours:
        nom = f"{jour:%d-%m-%Y}"
        tit = (f"LSS — Suivi Journalier du {jour:%d/%m/%Y} · {len(lignes)} camions · "
               f"archive du cycle de minuit")
        _ecrire_feuille_suivi(wb, nom, tit, lignes, detail)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_historique_pdf(du, au, jours: list, synthese: list[dict],
                          detail: bool = True, utilisateur: str = "") -> bytes:
    """Document unique : page de garde récapitulative (dates + stats globales),
    puis une section par jour — saut de page entre chaque jour (§4.4) —
    chaque section reprenant le format complet du Suivi Journalier."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A3, A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import (PageBreak, SimpleDocTemplate, Spacer,
                                    Paragraph, Table, TableStyle)

    titre = f"Historique du Suivi Journalier — {du:%d/%m/%Y} au {au:%d/%m/%Y}"
    page = landscape(A3) if detail else landscape(A4)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=8 * mm, rightMargin=8 * mm,
                            topMargin=14 * mm, bottomMargin=11 * mm, title=titre,
                            author="LSS Tracking")
    styles, petit = _styles_pdf()
    story = [Spacer(1, 40)]
    # page de garde : logo LSS si disponible (sinon texte de secours)
    if os.path.exists(_LOGO):
        from reportlab.platypus import Image as RLImage
        story.append(RLImage(_LOGO, width=52 * mm,
                             height=52 * mm / _LOGO_RATIO))
    else:
        story.append(Paragraph("<b>LSS Tracking</b>", styles["Title"]))
    story += [Spacer(1, 6),
              Paragraph(f"<b>{titre}</b>", styles["Title"]),
              Paragraph(f"{len(jours)} journée(s) archivée(s) · "
                        f"exporté le {datetime.now():%d/%m/%Y %H:%M} par {utilisateur or '—'}",
                        styles["Normal"]),
             Spacer(1, 14)]

    # tableau récapitulatif de la période (page de garde)
    data = [["Date", "Camions suivis", "TCJ moyen", "TTJ moyen", "Infractions", "Alertes"]]
    for s in synthese:
        data.append([s["date_label"], str(s["nb_camions"]), _fmt_duree_txt(s["tcj_moyen_s"]),
                     _fmt_duree_txt(s["ttj_moyen_s"]), str(s["nb_infractions"]),
                     str(s["nb_alertes"])])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DC")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]))
    story.append(t)

    # --- une section par jour, avec saut de page (§4.4)
    for jour, lignes in jours:
        story.append(PageBreak())
        story.append(Paragraph(f"<b>Suivi Journalier — {jour:%d/%m/%Y}</b> — "
                               f"{len(lignes)} camions", styles["Title"]))
        story.append(Spacer(1, 4))
        story.append(_table_suivi_pdf(lignes, detail))
    doc.build(story, onFirstPage=_pied_page(titre, utilisateur),
              onLaterPages=_pied_page(titre, utilisateur))
    return buf.getvalue()


# =============================================================================
# EXPORTS MISSIONS (Module 3 — Reconstitution & Suivi Logistique)
# =============================================================================
def export_missions_excel(titre_periode: str, missions: list[dict],
                          utilisateur: str = "") -> bytes:
    """Export Excel de l'onglet Missions avec les 4 blocs de colonnes."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Missions"

    headers = [
        "N° Mission", "Date", "Chauffeur", "Immatriculation",
        "N° OT", "Distributeur", "Produit", "Dépôt Prévu", "Destination Réelle",
        "Statut Camion", "Statut Mission",
        "Départ OT", "Chargement GRT", "Livraison / Fin",
        "Durée", "Km Vide", "Km Chargé", "Km Total", "Nb Infractions"
    ]

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1,
                value=f"LSS — Registre des Missions — {titre_periode} · {len(missions)} missions"
                      f" · exporté le {datetime.now():%d/%m/%Y %H:%M}"
                      + (f" par {utilisateur}" if utilisateur else ""))
    c.font = Font(bold=True, size=13, color="FFFFFF")
    for k in range(1, len(headers) + 1):
        ws.cell(row=1, column=k).fill = PatternFill("solid", fgColor="1F4E79")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    logo = _logo_excel(30)
    if logo is not None:
        ws.add_image(logo, "A1")

    for j, h in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=j, value=h)
        cell.font = Font(bold=True, size=10)
        cell.fill = PatternFill("solid", fgColor="EDF1F6")
        cell.alignment = Alignment(horizontal="center")

    for i, m in enumerate(missions, start=3):
        cond = m.get("conducteur") or {}
        chauffeur_nom = cond.get("nom_prenom") or cond.get("prenom_usuel") or "—"
        depot_eff = m.get("depot_effectif") or m.get("depot_prevu") or "—"
        if m.get("est_deviee"):
            depot_eff = f"{depot_eff} (DÉVIÉE)"

        vals = [
            m.get("code_mission") or f"MIS-{m.get('id', '')[:8]}",
            m.get("date_jour") or "—",
            chauffeur_nom,
            m.get("plaque") or "—",
            m.get("numero_ot") or "—",
            m.get("distributeur") or "—",
            m.get("produit") or "—",
            m.get("depot_prevu") or m.get("depot") or "—",
            depot_eff,
            m.get("statut_camion_actuel") or "—",
            m.get("statut") or "—",
            _hhmm(m.get("heure_debut")),
            _hhmm(m.get("heure_chargement")),
            _hhmm(m.get("heure_fin")),
            _fmt_duree_txt(m.get("duree_s")),
            round(m.get("km_vide") or 0.0, 1),
            round(m.get("km_charge") or 0.0, 1),
            round(m.get("kilometrage_total") or m.get("kilometrage") or 0.0, 1),
            m.get("nb_infractions") or 0,
        ]
        for j, val in enumerate(vals, start=1):
            cell = ws.cell(row=i, column=j, value=val)
            cell.font = Font(size=9)
            if j in (1, 2, 4, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19):
                cell.alignment = Alignment(horizontal="center")

    for j, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(j)].width = max(12, len(h) + 3)
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{len(missions) + 2}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_missions_pdf(titre_periode: str, missions: list[dict],
                        utilisateur: str = "") -> bytes:
    """Export PDF du registre des missions (Format A4 Paysage)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer, Paragraph, Table, TableStyle

    titre = f"Registre des Missions — {titre_periode}"
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=8 * mm, rightMargin=8 * mm,
                            topMargin=14 * mm, bottomMargin=11 * mm, title=titre,
                            author="LSS Tracking")
    styles, petit = _styles_pdf()
    story = [
        Paragraph(f"<b>{titre}</b> — {len(missions)} mission(s)", styles["Title"]),
        Spacer(1, 4),
    ]

    headers = ["N° Mission", "Chauffeur", "Camion", "N° OT", "Produit",
               "Dépôt Prévu", "Destination", "Statut", "Départ", "Livraison",
               "Km Tot.", "Infr."]

    data = [headers]
    for m in missions:
        cond = m.get("conducteur") or {}
        chauffeur_nom = cond.get("prenom_usuel") or (cond.get("nom_prenom", "")[:16]) or "—"
        depot_eff = m.get("depot_effectif") or m.get("depot_prevu") or "—"
        if m.get("est_deviee"):
            depot_eff = f"{depot_eff}*"

        row = [
            m.get("code_mission") or f"MIS-{m.get('id', '')[:6]}",
            chauffeur_nom,
            m.get("plaque") or "—",
            m.get("numero_ot") or "—",
            m.get("produit") or "—",
            m.get("depot_prevu") or m.get("depot") or "—",
            depot_eff,
            m.get("statut") or "—",
            _hhmm(m.get("heure_debut")),
            _hhmm(m.get("heure_fin")),
            f"{round(m.get('kilometrage_total') or m.get('kilometrage') or 0.0, 0):.0f} km",
            str(m.get("nb_infractions") or 0),
        ]
        data.append(row)

    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DC")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 1), (1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    doc.build(story, onFirstPage=_pied_page(titre, utilisateur),
              onLaterPages=_pied_page(titre, utilisateur))
    return buf.getvalue()

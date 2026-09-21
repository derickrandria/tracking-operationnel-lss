"""v1.51 — RATTRAPAGE & ARCHIVAGE : « une journée = une transaction isolée ».

Cahier des charges du 18/09/2026 (Lead Backend & Data Systems), 13 exigences.
Ce module est la SEULE porte d'entrée du rattrapage et de l'archivage des
journées passées ; `daily.rattraper_consolidation` y délègue.

  ┌ exigence ────────────────────────────────────────────────────────────┐
  1. une journée = une transaction isolée ............ `traiter_jour`
  2. erreur sur une journée ⇒ les suivantes continuent `rattraper_journees`
  3. l'échec est écrit dans une transaction SÉPARÉE .. `_consigner_echec`
  4. alerte visible créée OU mise à jour .............. `alerter_jour`
  5. source indisponible/incomplète ⇒ AUCUNE archive
     partielle ....................................... `_porte_archivage`
  6. un jour ARCHIVÉ n'est jamais supprimé/réécrit
     silencieusement ................................. `traiter_jour` + `daily`
  7. relancé au démarrage ET périodiquement .......... `boucle_rattrapage_periodique`
  8. idempotent ...................................... verrou + « déjà archivé »
  9. deux workers ne traitent pas la même journée .... `acquerir_verrou_jour`
 10. PROVISOIRE / EN_ATTENTE / REJETE / VALIDE distincts  statuts jamais fusionnés
 11. « source vide confirmée » ≠ « source indisponible »  `LectureSource.etat`
 12. un échec de Ym@ne ne bloque pas les trajets ..... `SOURCES_NON_BLOQUANTES`
 13. simulateur jamais activé silencieusement ........ `daily.archiver_jour`
  └──────────────────────────────────────────────────────────────────────┘

Règle d'or héritée de la v1.50 : **on ne tient jamais un verrou d'écriture
pendant un appel réseau**. La lecture des portails a lieu AVANT d'ouvrir la
transaction de la journée ; celle-ci ne contient que des écritures courtes.

Arbitrage assumé (exigence 5) : quand une source bloquante est indisponible,
la journée est CONSOLIDÉE (23:59:59 — la journée civile doit se fermer, le
split de minuit est une opération d'intégrité) mais RIEN n'est écrit dans
`historique_journalier`. Aucune archive partielle n'existe donc jamais ; le
jour reste dans `traitement_journees` en `EN_ATTENTE_SOURCE` et sera archivé
au premier passage où la source répond.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .config import (RATTRAPAGE_LIMITE_JOURS, RATTRAPAGE_PERIODE_S,
                     RATTRAPAGE_VERROU_TTL_S, jour_attribution, now_local)
from .database import SessionLocal
from .models import (Alerte, AuditLog, GraviteAlerte, HistoriqueJournalier,
                     StatutAlerte, SuiviJournalier, TraitementJournee,
                     TypeAlerte)

log = logging.getLogger("lss.rattrapage")

# --------------------------------------------------------------- états de suivi
STATUT_EN_COURS = "EN_COURS"
STATUT_TERMINE = "TERMINE"
STATUT_ECHEC = "ECHEC"
STATUT_EN_ATTENTE_SOURCE = "EN_ATTENTE_SOURCE"

# ------------------------------------------------------------- états de source
DISPONIBLE = "DISPONIBLE"            # le portail a répondu, avec des données
VIDE_CONFIRMEE = "VIDE_CONFIRMEE"    # le portail a répondu « 0 » — jour calme RÉEL
INDISPONIBLE = "INDISPONIBLE"        # le portail n'a pas répondu (réseau/HTTP/auth)
NON_CONFIGUREE = "NON_CONFIGUREE"    # pas de jeton / pas de collecteur branché

#: Sources dont l'indisponibilité INTERDIT l'archivage (exigence 5).
SOURCES_BLOQUANTES = ("MZONEX", "CAMTRACKPRO")
#: Sources qui n'apportent que des observations : jamais bloquantes (exigence 12).
SOURCES_NON_BLOQUANTES = ("YMANE",)


# =========================================================== lecture des sources
@dataclass
class LectureSource:
    """Verdict d'UNE source pour UNE journée (exigence 11)."""
    source: str
    etat: str
    items: list = field(default_factory=list)
    erreur: str | None = None
    categorie: str | None = None      # « locale » (notre base/disque) ou « portail »
    detail: str | None = None

    @property
    def bloquee(self) -> bool:
        return self.etat in (INDISPONIBLE, NON_CONFIGUREE)


@dataclass
class LectureJour:
    """Ce que les sources disent d'une journée — AVANT toute écriture."""
    jour: date
    sources: dict = field(default_factory=dict)

    @property
    def items(self) -> list:
        return [it for s in self.sources.values() for it in s.items]

    def etats(self) -> dict:
        return {nom: s.etat for nom, s in sorted(self.sources.items())}

    def resume(self) -> dict:
        return {nom: {"etat": s.etat, "n": len(s.items), "erreur": s.erreur,
                      "categorie": s.categorie, "detail": s.detail}
                for nom, s in sorted(self.sources.items())}

    def bloquantes_indisponibles(self) -> list:
        """Sources BLOQUANTES dont l'état n'autorise pas l'archivage.

        Fail-closed : une source bloquante absente du relevé est traitée comme
        non confirmée (on n'archive pas ce qu'on n'a pas pu vérifier).
        """
        bloquees = []
        for nom in SOURCES_BLOQUANTES:
            src = self.sources.get(nom)
            if src is None or src.bloquee:
                bloquees.append(nom)
        return bloquees

    def echecs(self) -> list:
        """Compat AM-4 : liste « SOURCE (cause) » des sources non confirmées."""
        return [f"{nom} ({s.erreur or s.etat})"
                for nom, s in sorted(self.sources.items()) if s.bloquee]

    def tout_vide_confirme(self) -> bool:
        """Vrai si TOUTES les sources de TRAJETS ont répondu « rien ce jour-là ».

        C'est la seule situation où l'on peut dire « personne n'a roulé » sans
        mentir (exigence 11) — distinction impossible avant la v1.51, où un
        portail muet et un jour calme étaient indiscernables.

        Ym@ne est exclue du verdict : elle ne publie pas de trajets, son état
        « DISPONIBLE » ne dit rien du volume de la journée (exigence 12).
        """
        bloquantes = [self.sources.get(nom) for nom in SOURCES_BLOQUANTES]
        return all(src is not None and src.etat == VIDE_CONFIRMEE
                   for src in bloquantes)


def _source_en_echec(nom: str, exc: Exception) -> LectureSource:
    categorie = "portail"
    try:
        from .scrapers import categorie_erreur
        categorie = categorie_erreur(exc)
    except Exception:                                    # pragma: no cover
        pass
    return LectureSource(nom, INDISPONIBLE,
                         erreur=f"{type(exc).__name__}: {exc}",
                         categorie=categorie)


def _lire_mzonex(jour: date) -> LectureSource:
    try:
        from .api_mzonex import ApiMZoneX, trajet_depuis_api
        bruts = ApiMZoneX().trajets_jour_local(jour)
        items = [it for it in (trajet_depuis_api(t) for t in bruts) if it]
        return LectureSource(
            "MZONEX", DISPONIBLE if items else VIDE_CONFIRMEE, items=items,
            detail=f"{len(bruts)} trajet(s) publié(s)")
    except Exception as exc:
        return _source_en_echec("MZONEX", exc)


def _lire_camtrackpro(jour: date) -> LectureSource:
    try:
        from .api_wialon import ApiWialon, jeton_configure
        from .daily import _cloture_du
        if not jeton_configure():
            return LectureSource("CAMTRACKPRO", NON_CONFIGUREE,
                                 detail="jeton Wialon absent — source non configurée")
        api = ApiWialon()
        try:
            items = list(api.trajets_du_jour(jour, fin_locale=_cloture_du(jour)))
        finally:
            api.fermer()
        return LectureSource("CAMTRACKPRO",
                             DISPONIBLE if items else VIDE_CONFIRMEE,
                             items=items, detail=f"{len(items)} trajet(s)")
    except Exception as exc:
        return _source_en_echec("CAMTRACKPRO", exc)


def etat_ymane() -> LectureSource:
    """Ym@ne apporte des OBSERVATIONS (infractions), jamais des trajets.

    Source NON BLOQUANTE (exigence 12) : son état est rapporté pour
    l'observabilité, mais il n'entre jamais dans la porte d'archivage.
    """
    etat = {}
    try:
        from .scrapers import etat_collecte_memoire
        etat = (etat_collecte_memoire().get("sources") or {}).get("YMANE") or {}
    except Exception:                                    # pragma: no cover
        etat = {}
    erreur = etat.get("derniere_erreur")
    if erreur:
        return LectureSource("YMANE", INDISPONIBLE, erreur=str(erreur)[:300],
                             categorie=etat.get("derniere_erreur_categorie") or "portail",
                             detail="non bloquante (observations)")
    return LectureSource("YMANE", DISPONIBLE,
                         detail="non bloquante (observations)"
                                if etat.get("derniere_reussite")
                                else "état Ym@ne non mesuré sur ce cycle")


def lecture_reelle(jour: date) -> LectureJour:
    """Lecture RÉELLE des trois sources pour `jour`.

    AUCUN test ne doit appeler cette fonction (elle sort sur le réseau) :
    les suites injectent leur propre `lecteur`.
    """
    lecture = LectureJour(jour=jour)
    lecture.sources["MZONEX"] = _lire_mzonex(jour)
    lecture.sources["CAMTRACKPRO"] = _lire_camtrackpro(jour)
    lecture.sources["YMANE"] = etat_ymane()
    return lecture


# ================================================================== verrou jour
def _proprietaire() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def acquerir_verrou_jour(jour: date, *, ttl_s: int | None = None,
                         proprietaire: str | None = None) -> tuple[bool, str]:
    """Exigence 9 — EXCLUSION MUTUELLE inter-processus sur une journée.

    La clé primaire `traitement_journees.jour` fait foi : deux workers qui
    tentent d'insérer la même journée, l'un gagne, l'autre reçoit une
    `IntegrityError` — vérifié par la BASE, pas par une convention. Un verrou
    laissé par un worker mort est repris après `ttl_s` (exigence 6/7 : un
    redémarrage pendant une consolidation ne bloque pas la journée).

    Retourne (acquis ?, raison lisible).
    """
    ttl = RATTRAPAGE_VERROU_TTL_S if ttl_s is None else ttl_s
    proprio = proprietaire or _proprietaire()
    mtn = now_local()
    db = SessionLocal()
    try:
        ligne = db.get(TraitementJournee, jour)
        if ligne is None:
            db.add(TraitementJournee(jour=jour, statut=STATUT_EN_COURS,
                                     proprietaire=proprio, debut=mtn, maj=mtn,
                                     tentatives=1))
            try:
                db.commit()
                return True, "acquis"
            except IntegrityError:
                # un autre worker a inséré la journée entre-temps
                db.rollback()
                return False, "verrou_pris_par_un_autre_worker"
        if ligne.statut == STATUT_EN_COURS:
            age = (mtn - (ligne.maj or ligne.debut or mtn)).total_seconds()
            if age < ttl:
                return False, f"en_cours_depuis_{int(age)}s_par_{ligne.proprietaire}"
            log.warning("Rattrapage : verrou expiré sur %s (%s, il y a %d s) "
                        "— reprise", jour, ligne.proprietaire, int(age))
        ligne.statut = STATUT_EN_COURS
        ligne.proprietaire = proprio
        ligne.maj = mtn
        ligne.tentatives = int(ligne.tentatives or 0) + 1
        db.commit()
        return True, "repris"
    finally:
        db.close()


def _marquer_jour(jour: date, statut: str, *, resume: dict | None = None,
                  erreur: str | None = None, archive: bool | None = None) -> None:
    """Met à jour l'état du jour (transaction courte, jamais bloquante)."""
    db = SessionLocal()
    try:
        ligne = db.get(TraitementJournee, jour)
        if ligne is None:
            ligne = TraitementJournee(jour=jour, debut=now_local(), tentatives=0)
            db.add(ligne)
        ligne.statut = statut
        ligne.maj = now_local()
        if resume is not None:
            ligne.sources_etat = resume
        if erreur:
            ligne.derniere_erreur = erreur[:2000]
        if archive is not None:
            ligne.archive = archive
        db.commit()
    except Exception:                                    # pragma: no cover
        db.rollback()
        log.exception("Rattrapage : impossible de marquer le jour %s", jour)
    finally:
        db.close()


# ============================================================ audit & alertes
def _audit(action: str, details: dict, *, entite: str = "jour",
           entite_id: str | None = None, username: str = "systeme") -> None:
    """Écrit une trace d'audit dans SA PROPRE transaction (jamais avalée par
    le rollback de la journée — exigence 3)."""
    db = SessionLocal()
    try:
        db.add(AuditLog(username=username, action=action, entite=entite,
                        entite_id=entite_id, details=details))
        db.commit()
    except Exception:                                    # pragma: no cover
        db.rollback()
        log.exception("Rattrapage : impossible d'écrire l'audit %s", action)
    finally:
        db.close()


def marqueur_alerte(jour: date) -> str:
    """Clé stable de l'alerte d'une journée — permet la MISE À JOUR plutôt
    que l'empilement d'alertes jumelles (exigence 4, idempotence)."""
    return f"[rattrapage:{jour.isoformat()}]"


def alerter_jour(jour: date, message: str, *,
                 gravite: GraviteAlerte = GraviteAlerte.MOYENNE,
                 type_alerte: TypeAlerte = TypeAlerte.GPS_HORS_LIGNE,
                 lien: str = "/historique") -> str | None:
    """Exigence 4 — alerte VISIBLE créée ou MISE À JOUR (jamais dupliquée).

    L'alerte porte le marqueur `[rattrapage:AAAA-MM-JJ]` : une seule alerte
    ouverte par journée, enrichie à chaque nouvel essai, refermée
    automatiquement dès que la journée est archivée.
    """
    marqueur = marqueur_alerte(jour)
    texte = f"{message} {marqueur}"
    db = SessionLocal()
    try:
        ouverte = db.scalar(select(Alerte).where(
            Alerte.type == type_alerte,
            Alerte.message.like(f"%{marqueur}%"),
            Alerte.statut.in_((StatutAlerte.NOUVELLE, StatutAlerte.VUE)))
            .order_by(Alerte.date_heure.desc()))
        if ouverte is None:
            a = Alerte(date_heure=now_local(), type=type_alerte, gravite=gravite,
                       message=texte, statut=StatutAlerte.NOUVELLE,
                       lien_module=lien)
            db.add(a)
            db.flush()
            db.commit()
            return a.id
        ouverte.message = texte                     # « depuis quand » inchangé
        ouverte.gravite = gravite
        db.commit()
        return ouverte.id
    except Exception:                                    # pragma: no cover
        db.rollback()
        log.exception("Rattrapage : impossible de créer/mettre à jour l'alerte %s",
                      jour)
        return None
    finally:
        db.close()


def clore_alerte_jour(jour: date, *, motif: str) -> bool:
    """Referme l'alerte de la journée dès qu'elle est archivée (exigence 4)."""
    marqueur = marqueur_alerte(jour)
    db = SessionLocal()
    try:
        alerte = db.scalar(select(Alerte).where(
            Alerte.message.like(f"%{marqueur}%"),
            Alerte.statut.in_((StatutAlerte.NOUVELLE, StatutAlerte.VUE))))
        if alerte is None:
            return False
        alerte.statut = StatutAlerte.TRAITEE
        db.add(AuditLog(username="systeme", action="alerte.rattrapage_close",
                        entite="alerte", entite_id=alerte.id,
                        details={"jour": jour.isoformat(), "motif": motif}))
        db.commit()
        return True
    finally:
        db.close()


def _consigner_echec(jour: date, exc: Exception, *, motif: str, phase: str) -> None:
    """Exigence 3 — l'échec est consigné dans une transaction SÉPARÉE.

    Appelée APRÈS l'annulation de la transaction de la journée : rien de ce
    qu'on écrit ici ne peut être perdu par le rollback du jour, et rien de ce
    qu'on écrit ici ne peut « réparer » la journée en échec.
    """
    detail = f"{type(exc).__name__}: {exc}"
    categorie = "portail"
    try:
        from .scrapers import categorie_erreur
        categorie = categorie_erreur(exc)
    except Exception:                                    # pragma: no cover
        pass
    _audit("jour.echec_traitement",
           {"jour": jour.isoformat(), "phase": phase, "motif": motif,
            "erreur": detail[:1000], "categorie": categorie,
            "regle": "v1.51 exigence 3 : échec consigné hors transaction du jour ; "
                     "exigence 2 : les journées suivantes ne sont pas bloquées"},
           entite="suivi")
    alerter_jour(
        jour,
        f"Rattrapage : échec du traitement de la journée du {jour:%d/%m/%Y} "
        f"({phase}) — {detail[:180]}. Reprise automatique au prochain passage ; "
        f"les journées suivantes ne sont pas bloquées.",
        gravite=GraviteAlerte.MOYENNE)
    _marquer_jour(jour, STATUT_ECHEC, erreur=detail, archive=False)


# ================================================================= le cœur
def _etat_du_jour(jour: date) -> dict:
    db = SessionLocal()
    try:
        return {
            "archives": db.scalar(select(func.count(HistoriqueJournalier.id))
                                  .where(HistoriqueJournalier.date_jour == jour)) or 0,
            "suivis": db.scalar(select(func.count(SuiviJournalier.id))
                                .where(SuiviJournalier.date_jour == jour)) or 0,
            "ligne": db.get(TraitementJournee, jour),
        }
    finally:
        db.close()


def traiter_jour(jour: date, *, lecteur=None, motif: str = "catchup",
                 verrou: bool = True) -> dict:
    """Traite UNE journée : lire → décider → consolider → archiver.

    Retourne un rapport ; `statut` ∈ {ARCHIVE, DEJA_ARCHIVE, VIDE_CONFIRME,
    REFUSE_SOURCE, REFUSE_PARTIEL, VERROUILLE, ECHEC, RIEN_A_FAIRE}.

    Exigence 1 : les écritures de la journée vivent dans UNE session, ouverte
    après la lecture réseau et fermée avant toute consignation d'échec.
    """
    from . import daily                                   # import différé (cycle)
    lecteur = lecteur or lecture_reelle
    rapport = {"jour": jour.isoformat(), "statut": "", "archives": 0,
               "consolides": 0, "sources": {}, "raison": None}

    etat = _etat_du_jour(jour)
    ligne = etat["ligne"]

    # ---- 0. journée déjà figée : on n'y touche pas (exigences 6 & 8) --------
    if etat["archives"]:
        rapport["statut"] = "DEJA_ARCHIVE"
        rapport["archives"] = etat["archives"]
        if ligne is None or ligne.statut != STATUT_TERMINE:
            _marquer_jour(jour, STATUT_TERMINE, archive=True,
                          resume={"archives_existantes": etat["archives"]})
        return rapport
    if (ligne is not None and ligne.statut == STATUT_TERMINE
            and etat["suivis"] == 0):
        # jour marqué terminé sans archive : « personne n'a roulé » (déjà
        # confirmé par les sources) et AUCUNE donnée locale depuis — ne pas
        # relire les portails à chaque passage. Si des suivis sont apparus
        # (donnée tardive), on retraite : le marquage ne fige pas le jour.
        rapport["statut"] = "RIEN_A_FAIRE"
        return rapport

    # ---- 1. verrou inter-processus (exigence 9) ---------------------------
    if verrou:
        ok, raison = acquerir_verrou_jour(jour)
        if not ok:
            rapport["statut"] = "VERROUILLE"
            rapport["raison"] = raison
            log.info("Rattrapage : %s déjà en cours de traitement (%s)",
                     jour, raison)
            return rapport

    # ---- 2. lecture réseau HORS transaction (leçon v1.50) -----------------
    try:
        lecture = lecteur(jour)
    except Exception as exc:
        _consigner_echec(jour, exc, motif=motif, phase="lecture_des_sources")
        rapport["statut"] = "ECHEC"
        rapport["raison"] = f"{type(exc).__name__}: {exc}"
        return rapport
    rapport["sources"] = lecture.etats()
    resume = lecture.resume()

    # ---- 3. porte d'archivage (exigences 5, 11, 12) -----------------------
    bloquees = lecture.bloquantes_indisponibles()
    if not lecture.items and lecture.tout_vide_confirme() and etat["suivis"] == 0:
        _audit("jour.vide_confirme",
               {"jour": jour.isoformat(), "sources": resume,
                "regle": "v1.51 exigence 11 : toutes les sources bloquantes ont "
                         "RÉPONDU « aucun trajet » — jour calme confirmé, rien à écrire"})
        _marquer_jour(jour, STATUT_TERMINE, resume=resume, archive=False)
        clore_alerte_jour(jour, motif="jour_vide_confirme")
        rapport["statut"] = "VIDE_CONFIRME"
        return rapport

    if bloquees:
        # la journée se ferme (23:59:59) mais RIEN n'est figé : pas d'archive
        # partielle. On n'appelle JAMAIS archiver_jour sur ce chemin.
        try:
            db = SessionLocal()
            try:
                rapport["consolides"] = daily.consolider_jour(db, jour)
            finally:
                db.close()
        except Exception as exc:
            _consigner_echec(jour, exc, motif=motif, phase="consolidation")
            rapport["statut"] = "ECHEC"
            rapport["raison"] = f"{type(exc).__name__}: {exc}"
            return rapport
        _audit("jour.archive_refusee",
               {"jour": jour.isoformat(), "sources_bloquees": bloquees,
                "sources": resume, "consolides": rapport["consolides"],
                "regle": "v1.51 exigence 5 : source indisponible/non confirmée "
                         "⇒ aucune archive partielle ; reprise automatique"},
               entite="suivi")
        alerter_jour(
            jour,
            f"Rattrapage : journée du {jour:%d/%m/%Y} NON archivée — "
            f"source(s) non confirmée(s) : {', '.join(bloquees)}. "
            f"Les données locales sont conservées et la journée sera archivée "
            f"dès que la source répondra (aucune archive partielle n'est écrite).")
        _marquer_jour(jour, STATUT_EN_ATTENTE_SOURCE, resume=resume, archive=False)
        rapport["statut"] = "REFUSE_SOURCE"
        rapport["raison"] = "source_indisponible:" + ",".join(bloquees)
        return rapport

    # ---- 4. transaction ISOLÉE de la journée (exigences 1 & 8) ------------
    try:
        db = SessionLocal()
        try:
            stats = daily._consolider_et_archiver_jour(db, jour, lecture.items,
                                                       username=motif)
        finally:
            db.close()
    except Exception as exc:
        _consigner_echec(jour, exc, motif=motif, phase="consolidation_archivage")
        rapport["statut"] = "ECHEC"
        rapport["raison"] = f"{type(exc).__name__}: {exc}"
        return rapport

    rapport["consolides"] = int(stats.get("consolides") or 0)
    rapport["archives"] = int(stats.get("archives") or 0)

    if rapport["archives"] == 0 and etat["suivis"] > 0:
        # consolidation faite mais écriture d'archive refusée par un garde-fou
        # (trajet PROVISOIRE résiduel, simulateur…) : on le dit, on n'insiste pas
        _audit("jour.archive_refusee",
               {"jour": jour.isoformat(), "sources": resume,
                "suivis": etat["suivis"],
                "regle": "v1.51 exigences 5/10/13 : archive refusée par un "
                         "garde-fou — voir les audits 'archive.*' du jour"},
               entite="suivi")
        alerter_jour(
            jour,
            f"Rattrapage : journée du {jour:%d/%m/%Y} consolidée mais NON "
            f"archivée (garde-fou d'archivage). Voir le journal d'audit.",
            gravite=GraviteAlerte.CRITIQUE)
        _marquer_jour(jour, STATUT_ECHEC, resume=resume, archive=False,
                      erreur="archive_refusee_par_garde_fou")
        rapport["statut"] = "REFUSE_PARTIEL"
        return rapport

    _marquer_jour(jour, STATUT_TERMINE, resume=resume, archive=bool(rapport["archives"]))
    clore_alerte_jour(jour, motif=f"archive:{motif}")
    rapport["statut"] = "ARCHIVE" if rapport["archives"] else "RIEN_A_FAIRE"
    return rapport


# ============================================================== boucle catch-up
def _jour_de_depart(db) -> date | None:
    return db.scalar(select(func.min(SuiviJournalier.date_jour)))


def jours_a_traiter(cible_hier: date | None = None, *, limite: int | None = None):
    """Liste des journées candidates, de la plus ancienne à la veille."""
    db = SessionLocal()
    try:
        aujour = jour_attribution(now_local())
        hier = cible_hier or (aujour - timedelta(days=1))
        premier = _jour_de_depart(db)
    finally:
        db.close()
    if premier is None or hier >= aujour:
        return []
    jours, jour = [], premier
    maxi = RATTRAPAGE_LIMITE_JOURS if limite is None else limite
    while jour <= hier and (maxi is None or len(jours) < maxi):
        jours.append(jour)
        jour += timedelta(days=1)
    return jours


def rattraper_journees(cible_hier: date | None = None, *, lecteur=None,
                       limite: int | None = None) -> dict:
    """Exigences 2, 7, 8 — rattrape toutes les journées manquées, une par une.

    Une erreur sur une journée n'arrête JAMAIS les suivantes : chaque journée
    est traitée dans son propre appel, son propre verrou et sa propre
    transaction ; l'échec est consigné (transaction séparée) puis on passe à
    la suivante. Relancer cette fonction ne change rien (idempotence).
    """
    from .engine import ensure_suivis_du_jour
    rapport = {"jours_traités": 0, "jours_archivés": 0, "jours_sautés": [],
               "détails": {}, "en_attente_source": [], "echecs": [],
               "verrouilles": [], "recus": 0}
    try:
        jours = jours_a_traiter(cible_hier, limite=limite)
    except Exception:
        log.exception("Rattrapage : impossible de calculer la liste des journées")
        return rapport
    rapport["recus"] = len(jours)

    for jour in jours:
        try:
            res = traiter_jour(jour, lecteur=lecteur)
        except Exception as exc:
            # ceinture et bretelles (exigence 2) : rien ne remonte plus haut
            _consigner_echec(jour, exc, motif="catchup", phase="boucle")
            res = {"jour": jour.isoformat(), "statut": "ECHEC",
                   "raison": f"{type(exc).__name__}: {exc}"}
        statut = res.get("statut")
        # `détails` = journées RÉELLEMENT TRAVAILLÉES (compat du rapport
        # historique) : un jour déjà archivé ou déjà marqué vide n'y figure pas,
        # il est listé dans `jours_sautés`.
        if statut not in ("DEJA_ARCHIVE", "RIEN_A_FAIRE"):
            rapport["détails"][jour.isoformat()] = res
        if statut in ("ARCHIVE", "DEJA_ARCHIVE"):
            rapport["jours_traités"] += 1
            rapport["jours_archivés"] += int(res.get("archives") or 0)
            if statut == "DEJA_ARCHIVE":
                rapport["jours_sautés"].append(jour.isoformat())
        elif statut == "REFUSE_SOURCE":
            rapport["en_attente_source"].append(jour.isoformat())
            rapport["jours_sautés"].append(jour.isoformat())
        elif statut == "VERROUILLE":
            rapport["verrouilles"].append(jour.isoformat())
        elif statut == "ECHEC":
            rapport["echecs"].append(jour.isoformat())

    try:
        db = SessionLocal()
        try:
            ensure_suivis_du_jour(db, jour_attribution(now_local()))
        finally:
            db.close()
    except Exception:
        log.exception("Rattrapage : création des suivis du jour en échec")

    if rapport["jours_traités"] or rapport["jours_sautés"] or rapport["echecs"]:
        log.info("Rattrapage terminé : %s", {k: v for k, v in rapport.items()
                                             if k in ("recus", "jours_traités",
                                                      "jours_archivés", "jour_sautés",
                                                      "jours_sautés", "echecs",
                                                      "en_attente_source", "verrouilles")})
    return rapport


# ============================================================ relance périodique
async def boucle_rattrapage_periodique(intervalle_s: int | None = None) -> None:
    """Exigence 7 — le rattrapage est relancé PÉRIODIQUEMENT, en plus du boot.

    Sans cette boucle, une journée non archivée (source en panne le soir, ou
    archive refusée par un garde-fou) attendait le prochain redémarrage — ce
    qui, sur un poste qui ne redémarre pas, pouvait durer des jours.
    """
    intervalle = max(60, int(intervalle_s or RATTRAPAGE_PERIODE_S))
    log.info("Rattrapage périodique actif (toutes les %d s)", intervalle)
    while True:
        try:
            await asyncio.sleep(intervalle)
            await asyncio.to_thread(rattraper_journees)
        except asyncio.CancelledError:                    # pragma: no cover
            raise
        except Exception:
            log.exception("Rattrapage périodique : échec du passage — le "
                          "suivant aura lieu dans %d s", intervalle)


def etat_rattrapage(db=None) -> dict:
    """Instantané pour /api/sante : ce qui attend, ce qui a échoué."""
    fermer = db is None
    db = db or SessionLocal()
    try:
        lignes = db.scalars(select(TraitementJournee)
                            .order_by(TraitementJournee.jour.desc()).limit(60)).all()
        return {
            "jours": [{"jour": l.jour.isoformat(), "statut": l.statut,
                       "tentatives": l.tentatives, "archive": bool(l.archive),
                       "proprietaire": l.proprietaire,
                       "maj": l.maj.isoformat() if l.maj else None,
                       "derniere_erreur": (l.derniere_erreur or "")[:200] or None}
                      for l in lignes],
            "en_attente_source": [l.jour.isoformat() for l in lignes
                                  if l.statut == STATUT_EN_ATTENTE_SOURCE],
            "echecs": [l.jour.isoformat() for l in lignes if l.statut == STATUT_ECHEC],
            "verrous_actifs": [l.jour.isoformat() for l in lignes
                               if l.statut == STATUT_EN_COURS],
        }
    finally:
        if fermer:
            db.close()

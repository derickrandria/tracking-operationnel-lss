# -*- coding: utf-8 -*-
"""Importateur des infractions **Ym@ne** (§0quinquies decies I1→I4, 25/08/2026).

- I1 : seules des lignes Ym@ne entrent ici — `exterieure=True`, source MZONEX,
  jamais d'écriture locale dans l'onglet (§0nonies C3/AM-5 confirmés).
- I2 : le filtrage du niveau « enregistrement » est fait EN AMONT, dans
  `api_ymane.ApiYmane.normaliser` — ce module ne reçoit que ALERTE / ALARME.
- I4 : l'upsert ne touche JAMAIS le workflow de validation
  (`validation`, `observation`, `validee_par`, `validee_le`) — souveraineté
  de l'exploitant ; une re-collecte ne peut pas écraser une décision.
- Jamais de suppression, jamais de doublon : identifiant externe `ymane_id`
  sinon clé naturelle (jour + heure + véhicule + nom).

Le mapping exact des champs est FIGÉ depuis l'activation (I5 — exécution du
26/08/2026, v1.36) : les items normalisés portent `type_code`, `seuil_unite`
(« kmh » | « s » | « brut ») et `seuil_texte` (verbatim du portail). Les
heuristiques ci-dessous (type, unité de seuil) ne servent plus que de repli
défensif documenté quand une clé manque.
"""
from __future__ import annotations

import re
import threading
from datetime import date, datetime, time

from sqlalchemy import func, select

from .api_ymane import ApiYmane, actif
from .config import (IDENT_PLAQUE_RE, normaliser_libelle, now_local,
                     plaque_depuis_libelle_portail)
from .database import SessionLocal
from .event_bus import publish
from .models import (Alerte, AuditLog, Conducteur, GraviteAlerte,
                     GraviteInfraction, Infraction, SourceEvenement,
                     StatutAlerte, TypeAlerte, TypeInfraction, Vehicule)
from .serializers import s_alerte, s_infraction

import logging
log = logging.getLogger("lss.ymane")

# §0septies decies K2 (27/08/2026) — verrou de passe : un cycle Ym@ne à la
# fois (planifié ou manuel), jamais deux concurremment.
_verrou_cycle = threading.Lock()

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


# ------------------------------------------------------------------ parsing
def _f(x):
    """float tolérant : 90 · « 90.5 » · « 90 km/h » → 90.0 ; sinon None."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = _NUM_RE.search(str(x))
    return float(m.group(0).replace(",", ".")) if m else None


def _i(x):
    f = _f(x)
    return int(round(f)) if f is not None else None


def _parse_jour(txt, defaut: date) -> date | None:
    """« 2026-08-25[ T…] » ou « 25/08/2026 » → date ; vide → défaut ;
    illisible → None (l'item sera écarté, jamais deviné)."""
    s = str(txt or "").strip()
    if not s:
        return defaut
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _parse_heure(it: dict) -> time | None:
    """Heure de l'infraction : champ dédié (« 14:22:10 », « 14:22 »,
    « 14h22 ») sinon partie horaire d'un date_txt datetime ISO."""
    s = str(it.get("heure_txt") or "").strip()
    m = re.match(r"(\d{1,2})[h:](\d{1,2})(?:[:.](\d{1,2}))?", s)
    if not m:                                    # repli : date/heure fusionnées
        m = re.search(r"[T ](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?",
                      str(it.get("date_txt") or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return time(h, mi, int(m.group(3) or 0))


# ------------------------------------------------------------- heuristiques
def _type_depuis_nom(nom: str) -> TypeInfraction:
    """Famille d'infraction déduite du libellé Ym@ne — REPLI défensif
    documenté : le mapping figé par I5 (`type_code` normalisé) prime ;
    cette heuristique ne sert que si la clé est absente/inconnue."""
    n = normaliser_libelle(nom)
    if "vitesse" in n or "speed" in n:
        return TypeInfraction.EXCES_VITESSE
    if "frein" in n:
        return TypeInfraction.FREINAGE_BRUSQUE
    if "accel" in n:
        return TypeInfraction.ACCELERATION_BRUSQUE
    if "ttj" in n:
        return TypeInfraction.DEPASSEMENT_TTJ
    if "tcj" in n:
        return TypeInfraction.DEPASSEMENT_TCJ
    return TypeInfraction.DEPASSEMENT_TCC       # défaut défensif documenté


# ------------------------------------------------------------------ import
def importer_infractions_ymane(db, items: list[dict],
                               maintenant: datetime | None = None) -> dict:
    """Upsert idempotent des items NORMALISÉS (déjà filtrés I2).

    Renvoie les compteurs de la passe. Jamais de suppression ; les champs
    du workflow de validation (I4) ne sont jamais modifiés ici.
    """
    maintenant = maintenant or now_local()
    aujourd_hui = maintenant.date()
    stats = {"recus": len(items), "nouvelles": 0, "maj": 0, "doublons": 0,
             "ignores": 0, "sans_vehicule": 0}

    # Caches de la passe (session sans autoflush : re-vérification immédiate)
    vus_par_id: dict[str, Infraction] = {}
    vus_par_cle: dict[tuple, Infraction] = {}
    creees: set[int] = set()                      # objets créés DANS cette passe
    conducteurs = list(db.scalars(select(Conducteur)).all())
    par_nom = {normaliser_libelle(c.nom_prenom): c for c in conducteurs}
    par_usuel = {normaliser_libelle(c.prenom_usuel): c for c in conducteurs}
    vehicules = {v.plaque: v for v in db.scalars(select(Vehicule)).all()}

    def resoudre_vehicule(plaque_brute):
        plaque = plaque_depuis_libelle_portail(plaque_brute or "")
        if not plaque and plaque_brute:
            candidat = "".join(str(plaque_brute).split()).upper()
            if IDENT_PLAQUE_RE.match(candidat):
                plaque = candidat
        return (vehicules.get(plaque), plaque) if plaque else (None, "")

    def resoudre_conducteur(chauffeur):
        n = normaliser_libelle(chauffeur)
        return par_nom.get(n) or par_usuel.get(n) if n else None

    for it in items:
        jour = _parse_jour(it.get("date_txt"), aujourd_hui)
        heure = _parse_heure(it)
        if jour is None or heure is None:                    # incompressible
            stats["ignores"] += 1
            log.warning("Ym@ne : item sans date/heure exploitable écarté : %s",
                        {k: it.get(k) for k in ("nom", "date_txt", "heure_txt")})
            continue
        vehicule, plaque = resoudre_vehicule(it.get("plaque_brute"))
        if vehicule is None:
            # D1/D4 (recensement N2) restent seuls créateurs de véhicules ;
            # l'item sera rattrapé au cycle suivant une fois le véhicule connu.
            stats["sans_vehicule"] += 1
            log.warning("Ym@ne : véhicule inconnu « %s » — item reporté",
                        it.get("plaque_brute"))
            continue
        # I5 (26/08/2026) : type et unité de seuil normalisés en amont ;
        # repli défensif sur les heuristiques si la clé manque.
        code = it.get("type_code")
        try:
            type_inf = TypeInfraction[code] if code else None
        except KeyError:
            type_inf = None
            log.warning("Ym@ne : type_code inconnu « %s » — repli heuristique", code)
        if type_inf is None:
            type_inf = _type_depuis_nom(it.get("nom") or "Infraction")
        seuil_unite = it.get("seuil_unite") or (
            "kmh" if type_inf == TypeInfraction.EXCES_VITESSE else "s")
        if seuil_unite not in ("kmh", "s", "brut"):
            seuil_unite = "brut"          # inconnu : verbatim, jamais inventé
        seuil_texte = (str(it["seuil_texte"]).strip()[:80]
                       if it.get("seuil_texte") else None)
        gravite = (GraviteInfraction.CRITIQUE if it.get("niveau") == "ALARME"
                   else GraviteInfraction.MOYENNE)
        chauffeur_brut = (str(it["chauffeur"]).strip()[:160]
                          if it.get("chauffeur") else None)
        conducteur = resoudre_conducteur(it.get("chauffeur"))
        yid = (str(it["ymane_id"]).strip()[:80] if it.get("ymane_id") else None)
        cle_naturelle = (jour, heure, vehicule.id, (it.get("nom") or "")[:160])
        # §0octies decies L2 (27/08/2026) — fin verbatim enddatetime ; « — »
        # si le portail ne publie rien (jamais d'invention).
        jfin = _parse_jour(it.get("fin_date_txt"), None)
        hfin = _parse_heure({"heure_txt": it.get("fin_heure_txt") or ""})

        # ---- recherche de l'existant : cache de passe puis base (id puis clé)
        ex = vus_par_id.get(yid) if yid else None
        if ex is None and yid:
            ex = db.scalar(select(Infraction).where(Infraction.ymane_id == yid))
        if ex is None:
            ex = vus_par_cle.get(cle_naturelle)
        if ex is None:
            ex = db.scalar(select(Infraction).where(
                Infraction.date_jour == jour, Infraction.heure == heure,
                Infraction.vehicule_id == vehicule.id,
                Infraction.nom_ymane == (it.get("nom") or "")[:160],
                Infraction.exterieure.is_(True)))
        if ex is not None and id(ex) in creees:
            stats["doublons"] += 1                  # doublon DANS la même passe
            continue

        if ex is None:
            ex = Infraction(
                date_jour=jour, heure=heure, vehicule_id=vehicule.id,
                date_fin=jfin, heure_fin=hfin,                     # L2
                type=type_inf, gravite=gravite,
                source=SourceEvenement.MZONEX, exterieure=True,      # I1
                niveau=it.get("niveau"), nom_ymane=(it.get("nom") or "")[:160],
                chauffeur_brut=chauffeur_brut, ymane_id=yid,
                seuil_unite=seuil_unite, seuil_texte=seuil_texte,
                validation="NON_TRAITEE")                            # I4
            stats["nouvelles"] += 1
            db.add(ex)
            creees.add(id(ex))
            nouvelle = True
        else:
            nouvelle = False
        # ---- champs métier (I4 : validation/observation JAMAIS touchées)
        avant = (ex.niveau, ex.valeur_mesuree, ex.seuil_reference, ex.duree_s,
                 ex.latitude, ex.longitude, ex.chauffeur_brut, ex.conducteur_id,
                 ex.seuil_unite, ex.seuil_texte, ex.date_fin, ex.heure_fin)
        ex.date_jour, ex.heure = jour, heure
        ex.date_fin, ex.heure_fin = jfin, hfin                     # L2
        ex.vehicule_id = vehicule.id
        ex.nom_ymane = (it.get("nom") or "")[:160]
        ex.niveau = it.get("niveau")
        ex.type, ex.gravite = type_inf, gravite
        ex.valeur_mesuree = _f(it.get("valeur"))
        ex.seuil_reference = _f(it.get("seuil"))
        ex.seuil_unite = seuil_unite
        ex.seuil_texte = seuil_texte
        ex.duree_s = _i(it.get("duree_s"))
        ex.latitude, ex.longitude = _f(it.get("lat")), _f(it.get("lng"))
        ex.chauffeur_brut = chauffeur_brut
        ex.conducteur_id = conducteur.id if conducteur else None
        if yid and not ex.ymane_id:
            ex.ymane_id = yid
        apres = (ex.niveau, ex.valeur_mesuree, ex.seuil_reference, ex.duree_s,
                 ex.latitude, ex.longitude, ex.chauffeur_brut, ex.conducteur_id,
                 ex.seuil_unite, ex.seuil_texte, ex.date_fin, ex.heure_fin)
        if not nouvelle and apres != avant:
            stats["maj"] += 1

        if yid:
            vus_par_id[yid] = ex
        vus_par_cle[cle_naturelle] = ex
        if nouvelle:
            try:                                       # temps réel (§9)
                publish("infraction.new", s_infraction(ex))
            except Exception:                          # jamais bloquant
                log.debug("Ym@ne : publication WS impossible", exc_info=True)

    if items:
        db.add(AuditLog(username="collecteur-ymane",
                        action="infraction.import_ymane", entite="infraction",
                        entite_id=None, details=dict(stats)))
    return stats


# ------------------------------------------------------------------- cycle
def cycle_ymane(db=None, api: ApiYmane | None = None) -> dict:
    """Passe de collecte Ym@ne adossée au cycle N2 (I5 — cadence 15 min).

    ACTIF depuis la v1.36 (YMANE_ACTIVE=1 — exécution de I5 du 26/08/2026) :
    le collecteur ouvre une session applicative Ym@ne, lit la fenêtre
    **J-8→J** (amendement A-I5 du 26/08 — retard de traitement Ym@ne > 12 h)
    et importe ALERTE/ALARME (I1/I2). `api` injectable pour les essais.
    """
    if not actif():
        return {"actif": False}
    api = api or ApiYmane()
    jour = now_local().date()
    bruts = api.infractions_recentes(jour)   # A-I5 : fenêtre J-8→J
    filtrees, normes = 0, []
    for b in bruts:
        n = ApiYmane.normaliser(b)
        if n is None:
            filtrees += 1                             # I2 : « enregistrement »
        else:
            normes.append(n)
    propre = db is None
    db = db or SessionLocal()
    try:
        stats = importer_infractions_ymane(db, normes, maintenant=now_local())
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if propre:
            db.close()
    # `recus` = lignes BRUTES du portail (**stats en premier : sa clé `recus`
    # interne — items admis à l'import — n'écrase jamais le compte portail).
    return {"actif": True, "jour": jour.isoformat(), **stats,
            "recus": len(bruts), "filtrees_i2": filtrees}


# ---------------------------------------------------------------------
# §0septies decies K1 (arbitrage LSS 27/08/2026) — observabilité :
# échec = alerte visible (anti-spam) ; guérison = fermeture automatique.
# ---------------------------------------------------------------------
def _raison_fr(exc: Exception) -> str:
    """Exception de collecte → raison EN FRANÇAIS, affichable telle quelle.
    Transport urllib (bibliothèque standard) depuis la v1.40."""
    import socket
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return (f"identifiants refusés par Ym@ne (HTTP {exc.code}) — "
                    "à vérifier dans backend/.env")
        return f"le site Ym@ne répond en erreur (HTTP {exc.code})"
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return "le site Ym@ne ne répond pas (délai dépassé)"
        return ("le site Ym@ne est injoignable — réseau, DNS ou pare-feu "
                "de ce poste")
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "le site Ym@ne ne répond pas (délai dépassé)"
    if isinstance(exc, RuntimeError) and "authentification refusée" in str(exc):
        return "identifiants refusés par Ym@ne à la connexion"
    if isinstance(exc, ValueError):          # JSONDecodeError en fait partie
        return "la réponse de Ym@ne est illisible (pas du JSON exploitable)"
    if isinstance(exc, OSError):             # reste réseau (DNS, refus…)
        return ("le site Ym@ne est injoignable — réseau, DNS ou pare-feu "
                "de ce poste")
    return f"erreur inattendue ({type(exc).__name__})"


def _alerte_ouverte(db):
    return db.scalar(select(Alerte).where(
        Alerte.type == TypeAlerte.COLLECTE_YMANE,
        Alerte.statut.in_((StatutAlerte.NOUVELLE, StatutAlerte.VUE))))


def _consigner_echec(raison: str) -> None:
    """K1 : crée l'alerte d'échec, ou met à jour l'existante — jamais de
    doublon d'alerte ouverte. Audit à chaque essai ; jamais d'exception."""
    db = SessionLocal()
    try:
        mtn = now_local()
        ouverte = _alerte_ouverte(db)
        bas_2 = (" Les infractions ne remonteront pas tant que le problème "
                 "dure ; la collecte retente toutes les 15 minutes et cette "
                 "alerte se refermera seule dès que la collecte fonctionnera "
                 "à nouveau.")
        if ouverte is None:
            a = Alerte(date_heure=mtn, type=TypeAlerte.COLLECTE_YMANE,
                       gravite=GraviteAlerte.MOYENNE,
                       message=(f"Collecte Ym@ne en échec depuis le "
                                f"{mtn:%d/%m/%Y %H:%M} : {raison}." + bas_2),
                       statut=StatutAlerte.NOUVELLE, lien_module="/infractions")
            db.add(a)
            db.flush()
            db.add(AuditLog(username="collecteur-ymane",
                            action="alerte.ymane_echec", entite="alerte",
                            entite_id=a.id,
                            details={"raison": raison, "essais": 1}))
        else:
            essais = int(db.scalar(select(func.count()).select_from(AuditLog)
                                   .where(AuditLog.action.in_(
                                       ("alerte.ymane_echec",
                                        "alerte.ymane_echec_repete")))) or 0)
            ouverte.message = (
                f"Collecte Ym@ne en échec depuis le "
                f"{ouverte.date_heure:%d/%m/%Y %H:%M} : {raison} — "
                f"{essais + 1}e échec consécutif (dernier essai le "
                f"{mtn:%d/%m/%Y %H:%M})." + bas_2)
            db.add(AuditLog(username="collecteur-ymane",
                            action="alerte.ymane_echec_repete",
                            entite="alerte", entite_id=ouverte.id,
                            details={"raison": raison,
                                     "essais": essais + 1}))
            a = ouverte
        db.commit()
        try:
            db.refresh(a)
            publish("alerte.new", s_alerte(a))          # temps réel (§9)
        except Exception:
            log.debug("Ym@ne : publication WS impossible", exc_info=True)
    except Exception:
        db.rollback()
        log.exception("Ym@ne : consignation de l'échec impossible — "
                      "l'échec de collecte, lui, reste journalisé")
    finally:
        db.close()


def _clore_si_guerie() -> bool:
    """K1 : cycle redevenu sain → l'alerte ouverte est refermée
    automatiquement (TRAITEE, motif audité) — jamais effacée."""
    db = SessionLocal()
    try:
        ouverte = _alerte_ouverte(db)
        if ouverte is None:
            return False
        mtn = now_local()
        ouverte.statut = StatutAlerte.TRAITEE
        ouverte.message += (f" — Rétablie le {mtn:%d/%m/%Y %H:%M} : la "
                            "collecte Ym@ne fonctionne à nouveau "
                            "(fermeture automatique).")
        db.add(AuditLog(username="collecteur-ymane",
                        action="alerte.ymane_guerison", entite="alerte",
                        entite_id=ouverte.id,
                        details={"motif": "guérison automatique — collecte "
                                          "Ym@ne redevenue saine"}))
        db.commit()
        log.info("Ym@ne : collecte redevenue saine — alerte %s refermée "
                 "automatiquement (K1)", ouverte.id)
        return True
    except Exception:
        db.rollback()
        log.exception("Ym@ne : fermeture automatique de l'alerte impossible")
        return False
    finally:
        db.close()


def cycle_ymane_avec_alerte(db=None, api: ApiYmane | None = None) -> dict:
    """Cycle Ym@ne + observabilité §0septies decies K1 (27/08/2026).

    - désactivé (YMANE_ACTIVE≠1) → `{"actif": False}` — silence souverain ;
    - autre cycle en cours → `{"actif": True, "ok": False, "conflit": True}` ;
    - échec → alerte COLLECTE_YMANE créée/mise à jour, `ok: False` + raison ;
    - succès → alerte ouverte refermée automatiquement, `ok: True` + stats
      du cycle (inchangées depuis la v1.37, §0quinquies decies A-I5).
    """
    if not actif():
        return {"actif": False}
    if not _verrou_cycle.acquire(blocking=False):
        return {"actif": True, "ok": False, "conflit": True,
                "raison": "une collecte Ym@ne est déjà en cours"}
    try:
        try:
            stats = cycle_ymane(db=db, api=api)
        except Exception as exc:
            raison = _raison_fr(exc)
            log.warning("Ym@ne : cycle en échec — %s (%s)", raison, exc)
            _consigner_echec(raison)
            return {"actif": True, "ok": False, "raison": raison}
        _clore_si_guerie()
        return {"actif": True, "ok": True, **stats}
    finally:
        _verrou_cycle.release()

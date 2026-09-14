"""Seed initial — données réelles des Annexes A/B/C (§15).

- 51 véhicules (Annexe A), 57 chauffeurs (Annexe B), situations (Annexe C) ;
- seuils réglementaires par défaut (§5.8) ;
- utilisateurs de démonstration (un par rôle §2).
"""
import logging
import random

from sqlalchemy import func, select

from .config import calculer_tokens_set, normaliser_libelle
from .database import SessionLocal, Base, engine as _engine
from .engine import SEUILS_DEFAUT
from .models import (Conducteur, ParametrageSeuil, Role, SituationCamion, User,
                     Vehicule)
from .security import hash_password

log = logging.getLogger("lss.seed")

# ------------------------------------------------------- Annexe A — véhicules
VEHICULES = [
    ("0576TCD", "0576TCD/0797TBP"), ("0616TCD", "0616TCD/0537TBP"),
    ("0826TBS", "0826TBS/9867TBD"), ("0906TBV", "0906TBV/0787TBP"),
    ("0916TBV", "0916TBV/1477TBP"), ("0926TBV", "0926TBV/9907TBD"),
    ("0936TBV", "0936TBV/4877TBB"), ("2226TBS", "2226TBS/6217TBE"),
    ("2606TBS", "2606TBS/6197TBB"), ("2736TCC", "2736TCC/2737TCC"),
    ("2746TCC", "2746TCC/2907TCC"), ("3046TBS", "3046TBS/6187TBE"),
    ("3056TBS", "3056TBS/1907TBE"), ("3076TBS", "3076TBS/4867TAU"),
    ("3646TBS", "3646TBS/4617TBH"), ("4006TBS", "4006TBS/0267TBG"),
    ("4296TCC", "4296TCC/3307TCC"), ("4526TCC", "4526TCC/3317TCC"),
    ("4566TCC", "4566TCC/5607TCC"), ("4736TCC", "4736TCC/5307TCC"),
    ("4866TBU", "4866TBU/4937TBE"), ("4876TBU", "4876TBU/6477TBE"),
    ("4886TBU", "4886TBU/4837TBB"), ("4926TBU", "4926TBU/6097TBE"),
    ("5156TBV", "5156TBV/7337TAV"), ("5186TBV", "5186TBV/3397TBB"),
    ("5306TBU", "5306TBU/9847TBD"), ("5316TBU", "5316TBU/6197TBE"),
    ("5346TBU", "5346TBU/6827TBK"), ("5376TBV", "5376TBV/4957TBE"),
    ("5446TBS", "5446TBS/4047TBH"), ("5506TBS", "5506TBS/0267TBG"),
    ("5616TCE", "5616TCE/5617TCE"), ("5626TCE", "5626TCE/0537TAV"),
    ("5646TCE", "5646TCE/5617TCE"), ("5706TBS", "5706TBS/4657TBH"),
    ("5716TBS", "5716TBS/3407TBB"), ("6256TCE", "6256TCE/6257TCE"),
    ("6546TCE", "6546TCE/9747TBD"), ("7306TCE", "7306TCE/9737TBD"),
    ("7766TBL", "7766TBL/7767TBL"), ("7936TCB", "7936TCB/5127TCB"),
    ("7946TCB", "7946TCB/5057TCB"), ("8076TCB", "8076TCB/0527TBP"),
    ("8086TCB", "8086TCB/0557TBP"), ("8116TCB", "8116TCB/1247TCC"),
    ("8806TCB", "8806TCB/1287TCC"), ("9176TCC", "9176TCC/6107TCC"),
    ("9186TCC", "9186TCC/7997TCC"), ("9226TCC", "9226TCC/4927TBE"),
    ("9236TCC", "9236TCC/6347TCC"), ("9806TCD", "9806TCD/1467TBP"),
    ("9816TCD", "9816TCD/0217TAU"), ("9856TCD", "9856TCD/4947TBE"),
]

# ------------------------------------------------------- Annexe B — chauffeurs
CHAUFFEURS = [
    ("RAKOTONANDRASANA Jacky", "JACKY", "038 48 930 63"),
    ("RAKOTOARIMANANA Jean Doré", "DORÉ", "034 28 685 51"),
    ("BEZAKA Valérien", "VALÉRIEN", "034 29 096 02"),
    ("RAKOTORAHALAHY Heriniaina Clément", "CLÉMENT", "034 29 244 99"),
    ("RAZAFITSIHOARANA Victorino Chicauro", "VICTORINO", "034 12 430 27"),
    ("RAZANATSOA Tandra Vincelin", "VINCELIN", "034 09 012 22"),
    ("RAZAFIMANDIMBY Hajason Remi", "HAJASON", "034 44 472 37"),
    ("RAMAMIARIVONY Santatriniaina Feno Hasina", "HASINA", "038 45 138 10"),
    ("CHRETIEN Rizzah Albertini", "RIZZAH", "038 60 917 22"),
    ("RALAIMBOANDRIMAHENINA Nirina Albert", "NIRINA", "038 56 616 31"),
    ("ANDRIAMAMPIANINA Lahatra Faneva Omega", "LAHATRA", "034 70 527 94"),
    ("SAIDI BESLMO", "SAIDI", "034 82 671 23"),
    ("RAVITA Jaofara Jean", "JAOFARA", "034 02 941 75"),
    ("RANDRIANANTENAINA Gentil Patrick", "PATRICK", "034 06 698 22"),
    ("JEANNOT Francomme", "FRANCOMME", "034 69 133 83"),
    ("RANDRIARIMANANA Jean Claude", "CLAUDE", "034 96 949 45"),
    ("RAKOTOMALALA Johnny horlando", "JOHNNY", "034 41 796 16"),
    ("LEMALADE Bertho Jean Chirack", "CHIRACK", "038 85 134 71"),
    ("RAKOTONIRIANA Marcelin", "MARCELIN", "034 62 360 67"),
    ("PHILEMONT RAVELOANJARA", "PHILEMONT", "038 98 356 42"),
    ("RAJAONARISON Bien Aimé", "BIEN AIMÉ", "034 45 199 71"),
    ("RAZAFINOMENJANAHARY Aimé Olivier", "AIMÉ OLIVIER", "038 06 224 10"),
    ("RAMAMIARIVONY Jean Bienvenue", "BIENVENUE", "034 59 834 35"),
    ("ANDRIANASOLO Mamison Herimbolamanana", "MAMISON", "034 12 492 33"),
    ("REHASY Fanahintsoa Thiernot", "THIERNOT", "034 57 741 70"),
    ("RAMAHALEO Jean", "RAMAHALEO", "034 18 976 96"),
    ("ANDRIAMISOMA Elysé André", "ELYSÉ", "034 11 208 36"),
    ("MANANKEVITRA Jefferson", "JEFFERSON", "034 07 144 66"),
    ("RABARY Andry", "RABARY ANDRY", "034 79 540 00"),
    ("VELOMARO", "VELOMARO", "034 05 300 27"),
    ("RAZAFINDRAINIBE Andrianaina Victor", "VICTOR", "034 79 992 46"),
    ("RAKOTONINDRINA Solofohery Alain Dominique", "DOMINIQUE", "034 28 112 97"),
    ("NOMENJANAHARY Sendrika Mourtazah", "MOURTAZAH", "032 73 142 78"),
    ("RATIANDRAINIBE Aimé Mys de Padoue", "PADOUE", "033 41 482 54"),
    ("BEZANDRY Hubert Ludovic", "HUBERT", "034 14 339 69"),
    ("RABENEFITRA Florent Jules", "FLORENT", "034 98 577 59"),
    ("BESILAMO Romuald Angelo", "ANGELO", "034 07 401 21"),
    ("RABENIAINA Andry Jean Mickael", "ANDRY MICKAEL", "034 22 699 27"),
    ("VONJY Herindrain'Asolo Zohindrazana", "HERINDRAIN'ASOLO", "038 28 794 38"),
    ("MICHAËL Justin", "MICHAËL", "034 51 386 68"),
    ("ANDRIANASOLO Maminiaina Eddy", "EDDY", "038 62 462 13"),
    ("RAKOTONDRAMANANA Jean Rolland", "ROLLAND", "034 44 835 83"),
    ("TOTO Ibrahim Abdillah Sabity", "IBRAHIM", "034 69 317 06"),
    ("RAMBINITSOA Jean Charles", "JEAN CHARLES", "034 91 523 57"),
    ("RABARISON Benjamin", "BENJAMIN", "034 14 796 62"),
    ("RAHARISON Elysée Joseph", "RAHARISON", "034 48 148 34"),
    ("RANAIVOSON Georges justin", "GEORGES", "034 78 135 85"),
    ("RABESON Fanomezana Rija Nirina", "RIJA", "034 20 080 65"),
    ("JAOVELO Frank", "JAOVELO", "034 39 551 57"),
    ("RASOLONJATOVO Albert", "RASOLONJATOVO", "038 90 696 47"),
    ("RATSIFERANA Arsène richard", "ARSÈNE", "034 11 486 09"),
    ("JOMA Alexandre", "ALEXANDRE", "038 11 119 62"),
    ("RAKOTOVAO", "HAJANIAINA", "034 54 795 05"),
    ("RABEMIARAMONINA Solofo Jean Michel", "MICHEL", "034 19 719 60"),
    ("TOLOJANAHARY", "MITANTSOA", "034 19 319 17"),
    ("TODIVELO", "ERIC", "038 56 057 42"),
    ("RAHARISON Septo", "SEPTO", None),
]

# ------------------------------------------------------- Annexe C — situations
SITUATIONS = [
    "En attente bon après déchargement ABI", "En attente bon après déchargement ABE",
    "En attente bon après déchargement FNR", "En attente bon après déchargement MNKR",
    "En attente bon après déchargement MRDV", "En attente bon après déchargement MMG",
    "En transit pour chargement", "En attente de chargement", "En cours de chargement",
    "En transit pour livraison TMT-MMG", "En transit pour livraison TMT-TANA",
    "En transit pour livraison TANA-FNR", "En transit pour livraison TANA-ABE",
    "En transit pour livraison TANA-MNKR", "En transit pour livraison TANA-MRDV",
    "En attente de déchargement", "En cours de déchargement",
    "Retour Base après déchargement ABI", "Retour Tana après déchargement ABE",
    "Retour Tana après déchargement FNR", "Retour Tana après déchargement MNKR",
    "Retour Tana après déchargement MRDV",
    "Départ prévu pour livraison TMT-TANA ce jour",
    "Chauffeur malade", "Chauffeur en formation PATH", "Chauffeur en congé",
    "Chauffeur coach", "En cours remplacement TRR/RMQ", "CC en maintenance",
    "Chauffeur suspendu pour coulage", "Chauffeur suspendu pour infraction",
    "Attente chauffeur", "En attente contrôle MADAUTO", "Contrôle MADAUTO ce jour",
    "En attente contrôle APAVE", "Contrôle APAVE ce jour", "Barémage ce jour",
    "En attente RDV pour barémage", "Attente confirmation APAVE",
    "En attente certificat barémage", "Vitting ce jour",
    "Attente Test compteur après barémage", "En cours remplacement TRR",
    "En cours remplacement RMQ", "Attente installation caméra anti-fatigue",
    "En attente intervention CAMTRACK", "Attente certificat de barémage",
    "Attente de barémage", "Repos hebdomadaire", "Test compteur ce jour",
    "En attente code de validation LP", "Indisponible",
    "Chauffeur suspendu pour perte de note", "CC immobilisé suite accident mortel",
    "Visite OSTIE ce jour", "En attente formation SCP",
    "En attente évolution du passage du cyclone", "Repos chauffeur",
]

MARQUES = ["SHACMAN F3000", "SINOTRUK HOWO", "RENAULT C380", "MERCEDES ATEGO", "VOLVO FM"]
CAPACITES = [30000, 33000, 36000, 40000]

# Flotte mixte (Addendum v1.2 §7.1, confirmée par les captures réelles) :
# véhicules visibles sur le portail CamtrackPro « Camtrack SARL » — les autres
# remontent via MZoneX (« Lss Tracking », groupe LSS (LPSA)).
VEHICULES_CAMTRACKPRO = {
    "0826TBS", "0906TBV", "3076TBS", "4296TCC", "5346TBU", "5506TBS",
    "5616TCE", "5626TCE", "5646TCE", "5716TBS", "6256TCE", "6546TCE",
    "7306TCE", "7766TBL", "9176TCC",
}


def seed_si_vide():
    from datetime import date, datetime, timedelta
    Base.metadata.create_all(bind=_engine)
    db = SessionLocal()
    try:
        if db.scalar(select(func.count(User.id))) == 0:
            db.add_all([
                User(username="admin", password_hash=hash_password("Admin@2026"),
                     nom_complet="Administrateur LSS", role=Role.ADMIN),
                User(username="tracking", password_hash=hash_password("Tracking@2026"),
                     nom_complet="Responsable Tracking", role=Role.TRACKING),
                User(username="consultation", password_hash=hash_password("Consult@2026"),
                     nom_complet="Direction / Contrôle", role=Role.CONSULTATION),
            ])
            log.info("Utilisateurs créés (admin / tracking / consultation)")

        # Seuils : insertion initiale ET mise à jour additive — une base
        # existante reçoit automatiquement les nouvelles clés (ex. Addendum
        # v1.4 §2.5) sans perdre les valeurs déjà personnalisées.
        cles_existantes = {r.cle for r in db.scalars(select(ParametrageSeuil))}
        for cle, (val, type_v, desc) in SEUILS_DEFAUT.items():
            if cle not in cles_existantes:
                db.add(ParametrageSeuil(cle=cle, valeur=val, type_valeur=type_v,
                                        description=desc))
                log.info("Seuil ajouté : %s = %s", cle, val)

        if db.scalar(select(func.count(SituationCamion.id))) == 0:
            for i, lib in enumerate(SITUATIONS):
                db.add(SituationCamion(libelle=lib, ordre=i))


        if db.scalar(select(func.count(Conducteur.id))) == 0:
            rng = random.Random(20260730)
            conducteurs = []
            for i, (nom, usuel, tel) in enumerate(CHAUFFEURS, start=1):
                statut = "ACTIF"
                if i == 53:
                    statut = "CONGÉ"
                elif i == 54:
                    statut = "SUSPENDU"
                c = Conducteur(nom_prenom=nom, prenom_usuel=usuel,
                               matricule=None, code_badge_mzonex=None,
                               nom_normalise=normaliser_libelle(nom),
                               tokens_set=calculer_tokens_set(nom),
                               telephone=tel, statut=statut)
                db.add(c)
                conducteurs.append(c)
            db.flush()

            disponibles = [c for c in conducteurs if c.statut == "ACTIF"]
            for i, (plaque, desc) in enumerate(VEHICULES):
                statut = "ACTIF"
                if plaque in ("0926TBV", "5716TBS"):
                    statut = "MAINTENANCE"
                conducteur = disponibles[i] if i < len(disponibles) else None
                est_camtrack = plaque in VEHICULES_CAMTRACKPRO
                plateforme_gps = "CAMTRACKPRO" if est_camtrack else "MZONEX"
                if conducteur:
                    if not est_camtrack:
                        # Flotte MZoneX : driverKeyCode officiel (numérique)
                        badge_code = 10000 + i
                        conducteur.code_badge_mzonex = badge_code
                        conducteur.matricule = str(badge_code)
                    else:
                        # Flotte CamtrackPro : code vide
                        conducteur.code_badge_mzonex = None
                        conducteur.matricule = None

                db.add(Vehicule(
                    plaque=plaque, description=desc, marque=MARQUES[i % len(MARQUES)],
                    capacite=CAPACITES[i % len(CAPACITES)], statut=statut,
                    gps_associe=f"OBC-{plaque}",
                    plateforme_gps=plateforme_gps,
                    conducteur_actuel_id=conducteur.id if conducteur else None))
            db.flush()
        else:
            # Complétion des véhicules manquants sur une base existante
            plaques_existantes = {v.plaque for v in db.scalars(select(Vehicule))}
            for i, (plaque, desc) in enumerate(VEHICULES):
                if plaque not in plaques_existantes:
                    est_camtrack = plaque in VEHICULES_CAMTRACKPRO
                    plateforme_gps = "CAMTRACKPRO" if est_camtrack else "MZONEX"
                    v_new = Vehicule(
                        plaque=plaque, description=desc,
                        marque="MERCEDES ATEGO",
                        capacite=36000, statut="ACTIF",
                        gps_associe=f"OBC-{plaque}",
                        plateforme_gps=plateforme_gps
                    )
                    db.add(v_new)
            db.flush()

        # Historique de démonstration : quelques jours archivés réalistes afin
        # que le module Historique et les courbes 30 jours soient exploitables
        # dès le premier lancement (la production les génère au cycle de minuit).
        from .models import HistoriqueJournalier, Infraction, GraviteInfraction, TypeInfraction, SourceEvenement
        db.flush()  # la session est en autoflush=False : matérialiser les véhicules seedés
        if db.scalar(select(func.count(HistoriqueJournalier.id))) == 0 and \
                db.scalar(select(func.count(Vehicule.id))) > 0:
            from datetime import date, datetime, timedelta
            rng = random.Random(777)
            auj = date.today()
            vehicules = db.scalars(select(Vehicule).where(Vehicule.statut == "ACTIF")).all()
            types = [(TypeInfraction.EXCES_VITESSE, GraviteInfraction.MOYENNE),
                     (TypeInfraction.FREINAGE_BRUSQUE, GraviteInfraction.FAIBLE),
                     (TypeInfraction.ACCELERATION_BRUSQUE, GraviteInfraction.FAIBLE)]
            for recul in (6, 5, 4, 3, 2, 1):
                jour = auj - timedelta(days=recul)
                if jour in (date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)):
                    continue
                for v in rng.sample(list(vehicules), k=min(len(vehicules), rng.randint(30, 40))):
                    c = v.conducteur_actuel
                    tcj = rng.randint(3 * 3600, 8 * 3600 + 1800)
                    pauses = rng.randint(1800, 5400)
                    ttj = min(86400, tcj + pauses)
                    dep = datetime.combine(jour, datetime.min.time()).replace(hour=rng.randint(5, 7))
                    nb_inf = rng.choices([0, 1, 2], weights=[70, 22, 8])[0]
                    tcj_str = f"{tcj // 3600:02d}:{(tcj % 3600) // 60:02d}"
                    ttj_str = f"{ttj // 3600:02d}:{(ttj % 3600) // 60:02d}"
                    pause_str = f"{pauses // 3600:02d}:{(pauses % 3600) // 60:02d}"
                    db.add(HistoriqueJournalier(
                        date_jour=jour, annee=jour.year, mois=jour.month,
                        vehicule_id=v.id, conducteur_id=c.id if c else None,
                        donnees={
                            "plaque": v.plaque,
                            "conducteur": {"prenom_usuel": c.prenom_usuel, "nom_prenom": c.nom_prenom} if c else None,
                            "situation": "Repos chauffeur", "statut_camion": "LIBRE",
                            "depot_recepteur": None,
                            "distributeur": None,
                            "produit": None,
                            "numero_ot": None,
                            "heure_depart": dep.isoformat(),
                            "arret_final": f"{rng.randint(15, 19):02d}:{rng.randint(0, 59):02d} · Base LSS — Antananarivo",
                            "tcc_s": 0, "tcc_secondes": 0, "tcc_str": "00:00",
                            "tcj_s": tcj, "tcj_secondes": tcj, "tcj_str": tcj_str,
                            "ttj_s": ttj, "ttj_secondes": ttj, "ttj_str": ttj_str,
                            "total_pause_s": pauses, "pauses_secondes": pauses, "total_pause_str": pause_str,
                            "km_parcourus": round(rng.uniform(120, 480), 1),
                            "nb_trajets": rng.randint(3, 7), "trajets": [],
                            "flag_tcj": bool(tcj > 36000),
                            "flag_ttj": bool(ttj > 43200),
                            "flag_tcc": False,
                        },
                        nb_infractions=nb_inf, nb_alertes=rng.randint(0, 3)))
                    for _ in range(nb_inf):
                        type_i, grav = rng.choice(types)
                        heure = dep + timedelta(hours=rng.randint(1, 9), minutes=rng.randint(0, 59))
                        db.add(Infraction(
                            date_jour=jour, heure=heure.time().replace(microsecond=0),
                            conducteur_id=c.id if c else None, vehicule_id=v.id,
                            type=type_i, gravite=grav,
                            valeur_mesuree=float(rng.randint(91, 115)) if type_i == TypeInfraction.EXCES_VITESSE else None,
                            seuil_reference=90.0 if type_i == TypeInfraction.EXCES_VITESSE else None,
                            source=SourceEvenement.SIMULATEUR,
                            adresse=rng.choice(["RN2 · PK 74 (avant Moramanga)", "RN2 · PK 201 (après Beforona)",
                                                "RN7 · PK 96 (avant Antsirabe)"])))
            log.info("Historique de démonstration généré (jours passés archivés)")

        db.commit()
    finally:
        db.close()

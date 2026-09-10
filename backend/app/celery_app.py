"""Celery (production, §4/§8/§10) — planificateur des traitements asynchrones.

En mode autonome (ce livrable), les mêmes traitements tournent dans des
tâches asyncio (main.py) ; en production multi-services, utiliser Celery :

    celery -A app.celery_app worker --loglevel=info
    celery -A app.celery_app beat --loglevel=info

Variables : REDIS_URL=redis://localhost:6379/0
"""
import os

from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery = Celery("lss", broker=REDIS_URL, backend=REDIS_URL)
celery.conf.timezone = "Indian/Antananarivo"


@celery.task
def collecte_gps():
    """Collecte API MZoneX/CamtrackPro (§10) — planifiée toutes les 10 s."""
    from .scrapers import (SOURCES, MZoneXCollector,
                           synchroniser_trajets_valides)
    source = os.getenv("COLLECTOR_SOURCE", "MZONEX")
    if source.upper() == "SIMULATEUR":
        return 0
    if source.upper() == "MIXTE":
        n1 = MZoneXCollector().run()
        n2 = synchroniser_trajets_valides("MIXTE")
        return {"niveau1": n1, "niveau2": n2}
    classe = SOURCES.get(source.upper())
    if classe is None:
        raise ValueError(f"Source de collecte inconnue : {source}")
    return classe().run()


@celery.task
def chien_de_garde():
    """GPS hors ligne, immobilisations, missions retardées (§6.5)."""
    from .engine import boucle_surveillance
    boucle_surveillance()


@celery.task
def cycle_minuit():
    """Archivage + nouvelle journée + reset sélectif (§8)."""
    from datetime import date, timedelta
    from .daily import executer_cycle_quotidien
    aujourd = date.today()
    return executer_cycle_quotidien(aujourd - timedelta(days=1), aujourd)


celery.conf.beat_schedule = {
    "collecte-gps": {"task": "app.celery_app.collecte_gps", "schedule": 10.0},
    "chien-de-garde": {"task": "app.celery_app.chien_de_garde", "schedule": 60.0},
    "cycle-minuit": {"task": "app.celery_app.cycle_minuit",
                     "schedule": crontab(hour=0, minute=0)},
}

"""Objets partagés, verrous et états transversaux du moteur réglementaire."""
import logging
from threading import RLock

log = logging.getLogger("lss.engine")

PUBLISH_ENABLED = {"on": True}
HOOKS_TRAJET_CLOTURE: list = []
_lock = RLock()
_flags: dict = {}

_ABSENTS_SIGNALES: set = set()
_D5_DEJA_LOGUES: set = set()
_DERNIERES_INTEGRATIONS: dict = {}
_EP_BADGE: dict = {}
_EP_VITESSE: dict = {}

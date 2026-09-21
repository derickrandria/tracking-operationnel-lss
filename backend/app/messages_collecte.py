"""R7 (21/09/2026) — MESSAGES DE COLLECTE : la cause RÉELLE, jamais supposée.

Module AUTONOME (stdlib uniquement), partagé par `/api/sante` et par la grille
de suivi : une seule vérité pour l'écran et pour l'API.

Défaut corrigé : l'écran affichait « collecte MZONEX bloquée localement — base
verrouillée » dès que la source figurait dans `sources_bloquees_localement`
(c'est-à-dire « attente SQLite »). Un dépassement de budget passé à attendre le
PORTAIL, une pagination lente ou une écriture interrompue par l'échéance
produisaient le même bandeau : l'exploitant cherchait un verrou inexistant.

Règle : le message se compose de TROIS informations DISTINCTES fournies par la
passe — l'ISSUE (comment elle s'est arrêtée), la CLASSE (pourquoi) et la PHASE
(où le temps a été passé). Un dépassement de budget se dit comme tel, quelle que
soit la phase ; « base de données » n'apparaît que si la classe est réellement
`attente_sqlite` ET qu'aucun dépassement n'est en cause.
"""

# Phases où le temps est consommé CÔTÉ PORTAIL (par opposition à nos écritures).
PHASES_PORTAIL = ("authentification", "attente_http", "pagination", "vehicule")

PHASES_LISIBLES = {
    "attente_http": "attente du portail",
    "authentification": "authentification",
    "pagination": "lecture paginée",
    "vehicule": "traitement par véhicule",
    "parsing": "lecture des données",
    "ecriture": "écriture en base",
    "verrou": "attente du verrou",
    "inconnue": "non déterminée",
}


def message_collecte(source: str, statut: str | None = None,
                     classe: str | None = None, phase: str | None = None,
                     raison: str | None = None, issue: str | None = None) -> dict:
    """Message adapté à la cause réelle + action attendue de l'exploitant."""
    phase_txt = PHASES_LISIBLES.get(str(phase or "").lower(), "non déterminée")
    motif = f" (motif : {raison})" if raison else ""
    # 1) Un DÉPASSEMENT DE BUDGET se dit comme tel — même si la phase est
    #    l'écriture : « budget dépassé pendant la phase écriture » n'est pas
    #    « base verrouillée ». La classe dit la CAUSE, l'issue dit l'ARRÊT.
    if issue == "BUDGET_DEPASSE" or classe == "budget_depasse" \
            or statut == "COLLECTE_BUDGET_DEPASSE":
        message = (f"Collecte {source} interrompue : budget dépassé pendant la "
                   f"phase {phase_txt}{motif}. Nouvelle tentative prévue.")
        action = ("Aucune action : la collecte reprendra automatiquement au "
                  "prochain cycle.")
    elif classe == "portail_lent":
        message = (f"Collecte {source} ralentie : le portail répond lentement "
                   f"pendant la phase {phase_txt}. La collecte reprendra au "
                   f"prochain cycle ; rien à faire côté serveur.")
        action = "Surveiller : si cela dure, vérifier la disponibilité du portail."
    elif classe == "portail_indisponible":
        message = (f"Collecte {source} interrompue : la plateforme du portail ne "
                   f"répond pas. Les trajets manquants seront complétés après "
                   f"rétablissement de la source.")
        action = "Contacter le portail si la panne persiste."
    elif classe == "attente_sqlite":
        message = (f"Collecte {source} interrompue : la base de données a fait "
                   f"attendre l'écriture (phase {phase_txt}). Nouvelle tentative "
                   f"prévue.")
        action = "Action technique côté serveur (base/écriture), pas côté portail."
    elif classe == "verrou_occupe":
        message = (f"Collecte {source} en attente : une autre tâche détient le "
                   f"verrou de cette source. Aucune donnée n'a été écrite par "
                   f"cette passe.")
        action = "Attendre la fin de la tâche en cours ; ne pas supprimer de verrou."
    elif classe == "configuration_absente":
        message = (f"Collecte {source} impossible : la configuration de la source "
                   f"est absente (jeton ou identifiants).")
        action = "Compléter la configuration de la source, puis relancer."
    elif statut == "COLLECTE_EN_COURS":
        message = f"Collecte {source} en cours (phase {phase_txt})."
        action = "Aucune action."
    elif classe or statut in ("COLLECTE_ECHOUEE", "COLLECTE_DEGRADEE"):
        message = (f"Collecte {source} en échec (phase {phase_txt}). "
                   f"Diagnostic dans /api/sante.")
        action = "Consulter la classe d'erreur et le journal serveur."
    else:
        message = f"Collecte {source} : aucune anomalie active."
        action = "Aucune action."
    # CAUSE PUBLIÉE : la classe dit l'ISSUE (budget_depasse…) ; quand le budget a
    # été consommé par le PORTAIL, la cause publiée est « portail_lent » — le
    # frontend garde ainsi la distinction, sans jamais maquiller l'issue.
    cause = classe
    if issue == "BUDGET_DEPASSE" and str(phase or "").lower() in PHASES_PORTAIL:
        cause = "portail_lent"
    return {"source": source, "statut": statut, "classe": classe, "cause": cause,
            "phase": phase, "issue": issue, "raison_annulation": raison,
            "message": message, "action": action}


def messages_par_source(metriques: dict | None = None) -> dict[str, dict]:
    """Un message par source, à partir de l'état publié par `scrapers`.

    La PHASE retenue est, dans l'ordre : la phase de l'erreur active, puis la
    phase de la passe EN COURS, puis celle de la dernière passe publiée — jamais
    une valeur inventée.
    """
    messages: dict[str, dict] = {}
    for source, etat in (metriques or {}).items():
        erreur = (etat or {}).get("derniere_erreur") or {}
        en_cours = (etat or {}).get("passe_actuelle") or {}
        derniere = (etat or {}).get("derniere_passe") or {}
        cycle = (etat or {}).get("cycle") or {}
        messages[source] = message_collecte(
            source,
            statut=("COLLECTE_EN_COURS" if cycle.get("en_cours") else None),
            classe=erreur.get("classe"),
            phase=(erreur.get("phase") or erreur.get("etape")
                   or en_cours.get("phase") or derniere.get("phase")
                   or en_cours.get("etape_bloquante")
                   or derniere.get("etape_bloquante")),
            raison=erreur.get("raison_annulation"),
            issue=(erreur.get("issue") or en_cours.get("issue")
                   or derniere.get("issue")))
    return messages

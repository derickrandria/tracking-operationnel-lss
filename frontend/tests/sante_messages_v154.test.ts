/**
 * R7/v1.54 (21/09/2026) — TEST FRONTEND : l'écran dit la CAUSE RÉELLE.
 *
 * Runner : `node --test` (intégré à Node ≥ 22, aucune dépendance ajoutée).
 * Lancement : `npm test` depuis `frontend/`.
 *
 * Le défaut corrigé : `/api/sante` indiquait un DÉPASSEMENT DE BUDGET pendant
 * la phase d'écriture, et l'écran affichait « collecte MZONEX bloquée
 * localement — base verrouillée » (message réservé aux attentes SQLite), ce qui
 * envoyait l'exploitant chercher un verrou inexistant.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  estBaseVerrouillee,
  libellePhase,
  messageCollecte,
  PHASES_LISIBLES,
} from "../src/lib/santeMessages.ts";

// ─────────────────────────── le cas signalé (MZONEX, budget, écriture)
test("budget dépassé pendant l'écriture → message de BUDGET, jamais « base verrouillée »", () => {
  const msg = messageCollecte("MZONEX", {
    statut: "COLLECTE_BUDGET_DEPASSE",
    classe: "budget_depasse",
    phase: "ecriture",
    issue: "BUDGET_DEPASSE",
  });
  assert.ok(msg);
  assert.match(msg!, /budget dépassé pendant la phase écriture en base/);
  assert.match(msg!, /Nouvelle tentative prévue/);
  assert.equal(/base verrouillée|bloquée localement/i.test(msg!), false);
  assert.equal(estBaseVerrouillee({ classe: "budget_depasse", issue: "BUDGET_DEPASSE" }), false);
});

test("budget dépassé pendant l'attente HTTP → phase « attente du portail »", () => {
  const msg = messageCollecte("MZONEX", {
    statut: "COLLECTE_BUDGET_DEPASSE",
    classe: "portail_lent",
    phase: "attente_http",
    issue: "BUDGET_DEPASSE",
  });
  assert.match(msg!, /budget dépassé pendant la phase attente du portail/);
  assert.equal(/base verrouillée/i.test(msg!), false);
});

test("budget dépassé sans phase connue → on le DIT (« non déterminée »)", () => {
  const msg = messageCollecte("N2_MIXTE", {
    statut: "COLLECTE_BUDGET_DEPASSE",
    classe: "budget_depasse",
    phase: "inconnue",
    issue: "BUDGET_DEPASSE",
  });
  assert.match(msg!, /phase non déterminée/);
});

test("dépassement déjà enregistré à l'étape « inconnue » : la PHASE réelle gagne", () => {
  const msg = messageCollecte("MZONEX", {
    classe: "attente_sqlite",
    phase: "ecriture",
    issue: "BUDGET_DEPASSE",
    raison_annulation: "surveillance_limite_depassee",
  });
  assert.match(msg!, /budget dépassé/);
  assert.match(msg!, /écriture en base/);
  assert.match(msg!, /motif : surveillance_limite_depassee/);
});

// ─────────────────────────── chaque classe a SON message (aucun regroupement)
test("portail lent → message de lenteur, pas de panne ni de verrou", () => {
  const msg = messageCollecte("MZONEX", { classe: "portail_lent", phase: "pagination" });
  assert.match(msg!, /portail répond lentement/);
  assert.match(msg!, /lecture paginée/);
  assert.equal(/verrouillée|en panne/i.test(msg!), false);
});

test("attente SQLite → c'est le SEUL cas où la base est mise en cause", () => {
  const msg = messageCollecte("MZONEX", { classe: "attente_sqlite", phase: "ecriture" });
  assert.match(msg!, /la base de données a fait attendre l'écriture/);
  assert.equal(estBaseVerrouillee({ classe: "attente_sqlite", phase: "ecriture" }), true);
});

test("verrou occupé → attente d'une autre tâche, aucune écriture de notre part", () => {
  const msg = messageCollecte("CAMTRACKPRO", { classe: "verrou_occupe", phase: "verrou" });
  assert.match(msg!, /une autre tâche détient le verrou/);
  assert.equal(/base verrouillée/i.test(msg!), false);
});

test("configuration absente → message de configuration, pas de panne de portail", () => {
  const msg = messageCollecte("CAMTRACKPRO", { classe: "configuration_absente" });
  assert.match(msg!, /configuration de la source est absente/);
});

test("portail indisponible → message de panne de source", () => {
  const msg = messageCollecte("MZONEX", { classe: "portail_indisponible" });
  assert.match(msg!, /en panne — collecte interrompue/);
});

test("collecte en cours → information, pas d'alerte", () => {
  const msg = messageCollecte("MZONEX", { statut: "COLLECTE_EN_COURS", phase: "ecriture" });
  assert.match(msg!, /en cours/);
  assert.equal(estBaseVerrouillee({ statut: "COLLECTE_EN_COURS", classe: "verrou_occupe" }), false);
});

test("budget consommé CÔTÉ PORTAIL (cause) → l'écran dit « portail lent », l'issue reste un budget",
  () => {
    // Le backend publie classe = budget_depasse (ISSUE) et cause = portail_lent.
    const etat = {
      classe: "budget_depasse", cause: "portail_lent",
      phase: "attente_http", issue: "BUDGET_DEPASSE",
    };
    const msg = messageCollecte("MZONEX", etat);
    // L'ISSUE prime : l'exploitant doit savoir que la passe s'est ARRÊTÉE.
    assert.match(msg!, /budget dépassé/);
    assert.match(msg!, /attente du portail/);
    assert.equal(/base verrouillée|bloquée localement/i.test(msg!), false);
  });

test("la cause du backend prime sur la classe pour qualifier la phase", () => {
  const msg = messageCollecte("MZONEX", { classe: "budget_depasse", cause: "portail_lent", phase: "pagination" });
  assert.match(msg!, /lecture paginée/);
});

// ─────────────────────────── correspondance statut / classe / phase / message
test("les huit situations donnent des messages DIFFÉRENTS (aucun regroupement)", () => {
  const cas: Array<[string, Parameters<typeof messageCollecte>[1]]> = [
    ["budget", { statut: "COLLECTE_BUDGET_DEPASSE", classe: "budget_depasse", phase: "ecriture" }],
    ["lent", { classe: "portail_lent", phase: "attente_http" }],
    ["sqlite", { classe: "attente_sqlite", phase: "ecriture" }],
    ["verrou", { classe: "verrou_occupe", phase: "verrou" }],
    ["config", { classe: "configuration_absente" }],
    ["panne", { classe: "portail_indisponible" }],
    ["en_cours", { statut: "COLLECTE_EN_COURS", phase: "pagination" }],
    ["echouee", { statut: "COLLECTE_ECHOUEE", classe: "collecte_echouee", phase: "parsing" }],
  ];
  const messages = cas.map(([, etat]) => messageCollecte("MZONEX", etat));
  assert.equal(new Set(messages).size, cas.length);
  assert.equal(messages.every((m) => !!m), true);
});

test("aucune anomalie → aucun bandeau (null)", () => {
  assert.equal(messageCollecte("MZONEX", {}), null);
  assert.equal(messageCollecte("MZONEX", null), null);
});

test("le backend reste prioritaire quand il fournit le message", () => {
  // L'interface affiche le message du backend : une seule vérité.
  const msg = messageCollecte("MZONEX", { message: "Message du backend" });
  assert.equal(msg, null); // le composant utilise `etat.message` avant d'appeler ce module
});

test("les libellés de phase couvrent toutes les phases publiées", () => {
  for (const phase of Object.keys(PHASES_LISIBLES)) {
    assert.equal(typeof PHASES_LISIBLES[phase], "string");
    assert.ok(libellePhase(phase).length > 0);
  }
  assert.equal(libellePhase(undefined), "non déterminée");
  assert.equal(libellePhase("phase_inexistante"), "non déterminée");
});

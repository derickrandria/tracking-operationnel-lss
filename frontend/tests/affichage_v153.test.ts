/**
 * P4 — TESTS FRONTEND (v1.53, 18/09/2026)
 *
 * Runner : `node --test` (intégré à Node ≥ 22, AUCUNE dépendance ajoutée).
 * Lancement : `npm run test` (depuis frontend/).
 *
 * Ces tests couvrent les RÈGLES D'AFFICHAGE PARTAGÉES (`src/utils.ts`), celles
 * qui décident de ce que l'écran montre :
 *   · TTJ « ≥ 12:00 » INCLUSIF (11:59:59 non signalé, 12:00:00 signalé) ;
 *   · TCC d'une journée close → « — » (jamais « 0:00 » qui se lirait comme
 *     une mesure) ;
 *   · convention d'EXPORT « 0:00 » (la note explicative est dans les exports) ;
 *   · durées bornées à 24 h et formats de saisie.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  celluleTcc,
  CONVENTION_TCC_EXPORT,
  fmtDuree,
  parseDureeEnSecondes,
  seuilTtjAtteint,
  SEUIL_TTJ_SIGNAL_S,
} from "../src/utils.ts";

// ─────────────────────────────────────────────── TTJ : seuil de 12 h inclusif
test("TTJ 11:59:59 → PAS de signalement", () => {
  assert.equal(seuilTtjAtteint(43199), false);
  assert.equal(fmtDuree(43199), "11:59");
});

test("TTJ 12:00:00 pile → SIGNALÉ (≥ est inclusif)", () => {
  assert.equal(SEUIL_TTJ_SIGNAL_S, 43200);
  assert.equal(seuilTtjAtteint(43200), true);
  assert.equal(fmtDuree(43200), "12:00");
});

test("TTJ 12:00:01 → signalé aussi", () => {
  assert.equal(seuilTtjAtteint(43201), true);
});

test("TTJ inconnu (null / undefined) → JAMAIS signalé (et jamais lu comme 0)", () => {
  assert.equal(seuilTtjAtteint(null), false);
  assert.equal(seuilTtjAtteint(undefined), false);
  assert.equal(fmtDuree(null), "—");
});

test("TTJ 13:30 (valeur réelle conservée, jamais écrêtée à 12 h)", () => {
  assert.equal(fmtDuree(48600, true), "13:30");
});

// ───────────────────────────────────────────────── TCC : « — » sur journée close
test("TCC d'une journée close → « — » (jamais « 0:00 » à l'écran)", () => {
  assert.equal(celluleTcc(7200, true), "—");
});

test("TCC inconnu → « — » (un champ absent n'est pas un zéro)", () => {
  assert.equal(celluleTcc(null), "—");
  assert.equal(celluleTcc(undefined), "—");
});

test("TCC en cours (journée active) → durée affichée", () => {
  assert.equal(celluleTcc(5400, false), "1:30");
});

test("TCC à 0 avec un jour actif → « 0:00 » (mesure réelle, pas un masque)", () => {
  assert.equal(celluleTcc(0, false), "0:00");
});

test("convention d'export : « 0:00 » (accompagnée de la note dans les exports)", () => {
  assert.equal(CONVENTION_TCC_EXPORT, "0:00");
});

// ─────────────────────────────────────────────────────────── durées & bornes
test("les durées sont bornées à 24 h (86 399 s et 86 400 s)", () => {
  assert.equal(fmtDuree(86399, true), "23:59");
  assert.equal(fmtDuree(86400, true), "24:00");
  assert.equal(fmtDuree(120000, true), "24:00"); // donnée anormale → borne technique
});

test("une valeur inconnue ne devient jamais 0 (saisie « — »)", () => {
  assert.equal(parseDureeEnSecondes("—"), 0);
  assert.equal(parseDureeEnSecondes("12:00"), 43200);
  assert.equal(parseDureeEnSecondes("13:30"), 48600);
  assert.equal(parseDureeEnSecondes(null), 0);
});

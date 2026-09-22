# RÈGLES PERMANENTES DU PROJET (LSS — Tracking Opérationnel)

**Nature de ce fichier** : règles **de travail** (méthode, interdits, régime
d'exécution). Il **ne remplace pas** les sources normatives du produit et n'y ajoute
aucune règle métier :

| Source | Portée |
|---|---|
| `SPEC_RULES_v3.md` | **vérité unique des règles métier** (validité, TCC/TCJ/TTJ, pauses, alertes…) |
| `AGENT` | garde-fous d'environnement et de données |
| `REFERENCE_IA_REGLES.md` · `CHANGELOG_REGLES.md` | référence IA et historique des décisions |
| ce fichier | **méthode de travail** et régime d'exécution |

---

## 1. Priorité absolue

La **fiabilité du cœur** de la plateforme prime sur toute nouvelle fonctionnalité :
collecte, rattrapage, intégrité des données, calculs, API et affichage.
En cas de conflit entre « livrer une fonction » et « fiabiliser le cœur », le cœur
gagne.

## 2. Avant TOUTE modification (code, test, script ou document)

1. **décrire le problème** ;
2. **fournir une preuve** (chemin + ligne + extrait + commande + sortie réelle) ;
3. **expliquer la solution envisagée** ;
4. **indiquer les risques** (produit, données, exploitation) ;
5. **proposer les tests** qui établiront le résultat ;
6. **prévoir un rollback**.

**Périmètre : toute modification, sans exception** — code produit, tests, scripts,
fichiers de configuration et documents d'audit. Le contrôle `py_compile` ne
dispense de rien (il ne voit ni `NameError` ni `TypeError`) : un smoke test est
obligatoire après tout patch d'usage.

## 3. Interdits permanents

- Ne **jamais** présenter une hypothèse comme un fait.
- Ne **jamais** modifier un test uniquement pour le faire passer (toute
  modification d'attente doit être justifiée par la règle produit, avec preuve).
- Ne **supprimer aucune donnée** ; ne **réécrire aucune archive silencieusement**.
- Ne **jamais** toucher la production sans autorisation explicite.
- Ne **révéler aucun secret** (ni `.env`, ni jeton, ni mot de passe, ni identifiant).
- **Ne pas fusionner `main`**, **ne pas déployer**, **ne pas modifier `main`**.
- **Ne pas modifier `SPEC_RULES_v3.md`** (amendements par procédure dédiée).
- **Aucun développement Mission** tant que le cœur n'est pas validé **avec les
  données réelles des portails**.

## 4. Régime d'exécution « données simulées » (décision du 22/09/2026)

- **Autorisation préalable systématique** : aucune exécution utilisant des données
  simulées (bases de test créées par `seed_si_vide()`, simulateur, jeux de
  démonstration) ne démarre sans autorisation explicite.
- Toute exécution autorisée est **annoncée avant** : nature des données, base
  utilisée, ce qu'elle prouve et **ce qu'elle ne prouve pas**.
- Une preuve obtenue sur données simulées **ne vaut jamais** preuve métier.

## 5. Après chaque correction

- **exécuter les tests** concernés ;
- **comparer avec les données réelles** (quand l'accès existe ; sinon, le déclarer
  comme verrou) ;
- **documenter le résultat** (chemin du rapport et du journal) ;
- **créer un commit isolé**, message explicite ;
- **fournir un statut GO ou NO-GO justifié**.

## 6. En cas de doute

**Arrêter l'action** et demander une clarification — jamais d'initiative silencieuse
sur le cœur, les données ou la production.

---

## 7. Régime de preuve et de test (rappel, déjà en vigueur)

- Toute affirmation = **chemin + ligne + extrait + commande**.
- **Ne jamais concaténer** `stdout` et `stderr` dans un bilan de tests.
- Campagne de tests : séparer **réussi / échoué / crash / non exécuté / instable** ;
  comparer avant/après ; ne jamais déclarer une campagne « verte » si une suite ne
  prouve rien.
- Tests **exécutables de tout répertoire** ; suite de régression obligatoire pour
  chaque correctif.

## 8. État au 22/09/2026

| Élément | État |
|---|---|
| Branche de travail | `arena/01a0aa2b-tracking-operationnel-lss` — `7ccdd91` |
| `main` | `9a8f3ee` — **intacte**, jamais poussée dessus |
| Pull request #1 | ouverte, **brouillon**, non fusionnée |
| Campagne complète | **57 réussies / 0 échouée / 0 crash / 2 non exécutées / 0 instable** |
| Verrou restant | **les deux verdicts de préproduction** (`test_mzonex_ping`, `test_e2e_reel_v113`) — procédure : `docs/audits/RECETTE_PREPROD_WINDOWS_v154.md` |
| Verdict | **❌ NO-GO** tant que les deux verdicts de préproduction ne sont pas obtenus sur données réelles |
| Décision parquée | matricule (`AUTO-xxxxxxxx` MZoneX vs `None` CamtrackPro) — arbitrage à rendre |

Preuves détaillées : `docs/audits/RAPPORT_GO_NO_GO_v154.md` (§10) et
`docs/audits/campagne_v154_resultats.json`.

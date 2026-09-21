# Proposition d'amendement — SPEC_RULES_v3

**Statut : PROPOSITION — NON APPLIQUÉE.** `SPEC_RULES_v3.md` n'a **pas** été modifié
(consigne du 18/09/2026 : « Ne modifie pas silencieusement SPEC_RULES_v3 ; si une
modification de la règle est nécessaire, prépare une proposition d'amendement séparée »).

Origine : **clarification métier définitive du 18/09/2026** (TTJ, TCC, TCJ, fusion
d'affichage) + arbitrages LSS exprimés le même jour :
`nb_trajets` = séquences affichées · règle TCC **par interruption** · « — » sur toutes
les journées closes (écran) · note de convention dans **les deux** exports.

Implémentation correspondante : **v1.53** (`backend/`, commit à venir) — la présente
proposition décrit ce que le code applique **déjà** et que la loi doit refléter.

---

## 1. [R-02] Affichage / stockage — découplage strict

**Texte actuel** (l.53-63)

> - L'affichage montre les trajets valides + pauses ≥ 30 min.
> - Les données de base ne sont jamais effacées.

**Proposition**

> - L'affichage projette les trajets valides en **SÉQUENCES** et les pauses ≥ 30 min.
> - Les données de base ne sont jamais effacées ni écrasées par une décision de rendu.
>
> *Conditions*
> - Trajet valide = `distance ≥ 0,3 km` ; les autres sont conservés pour audit.
> - `TCJ`, `TTJ` et `TCC` sont calculés **AVANT toute sérialisation** et ne dépendent
>   jamais du rendu.
> - Trois compteurs distincts : `nb_trajets_valides_reels` (base),
>   `nb_sequences_affichees` (rendu), `nb_trajets_fusionnes` (absorbés par le
>   regroupement). `nb_trajets` a **une seule** signification — le nombre de
>   séquences affichées — identique en base, à l'écran et à l'export.
>
> *Exceptions*
> - Les pauses < 30 min sont masquées à l'écran (mesurées et stockées).
> - Journée close : `tcc_s` n'est **jamais** remis à 0 en base ; le rendu est « — » à
>   l'interface et « 0:00 » à l'export, convention accompagnée d'une note explicite.
> - Les trajets invalides restent en base mais ne sont pas rendus comme trajets actifs.

**Justification** : la version actuelle laisse `tcc_s = 0` possible au stockage (pratique
N1 du 31/08/2026) et ne définit pas la notion de séquence ; l'audit du 18/09 a montré
qu'un sérialiseur remettait la valeur calculée à zéro (`daily.py`, `serializers.py`,
`operations.py` — corrigé en v1.52) et que `nb_trajets` portait deux valeurs
(stockage = trajets réels, lecture = lignes affichées).

---

## 2. [R-03] TTJ — temps écoulé depuis le départ

**Texte actuel** (l.65-73)

> - `TTJ = TCJ + Σ tous arrêts = maintenant − départ`
> - La borne `TTJ_MAX` = 12:00 **reste appliquée**.

**Proposition**

> - `TTJ = TCJ + Σ tous arrêts = maintenant − départ`, départ = **premier trajet valide**.
> - `TTJ_MAX` = 12:00 est un **SEUIL DE SIGNALEMENT, JAMAIS UN PLAFOND** : le
>   dépassement lève `flag_ttj` (coloration rouge à l'écran) et déclenche l'alerte ;
>   la durée **réelle** reste mesurée et stockée. Le camion peut continuer à rouler
>   au-delà de 12 h.
> - Le seuil de 12 h ne s'applique **pas** au TCC (logique distincte : chrono de session).

**Justification** : « borne » (terme actuel) se lit comme un plafond ; le seul écrêtage
légitime est **technique** (24 h/jour, durée aberrante). Code : `engine.py:412`
(`min(86400, …)`), `serializers.py:430` / `daily.py:542` (`flag_ttj`).

---

## 3. [R-07] Arrêts — source portail + mini-manœuvres

**Texte actuel** (l.120-121)

> - Une mini-manœuvre en cours de route est déduite du TCJ.
> - Si **sa** durée ≥ 30 min, elle est traitée comme pause TCC.

**Proposition** (ajouter, sans retirer)

> - Une **interruption** entre deux trajets valides qui **contient** des
>   mini-manœuvres est analysée selon **sa durée totale**, celles-ci incluses.
> - Si cette interruption atteint ≥ 30 min, elle **réinitialise le TCC** et **s'affiche
>   comme pause** dans la colonne pause.
> - Le cumul des mini-manœuvres d'une session **ne coupe pas** le TCC : seule une
>   interruption atteignant 30 min coupe la session.
> - Les mini-manœuvres ne deviennent **jamais** des trajets affichés et ne démarrent
>   jamais une session.

**Justification** : la règle v1.46 « mini-manœuvres **cumulées** de la session ≥ 30 min
→ TCC = 0 » (`engine.py`, arbitrage du 04/09/2026) figeait le chrono à zéro pour tout le
reste de la journée ; deux interruptions de 16 min séparées par de la conduite n'ont
jamais constitué une pause de 30 min. La coupure par **interruption** est celle que
décrit la clarification du 18/09 — le code l'applique désormais seul (v1.53).

---

## 4. [R-10] Affichage du suivi

**Texte actuel** (l.154-163)

> - Afficher tous les trajets valides.
> - Afficher seulement les pauses ≥ 30 min.

**Proposition**

> - Afficher toutes les **SÉQUENCES** de trajets valides : deux trajets valides
>   consécutifs dont l'interruption est **strictement inférieure à 30 minutes**
>   forment une même séquence.
> - Afficher seulement les pauses ≥ 30 min, **entre** deux séquences.
>
> *Conditions*
> - Une séquence commence au début de son premier trajet valide et se termine à la fin
>   de son dernier trajet valide.
> - Une interruption ≥ 30 min **clôt** la séquence : fin affichée = fin du dernier
>   trajet valide, **pause affichée = interruption complète**, séquence suivante =
>   départ valide suivant.
> - La `distance` et les données d'une séquence sont l'**agrégation** de ses trajets.
> - **Aucun trajet valide n'est perdu en BASE** : le regroupement est une projection
>   d'affichage, jamais une fusion stockée.
>
> *Exceptions*
> - Pauses courtes masquées à l'écran, jamais retirées du calcul.
> - Manœuvres < 0,3 km jamais affichées comme trajets (conservées pour audit).
> - Aucun nettoyage destructif des temps de conduite dans les colonnes de calcul.

**Justification** : « Afficher tous les trajets valides » (texte actuel) contredit le
regroupement en séquences demandé le 18/09. Exemple canonique (vérifié en v1.53) :
05:30→08:00 (115 km) + 08:05→08:15 (0,520 km) + 08:50→… (75 km) → **T1 = 05:30→08:15**,
**pause affichée 00:35**, **T2 dès 08:50**, 3 trajets valides réels, 2 séquences.

---

## 5. [R-08] TCC — session continue (précision, sans contradiction)

**Proposition** (ajouter une puce aux Exceptions)

> - Après réinitialisation, le TCC **reste à 0 jusqu'au prochain départ valide** : la
>   pause n'est jamais recomptée dans la session suivante.

**Justification** : le texte actuel dit « réinitialise le TCC » sans préciser l'état
pendant la pause ni le point de reprise — précision de la clarification du 18/09.

---

## 6. [R-11] Historique / archive (ajout)

**Proposition** (ajouter aux Conditions)

> - Une journée close conserve son `tcc_s` **réel** : jamais écrasé par 0 en base.
> - Le rendu d'une journée close est « **—** » à l'interface (non applicable à l'écran)
>   et « 0:00 » à l'export, avec une **note de convention** indiquant qu'il s'agit
>   d'une valeur d'affichage et non d'une destruction du calcul.

**Justification** : clarification du 18/09 §5 ; le drapeau d'affichage `tcc_masque`
porte cette intention sans toucher à la donnée (v1.52/v1.53).

---

## Portée et rollback

- **Aucune modification de `SPEC_RULES_v3.md`** n'est incluse ici : ce document est une
  proposition soumise à validation.
- Application : à valider par le responsable métier, puis report manuel dans
  `SPEC_RULES_v3.md` (section « Règles actives ») **et** dans `CHANGELOG_REGLES.md`.
- En cas de refus d'un point, seul le point concerné est écarté : les autres sont
  indépendants (R-02/R-10 = affichage ; R-03 = TTJ ; R-07 = manœuvres ; R-11 = archive).
- Le code v1.53 applique déjà les points 1 à 6 ; un refus impliquerait un correctif de
  code supplémentaire (rollback `git revert` du commit v1.53).

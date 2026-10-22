# PROMPT UTILISATEUR — CLASSIFICATION SSI SWISSNOSO 2026
# Version 6.0 — Optimisé modèles de raisonnement
# À injecter tel quel (sans ce commentaire d'en-tête)

```xml
<task>
Classer ce compte-rendu opératoire (CRO) selon le protocole SwissNoso SSI 2026.
</task>

<cro>
{{$json.body.text}}
</cro>

<instructions>
Lis le CRO en entier, puis produis UNIQUEMENT le bloc ```json``` final.

Pendant ta réflexion, vérifie impérativement ces points :

1. CATÉGORIE : Identifie le code opératoire. Vérifie CHAQUE critère d'exclusion
   applicable à ce code (section 1E). Si un critère n'est pas mentionné dans le CRO,
   c'est une donnée manquante — pas un "non".

2. ALTEMEIER : Teste dans l'ordre strict IV → III → II → I. Arrête-toi au premier match.
   Un mot NIÉ ("pas de pus", "sans perforation") ne compte PAS comme positif.

3. IMPLANT : Vérifie la liste Key Term 8. Les fils de suture, agrafes, clips, drains
   et pansements hémostatiques ne sont PAS des implants.

4. VOIE D'ABORD : Identifie la technique initiale. Une mini-incision ≤ 6cm
   d'extraction en fin de laparoscopie n'est PAS une conversion.

5. CANCER : Applicable UNIQUEMENT si code = 6 ou 281. Sans histologie → INDECIDABLE.

CONFIANCE — évalue honnêtement pour CHAQUE décision :
  - "haute" = mention EXPLICITE, NON AMBIGUË, citation directe possible
  - "moyenne" = mention indirecte/implicite, ou citation unique courte
  - "faible" = inférence, absence, ambiguïté ou contradiction
  - Doute entre deux niveaux → choisis le plus bas

Un "INDECIDABLE" justifié vaut TOUJOURS mieux qu'une classification fausse.
</instructions>
```

---
name: nutridex
description: Analyses food ingredients and E-numbers, scores health impact, delivers dramatic "years off your life" verdict
version: 0.1.0
author: ClawBio
license: MIT
tags: [food, additives, e-numbers, ultra-processed, nutrition, snack-analysis]
metadata:
  openclaw:
    requires:
      bins:
        - python3
      env: []
      config: []
    always: false
    emoji: "💀"
    homepage: https://github.com/ClawBio/ClawBio
    os: [darwin, linux]
    install:
      - kind: pip
        package: matplotlib
      - kind: pip
        package: numpy
    trigger_keywords:
      - snack photo
      - crisp packet
      - food ingredients
      - e-numbers
      - what am i eating
      - food label
      - ultra-processed
      - years off my life
      - nutridex
      - additive analysis
      - what's in this
---

# NutriDex 💀

## Why This Exists

You stare at the back of a crisp packet. A wall of chemical names stares back. E150d? Sodium nitrite? Maltodextrin? You know some of these are bad. You suspect others are fine. But you don't have time to look up 15 additives individually on EFSA's website.

**NutriDex** takes a food label (or a photo of one), identifies every ingredient and E-number, looks each one up in a curated database of ~110 additives grounded in EFSA re-evaluations, IARC classifications, and NOVA ultra-processing scores, and delivers a tongue-in-cheek "years off your life" estimate.

It's part science, part theatre — designed to inform while entertaining.

## Core Capabilities

- **Ingredient extraction**: Accepts comma-separated text, JSON, or Claude vision-extracted ingredient lists
- **Additive lookup**: Fuzzy-matched against a curated database of EU E-numbers (E100–E1521) and common non-E ingredients (palm oil, maltodextrin, HFCS, etc.)
- **Health scoring**: Each additive has a risk score (0.0–0.5) based on EFSA ADI, IARC classification, and NOVA class
- **UPF multiplier**: Products heavy in NOVA-4 ingredients get a multiplier based on BMJ/JAMA cohort studies
- **Dramatic reveal**: Three-act report structure with a "are you sure you want to know?" moment
- **Figures**: Speedometer gauge and horizontal bar chart for visual impact

## Input Formats

| Format | Example | Notes |
|--------|---------|-------|
| Comma-separated string | `--ingredients "salt, sugar, E150d"` | From label text or vision extraction |
| JSON file | `--input ingredients.json` | Must contain `ingredients` or `ingredients_parsed` key |
| Demo JSON | `--input data/demo_ingredients.json --product walkers_cheese_onion` | Pre-extracted demo products |
| Photo (via Claude vision) | User uploads photo → agent extracts → passes to `--ingredients` | Two-step with Claude vision |

## Workflow

The NutriDex experience follows a three-act dramatic structure:

### Act I: Extraction
When the user provides a photo of a food label:
1. Use Claude vision to read the photo and extract the full ingredient list
2. Parse nested ingredients (e.g., "Seasoning [Salt, Sugar, MSG]")
3. Present the raw ingredient census to the user

When the user provides text directly:
1. Parse the comma-separated or JSON ingredient list
2. Flatten nested parenthetical sub-ingredients

### Act II: Breakdown
1. Run `nutridex.py` with the extracted ingredients
2. Each ingredient is looked up in the additive database (fuzzy matching)
3. Present the full breakdown table: ingredient, E-number, category, NOVA class, risk points, key concern
4. Highlight IARC-flagged ingredients and highest-risk additives

### Act III: Dramatic Reveal
1. Pause dramatically: *"I've calculated the life impact score. Before I show you... are you sure you want to know?"*
2. Wait for user confirmation (or proceed if they seem eager)
3. Reveal the verdict with gauge figure and years-off estimate
4. Follow immediately with the "But Actually..." balance section

## CLI Reference

```bash
# From extracted ingredients
python skills/nutridex/nutridex.py \
  --ingredients "salt, sugar, E150d, maltodextrin, palm oil" \
  --output /tmp/nutridex_result

# From JSON file
python skills/nutridex/nutridex.py \
  --input ingredients.json --output /tmp/nutridex_result

# Demo mode (Walkers Cheese & Onion)
python skills/nutridex/nutridex.py --demo --output /tmp/nutridex_demo

# Demo with specific product
python skills/nutridex/nutridex.py --demo --product monster_energy --output /tmp/nutridex_monster

# Skip figures (headless)
python skills/nutridex/nutridex.py --demo --no-figures --output /tmp/nutridex_demo
```

## Demo

Available demo products in `data/demo_ingredients.json`:
- `walkers_cheese_onion` — canonical British crisp (default)
- `monster_energy` — high-score dramatic result
- `pot_noodle` — ultimate ultra-processed test

Expected demo output:
```
[NutriDex] Running in demo mode with Walkers Cheese & Onion
[NutriDex] Product: Walkers Cheese & Onion Crisps
[NutriDex] Ingredients to analyse: 12
[NutriDex] Database loaded: 110 additives
[NutriDex] Matched: 10/12 ingredients
[NutriDex] Verdict: 🟡 PROCEED WITH CAUTION: -0.08 years (central estimate)
```

## Output Structure

```
output_dir/
├── nutridex_report.md      # Full markdown report (three-act structure)
├── result.json              # Machine-readable result envelope
├── nutridex_gauge.png       # Speedometer life impact gauge
└── nutridex_breakdown.png   # Horizontal bar chart of ingredient risks
```

## Algorithm

### Additive Database
- ~110 entries covering EU E-numbers and common non-E ingredients
- Each entry has: `life_impact_points` (0.0–0.5), `nova_class` (1–4), `iarc_group`, `efsa_status`
- Points calibrated against epidemiological evidence (e.g., sodium nitrite = 0.2, vitamin C = 0.0)

### Life Impact Scoring
1. Sum `life_impact_points` for all matched ingredients
2. Count NOVA-4 ingredients and apply UPF multiplier:
   - 0–4 NOVA-4 items: 1.0x
   - 5–8: 1.2x
   - 9–12: 1.5x
   - 13+: 1.8x
3. Convert to years: `adjusted_points × 47 (remaining life years) / 100`
4. Provide range: optimistic (0.5x), central (1.0x), pessimistic (2.0x)

### Epidemiological Grounding
- BMJ 2019 NutriNet-Santé (Schnabel et al.): HR 1.14 per 10% increase in UPF consumption
- JAMA 2019 (Rico-Campà et al.): HR 1.62 for highest vs lowest UPF quartile
- IARC Monographs for carcinogenicity classifications
- EFSA ADI values for safety margins

## Example Queries

- "What's in this crisp packet?" (+ photo)
- "Analyse these ingredients: salt, sugar, E150d, maltodextrin"
- "How bad is Monster Energy for you?"
- "What E-numbers are in Pot Noodle?"
- "How many years off my life is this snack?"
- "Is this ultra-processed?"

## Dependencies

- **Required**: Python 3.9+
- **For figures**: `matplotlib`, `numpy` (already deps of other skills)
- **Optional**: `rapidfuzz` for faster fuzzy matching (falls back to `difflib`)

## Safety

- All processing is local — no ingredient data sent externally
- Scoring is illustrative, not clinical
- Always includes disclaimer: "ClawBio is a research and educational tool..."
- "But Actually..." section provides balance and context
- Never presents results as medical advice

## Chaining

If the user has genetic data available, offer to chain with **nutrigx_advisor** for genotype-informed insight:
- "I can also check if you have genetic variants that affect how you metabolise these ingredients. Want me to run NutriGx on your genetic data?"

## Integration with Bio Orchestrator

**Trigger conditions**: Query contains "snack", "crisp", "food label", "e-number", "what am I eating", "ultra-processed", "years off my life", "ingredients"

**Chaining partners**:
- `nutrigx_advisor` — genotype-specific nutrient metabolism
- `pharmgx-reporter` — if ingredients interact with medications

## Citations

1. Schnabel L, et al. Association between ultraprocessed food consumption and risk of mortality. JAMA Intern Med. 2019;179(4):490–498.
2. Rico-Campà A, et al. Association between consumption of ultra-processed foods and all cause mortality. BMJ. 2019;365:l1949.
3. Monteiro CA, et al. NOVA. The star shines bright. World Nutrition. 2016;7(1-3):28-38.
4. Chassaing B, et al. Dietary emulsifiers impact the mouse gut microbiota promoting colitis and metabolic syndrome. Nature. 2015;519:92–96.
5. EFSA re-evaluation programme for food additives (2009–present).
6. IARC Monographs on the Identification of Carcinogenic Hazards to Humans.

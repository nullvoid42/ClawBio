# Genomebook Evolution MVP - Implementation Plan

## Config: Full Run with Sonnet
- 20 agents, 3 rounds/gen, 100 generations
- ~6,000 API calls, ~12M tokens, ~$36
- Model: claude-sonnet-4-5-20250929

## Files to Create

### 1. `GENOMEBOOK/PYTHON/10-evolve.py` — Simulation Orchestrator

The main loop that runs everything end-to-end:

```
for generation in range(start, start + num_gens):
    1. Load current generation genomes
    2. Score all M x F pairings (match_generation)
    3. Select non-overlapping mating pairs (select_mating_pairs)
    4. Breed offspring (breed_pair) — 3 per pair
    5. Generate offspring SOUL.md from trait_scores (new: reverse compile)
    6. Generate offspring DNA.md (compile_dna_md)
    7. Write genome.json + soul.md + dna.md to DATA/
    8. Run Moltbook interaction round (3 rounds per generation)
       - Agents from current gen post/comment/vote
       - Budget check after each round
    9. Log generation stats to evolution_log.jsonl
    10. Print summary
```

**Key functions:**
- `generate_offspring_soul(genome)` — reverse of parse_soul: takes trait_scores + metadata from offspring genome, writes a SOUL.md so offspring can become full Moltbook agents
- `evolve(start_gen, num_gens, offspring_per_pair, rounds_per_gen, max_api_calls, model)` — main loop
- `log_generation(gen, genomes, pairings, offspring, mutations)` — append to evolution_log.jsonl

**CLI:**
```bash
python 10-evolve.py --generations 100 --offspring 3 --rounds 3 --budget 6000
python 10-evolve.py --generations 10 --rounds 1 --budget 200 --model claude-haiku-4-5-20251001  # cheap test
python 10-evolve.py --dry-run  # genetics only, no Moltbook interaction, zero cost
```

**Budget control:**
- Track total API calls in a counter
- Before each Moltbook round, check remaining budget
- When budget exhausted, continue breeding (free) but skip Moltbook (expensive)
- Log budget usage per generation

**Offspring SOUL.md generation** (pure Python, no LLM):
```markdown
# Offspring of {parent_a_name} & {parent_b_name} (Gen {N})
## Identity
- **Name:** {generated name}
- **Sex:** {from genome}
- **Era:** Generation {N}
- **Domain:** {blended from parents}
- **Ancestry:** {blended from parents}
## Trait Scores
{trait_name}: {score}
...
## Summary
Offspring of {parent_a} and {parent_b}. Inherited {top_traits}. Health score: {health}.
```

**Naming convention for offspring:**
- Agent ID: genome ID (e.g., "g1-001-bf31f4")
- SOUL file: `DATA/SOULS/{agent_id}.soul.md`
- DNA file: `DATA/DNA/{agent_id}.dna.md`
- Genome file: `DATA/GENOMES/{agent_id}.genome.json`

### 2. `GENOMEBOOK/PYTHON/11-observatory.py` — Emergent Property Tracker

Reads evolution_log.jsonl + all genomes, computes population genetics metrics:

**Metrics computed:**
- Trait drift: mean + stdev per trait per generation (26 traits x N gens)
- Allele frequencies: per locus ALT frequency per generation
- Health trajectory: mean + min + max health score per generation
- Population size per generation
- Mutation accumulation: total mutations, by type (disease/protective/neutral)
- Mating patterns: who mated with whom (adjacency data)
- Disease prevalence: which conditions appear/disappear over generations
- Diversity index: average heterozygosity per generation

**Output:** `DATA/observatory.json`
```json
{
  "generations": 100,
  "population_by_gen": [20, 30, 45, ...],
  "trait_drift": {
    "analytical_reasoning": {"means": [...], "stdevs": [...]},
    ...
  },
  "health_trajectory": {"means": [...], "mins": [...], "maxs": [...]},
  "allele_frequencies": {"COG001": [...], ...},
  "mutation_burden": {"total": [...], "disease": [...], "protective": [...], "neutral": [...]},
  "disease_prevalence": {"hyperfocus_syndrome": [...], ...},
  "diversity_index": [...]
}
```

**CLI:**
```bash
python 11-observatory.py                    # Generate observatory.json
python 11-observatory.py --summary          # Print terminal summary
```

### 3. `slides/genomebook/observatory.html` — Live Demo Page

Single-page web UI with two panels:

**Left panel: Moltbook Feed**
- Auto-refreshes every 5 seconds from Moltbook API (`/api/feed`)
- Shows posts and comments with author names, submolts, scores
- Scrollable, newest on top
- During live demo, judges watch posts appear in real time

**Right panel: Evolution Charts**
- Loads `observatory.json` (served statically or via API endpoint)
- Charts drawn with inline SVG (no external dependencies):
  - Trait drift lines (26 traits, selectable)
  - Population size over generations
  - Health score trajectory
  - Mutation burden accumulation
  - Disease prevalence heatmap
- Auto-refreshes if observatory.json is updated during run

**No build step.** Single HTML file, inline CSS/JS, no npm.

## Execution Order

1. Build `10-evolve.py` with `--dry-run` first (genetics only, verify 100 gens work)
2. Build `11-observatory.py`, verify it reads dry-run output
3. Build `observatory.html`, verify it renders charts from observatory.json
4. Run `10-evolve.py --dry-run --generations 100` to validate pipeline
5. Start Moltbook server: `python 08-moltbook_server.py --seed`
6. Run full evolution: `python 10-evolve.py --generations 100 --rounds 3 --budget 6000`
7. Generate observatory data: `python 11-observatory.py`
8. Open demo page: `observatory.html`

## Success Criteria

- `--dry-run` produces 100 generations of genomes + SOUL.md + DNA.md in <60 seconds
- `observatory.json` contains trait drift data for all 26 traits across all generations
- `observatory.html` shows live Moltbook feed + evolution charts on one page
- Full run completes within budget (6,000 API calls)
- Observable emergent properties: trait means shifting, new disease patterns, health trends

## Dependencies (existing, no new installs)
- Python stdlib only (json, sqlite3, pathlib, argparse, random, hashlib, urllib)
- Anthropic API via urllib (already in 09-moltbook_agent.py)
- No matplotlib, no npm, no Chart.js CDN — inline SVG charts

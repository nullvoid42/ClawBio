"""
11-observatory.py -- Emergent Property Tracker for Genomebook

Reads evolution_log.jsonl and all genomes to compute population genetics
metrics: trait drift, health trajectories, mutation burden, mating patterns.

Usage:
    python 11-observatory.py                # Generate observatory.json
    python 11-observatory.py --summary      # Print terminal summary
"""

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "DATA"
GENOMES_DIR = DATA / "GENOMES"
EVOLUTION_LOG = DATA / "evolution_log.jsonl"
OBSERVATORY_JSON = DATA / "observatory.json"


def load_evolution_log():
    """Load all generation stats from the JSONL log."""
    entries = []
    if EVOLUTION_LOG.exists():
        for line in EVOLUTION_LOG.read_text().strip().split("\n"):
            if line.strip():
                entries.append(json.loads(line))
    return entries


def load_all_genomes():
    """Load every genome file, grouped by generation."""
    by_gen = {}
    for gf in sorted(GENOMES_DIR.glob("*.genome.json")):
        g = json.load(open(gf))
        gen = g.get("generation", 0)
        if gen not in by_gen:
            by_gen[gen] = []
        by_gen[gen].append(g)
    return by_gen


def compute_observatory(log_entries, genomes_by_gen):
    """Compute all observatory metrics."""

    max_gen = max(genomes_by_gen.keys()) if genomes_by_gen else 0
    generations = sorted(genomes_by_gen.keys())

    # Build lookup from log entries
    log_by_gen = {e["generation"]: e for e in log_entries}

    # Trait drift
    all_traits = set()
    for gen_genomes in genomes_by_gen.values():
        for g in gen_genomes:
            all_traits.update(g.get("trait_scores", {}).keys())
    all_traits = sorted(all_traits)

    trait_drift = {}
    for trait in all_traits:
        means = []
        stdevs = []
        for gen in generations:
            values = [g["trait_scores"].get(trait, 0.5) for g in genomes_by_gen[gen]
                      if "trait_scores" in g]
            if values:
                mean = sum(values) / len(values)
                var = sum((v - mean) ** 2 for v in values) / len(values)
                means.append(round(mean, 4))
                stdevs.append(round(var ** 0.5, 4))
            else:
                means.append(0.5)
                stdevs.append(0.0)
        trait_drift[trait] = {"means": means, "stdevs": stdevs}

    # Population size
    pop_sizes = [len(genomes_by_gen.get(gen, [])) for gen in generations]

    # Health trajectory
    health_means = []
    health_mins = []
    health_maxs = []
    for gen in generations:
        scores = [g.get("health_score", 1.0) for g in genomes_by_gen[gen]]
        if scores:
            health_means.append(round(sum(scores) / len(scores), 4))
            health_mins.append(round(min(scores), 4))
            health_maxs.append(round(max(scores), 4))
        else:
            health_means.append(1.0)
            health_mins.append(1.0)
            health_maxs.append(1.0)

    # Allele frequencies per locus
    all_loci = set()
    for gen_genomes in genomes_by_gen.values():
        for g in gen_genomes:
            all_loci.update(g.get("loci", {}).keys())
    all_loci = sorted(all_loci)

    allele_freqs = {}
    for locus in all_loci:
        freqs = []
        for gen in generations:
            alt_count = 0
            total_alleles = 0
            for g in genomes_by_gen[gen]:
                if locus in g.get("loci", {}):
                    alleles = g["loci"][locus]["alleles"]
                    alt = g["loci"][locus]["alt"]
                    alt_count += alleles.count(alt)
                    total_alleles += 2
            freq = round(alt_count / total_alleles, 4) if total_alleles > 0 else 0.0
            freqs.append(freq)
        allele_freqs[locus] = freqs

    # Mutation burden (computed from genomes directly)
    mutation_total = []
    mutation_disease = []
    mutation_protective = []
    mutation_neutral = []
    for gen in generations:
        mt_total = 0
        mt_dis = 0
        mt_prot = 0
        mt_neut = 0
        for g in genomes_by_gen[gen]:
            for m in g.get("mutations", []):
                mt_total += 1
                mt = m.get("type", "neutral")
                if mt == "disease_risk":
                    mt_dis += 1
                elif mt == "protective":
                    mt_prot += 1
                else:
                    mt_neut += 1
        mutation_total.append(mt_total)
        mutation_disease.append(mt_dis)
        mutation_protective.append(mt_prot)
        mutation_neutral.append(mt_neut)

    # Condition burden (computed from genomes directly)
    condition_total = []
    for gen in generations:
        ct = sum(len(g.get("clinical_history", [])) for g in genomes_by_gen[gen])
        condition_total.append(ct)

    # Disease prevalence (computed from genomes directly)
    all_diseases = set()
    for gen_genomes in genomes_by_gen.values():
        for g in gen_genomes:
            for cond in g.get("clinical_history", []):
                all_diseases.add(cond["name"])
    all_diseases = sorted(all_diseases)

    disease_prevalence = {}
    for disease in all_diseases:
        counts = []
        for gen in generations:
            count = 0
            for g in genomes_by_gen[gen]:
                for cond in g.get("clinical_history", []):
                    if cond["name"] == disease:
                        count += 1
            counts.append(count)
        disease_prevalence[disease] = counts

    # Heterozygosity
    diversity = []
    for gen in generations:
        het_vals = []
        for g in genomes_by_gen[gen]:
            loci = g.get("loci", {})
            if loci:
                het = sum(1 for l in loci.values() if len(set(l["alleles"])) > 1) / len(loci)
                het_vals.append(het)
        avg = round(sum(het_vals) / len(het_vals), 4) if het_vals else 0.0
        diversity.append(avg)

    # Sex ratio
    sex_ratios = []
    for gen in generations:
        males = sum(1 for g in genomes_by_gen[gen] if g["sex"] == "Male")
        total = len(genomes_by_gen[gen])
        sex_ratios.append(round(males / total, 4) if total > 0 else 0.5)

    # Mating log (from genomes with parents)
    mating_log = []
    for gen in generations:
        for g in genomes_by_gen[gen]:
            parents = g.get("parents", [None, None])
            if parents[0] and parents[1]:
                mating_log.append({
                    "generation": gen,
                    "offspring_id": g["id"],
                    "father": parents[0],
                    "mother": parents[1],
                    "health": g.get("health_score", 1.0),
                })

    observatory = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "total_generations": len(generations),
        "generations": generations,
        "population_by_gen": pop_sizes,
        "sex_ratios": sex_ratios,
        "trait_drift": trait_drift,
        "trait_names": all_traits,
        "health_trajectory": {
            "means": health_means,
            "mins": health_mins,
            "maxs": health_maxs,
        },
        "allele_frequencies": allele_freqs,
        "mutation_burden": {
            "total": mutation_total,
            "disease_risk": mutation_disease,
            "protective": mutation_protective,
            "neutral": mutation_neutral,
        },
        "condition_burden": condition_total,
        "disease_prevalence": disease_prevalence,
        "diversity_index": diversity,
        "mating_log": mating_log,
    }

    return observatory


def print_summary(obs):
    """Print a terminal summary of the observatory data."""
    print("=" * 70)
    print("GENOMEBOOK OBSERVATORY")
    print("=" * 70)
    print(f"Generations:    {obs['total_generations']}")
    print(f"Final pop:      {obs['population_by_gen'][-1] if obs['population_by_gen'] else 0}")
    print()

    # Trait drift summary
    print("TRAIT DRIFT (gen 0 mean -> final gen mean):")
    for trait in obs["trait_names"]:
        td = obs["trait_drift"][trait]
        if td["means"]:
            start = td["means"][0]
            end = td["means"][-1]
            delta = end - start
            arrow = "+" if delta > 0 else ""
            print(f"  {trait:30s}  {start:.3f} -> {end:.3f}  ({arrow}{delta:.3f})")

    print()
    print("HEALTH TRAJECTORY:")
    ht = obs["health_trajectory"]
    if ht["means"]:
        print(f"  Gen 0:   mean={ht['means'][0]:.3f}  min={ht['mins'][0]:.3f}  max={ht['maxs'][0]:.3f}")
        print(f"  Final:   mean={ht['means'][-1]:.3f}  min={ht['mins'][-1]:.3f}  max={ht['maxs'][-1]:.3f}")

    print()
    print("DIVERSITY (heterozygosity):")
    di = obs["diversity_index"]
    if di:
        print(f"  Gen 0:   {di[0]:.3f}")
        print(f"  Final:   {di[-1]:.3f}")

    print()
    print("DISEASE PREVALENCE (final generation):")
    for disease, counts in obs["disease_prevalence"].items():
        if counts and counts[-1] > 0:
            print(f"  {disease:40s}  {counts[-1]} cases")

    print()
    print(f"Total matings recorded: {len(obs['mating_log'])}")


def main():
    parser = argparse.ArgumentParser(description="Genomebook Observatory")
    parser.add_argument("--summary", action="store_true", help="Print terminal summary")
    parser.add_argument("--output", type=str, default=str(OBSERVATORY_JSON), help="Output path")
    args = parser.parse_args()

    log_entries = load_evolution_log()
    genomes_by_gen = load_all_genomes()

    if not genomes_by_gen:
        print("ERROR: No genomes found. Run evolve.py first.")
        return

    print(f"Loaded {sum(len(v) for v in genomes_by_gen.values())} genomes across {len(genomes_by_gen)} generations")

    obs = compute_observatory(log_entries, genomes_by_gen)

    out_path = Path(args.output)
    with open(out_path, "w") as f:
        json.dump(obs, f, indent=2)
    print(f"Written: {out_path}")

    if args.summary:
        print()
        print_summary(obs)


if __name__ == "__main__":
    main()

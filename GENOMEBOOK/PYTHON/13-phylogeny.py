"""
13-phylogeny.py -- Interactive Phylogenetic Tree for Genomebook

Builds a focused lineage viewer: select a founder and see their descendants.
Each node shows name, sex, health, top traits. Hover for details.

Usage:
    python 13-phylogeny.py
    python 13-phylogeny.py --max-gen 5
"""

import argparse
import json
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "DATA"
GENOMES_DIR = DATA / "GENOMES"
DEFAULT_OUTPUT = BASE.parent / "slides" / "genomebook" / "phylogeny.html"


def load_all_genomes(max_gen=None):
    genomes = {}
    for gf in sorted(GENOMES_DIR.glob("*.genome.json")):
        g = json.load(open(gf))
        if max_gen is not None and g.get("generation", 0) > max_gen:
            continue
        genomes[g["id"]] = g
    return genomes


def build_tree_data(genomes):
    """Build nodes, edges, and lineage index."""
    nodes = []
    edges = []
    children_of = defaultdict(list)  # parent_id -> [child_ids]
    founders = []

    for gid, g in genomes.items():
        traits = g.get("trait_scores", {})
        top3 = sorted(traits.items(), key=lambda x: x[1], reverse=True)[:3]
        bot2 = sorted(traits.items(), key=lambda x: x[1])[:2]

        name = g.get("name", gid)
        if len(name) > 45:
            name = name[:42] + "..."

        node = {
            "id": gid,
            "name": name,
            "gen": g.get("generation", 0),
            "sex": g.get("sex", "?"),
            "health": round(g.get("health_score", 1.0), 2),
            "conditions": len(g.get("clinical_history", [])),
            "mutations": len(g.get("mutations", [])),
            "top": ", ".join(f"{t.replace('_',' ')}: {s:.2f}" for t, s in top3),
            "weak": ", ".join(f"{t.replace('_',' ')}: {s:.2f}" for t, s in bot2),
            "ancestry": g.get("ancestry", ""),
            "parents": g.get("parents", [None, None]),
        }
        nodes.append(node)

        if g.get("generation", 0) == 0:
            founders.append({"id": gid, "name": name, "sex": g["sex"]})

        parents = g.get("parents", [None, None])
        for pid in parents:
            if pid and pid in genomes:
                edges.append({"from": pid, "to": gid})
                children_of[pid].append(gid)

    return nodes, edges, founders, children_of


def build_html(nodes, edges, founders):
    nodes_json = json.dumps(nodes, default=str)
    edges_json = json.dumps(edges, default=str)
    founders_json = json.dumps(founders, default=str)
    max_gen = max((n["gen"] for n in nodes), default=0)

    founder_options = "\n".join(
        f'<option value="{f["id"]}">{f["name"]} ({f["sex"][0]})</option>' for f in founders
    )

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Genomebook Phylogeny</title>
<style>
:root {{
  --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
  --border: #30363d; --text: #e6edf3; --muted: #8b949e;
  --green: #3fb950; --blue: #58a6ff; --red: #f85149;
  --purple: #bc8cff; --orange: #e3b341; --pink: #f778ba;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; }}

.header {{
  background: var(--bg2); border-bottom: 1px solid var(--border);
  padding: 0.7rem 1.5rem; display: flex; justify-content: space-between; align-items: center; flex-shrink: 0;
}}
.header h1 {{ font-size: 1.1rem; font-weight: 800; }}
.header h1 span {{ color: var(--green); }}
.header .links {{ font-size: 0.75rem; }}
.header a {{ color: var(--blue); text-decoration: none; }}

.controls {{
  background: var(--bg2); border-bottom: 1px solid var(--border);
  padding: 0.5rem 1.5rem; display: flex; gap: 1rem; align-items: center; font-size: 0.78rem; flex-shrink: 0;
}}
.controls label {{ color: var(--muted); }}
.controls select {{
  background: var(--bg3); color: var(--text); border: 1px solid var(--border);
  border-radius: 4px; padding: 0.3rem 0.5rem; font-size: 0.78rem;
}}
.controls .chip {{
  background: var(--bg3); border: 1px solid var(--border); color: var(--muted);
  padding: 0.2rem 0.6rem; border-radius: 10px; font-size: 0.72rem; cursor: pointer;
}}
.controls .chip:hover, .controls .chip.active {{ background: rgba(63,185,80,0.15); color: var(--green); border-color: var(--green); }}

.tree-area {{ flex: 1; overflow: auto; position: relative; }}

/* Tree nodes rendered as HTML divs for proper scaling */
.gen-row {{
  display: flex; align-items: flex-start; padding: 0.4rem 1rem;
  position: relative; min-height: 60px;
}}
.gen-label {{
  width: 80px; flex-shrink: 0; font-size: 0.68rem; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.05em; padding-top: 0.6rem;
  font-weight: 700;
}}
.gen-nodes {{
  display: flex; flex-wrap: wrap; gap: 0.4rem; flex: 1;
}}

.node {{
  background: var(--bg2); border: 1.5px solid var(--border);
  border-radius: 6px; padding: 0.4rem 0.5rem; min-width: 130px; max-width: 180px;
  cursor: pointer; transition: all 0.2s; font-size: 0.72rem; position: relative;
}}
.node:hover {{ border-color: var(--green); transform: translateY(-1px); }}
.node.highlighted {{ border-color: var(--green); background: rgba(63,185,80,0.08); }}
.node.dimmed {{ opacity: 0.25; }}

.node-name {{ font-weight: 700; font-size: 0.75rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.node-meta {{ color: var(--muted); font-size: 0.65rem; margin-top: 0.15rem; }}
.node-bar {{ height: 3px; border-radius: 2px; margin-top: 0.25rem; background: var(--bg3); }}
.node-bar-fill {{ height: 100%; border-radius: 2px; }}

.sex-m {{ border-left: 3px solid var(--blue); }}
.sex-f {{ border-left: 3px solid var(--pink); }}

/* Tooltip */
.tooltip {{
  position: fixed; background: var(--bg2); border: 1px solid var(--green);
  border-radius: 8px; padding: 0.8rem; font-size: 0.78rem; max-width: 340px;
  pointer-events: none; display: none; z-index: 100; line-height: 1.5;
  box-shadow: 0 4px 20px rgba(0,0,0,0.5);
}}
.tt-name {{ font-weight: 700; font-size: 0.9rem; margin-bottom: 0.3rem; }}
.tt-row {{ margin-top: 0.2rem; }}
.tt-label {{ color: var(--muted); font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; }}
.tt-val {{ color: var(--text); }}
.tt-good {{ color: var(--green); }}
.tt-bad {{ color: var(--red); }}
.tt-mid {{ color: var(--orange); }}

/* Connector lines via SVG overlay */
.connectors {{
  position: absolute; top: 0; left: 0; width: 100%; height: 100%;
  pointer-events: none; z-index: 0;
}}

/* Summary stats */
.summary {{
  background: var(--bg2); border-bottom: 1px solid var(--border);
  padding: 0.4rem 1.5rem; display: flex; gap: 2rem; font-size: 0.75rem; flex-shrink: 0;
}}
.summary-item {{ color: var(--muted); }}
.summary-item strong {{ color: var(--green); }}
</style>
</head>
<body>

<div class="header">
  <h1>&#x1F9EC; <span>Genomebook</span> Phylogeny</h1>
  <div class="links">
    <a href="demo.html">Observatory</a> &middot;
    <a href="https://clawbio.github.io/ClawBio/slides/genomebook/">Slides</a> &middot;
    <a href="https://github.com/ClawBio/ClawBio/tree/main/GENOMEBOOK">Code</a>
  </div>
</div>

<div class="controls">
  <label>Lineage of:</label>
  <select id="founder-select" onchange="renderTree()">
    <option value="all">All Founders</option>
    {founder_options}
  </select>
  <label>Color:</label>
  <select id="color-mode" onchange="renderTree()">
    <option value="health">Health</option>
    <option value="sex">Sex</option>
  </select>
  <div class="chip" onclick="toggleShowAll(this)">Show all agents</div>
</div>

<div class="summary" id="summary"></div>

<div class="tree-area" id="tree-area"></div>

<div class="tooltip" id="tooltip"></div>

<script>
const NODES = {nodes_json};
const EDGES = {edges_json};
const FOUNDERS = {founders_json};

// Build lookup maps
const nodeMap = {{}};
for (const n of NODES) nodeMap[n.id] = n;

const childrenOf = {{}};
const parentsOf = {{}};
for (const e of EDGES) {{
  if (!childrenOf[e.from]) childrenOf[e.from] = [];
  childrenOf[e.from].push(e.to);
  if (!parentsOf[e.to]) parentsOf[e.to] = [];
  parentsOf[e.to].push(e.from);
}}

let showAll = false;

function getDescendants(rootId) {{
  const desc = new Set();
  const queue = [rootId];
  while (queue.length) {{
    const id = queue.shift();
    desc.add(id);
    for (const cid of (childrenOf[id] || [])) {{
      if (!desc.has(cid)) queue.push(cid);
    }}
  }}
  return desc;
}}

function getAncestors(id) {{
  const anc = new Set();
  const queue = [id];
  while (queue.length) {{
    const curr = queue.shift();
    anc.add(curr);
    for (const pid of (parentsOf[curr] || [])) {{
      if (!anc.has(pid)) queue.push(pid);
    }}
  }}
  return anc;
}}

function healthColor(h) {{
  if (h >= 0.85) return '#3fb950';
  if (h >= 0.7) return '#58a6ff';
  if (h >= 0.5) return '#e3b341';
  return '#f85149';
}}

function renderTree() {{
  const founderId = document.getElementById('founder-select').value;
  const colorMode = document.getElementById('color-mode').value;

  // Determine visible set
  let visibleSet = null;
  if (founderId !== 'all') {{
    visibleSet = getDescendants(founderId);
    // Also include the founder's co-parents
    for (const id of [...visibleSet]) {{
      const node = nodeMap[id];
      if (node && node.parents) {{
        for (const pid of node.parents) {{
          if (pid && nodeMap[pid]) visibleSet.add(pid);
        }}
      }}
    }}
  }}

  // Group by generation
  const byGen = {{}};
  let visibleNodes = NODES;
  if (visibleSet && !showAll) {{
    visibleNodes = NODES.filter(n => visibleSet.has(n.id));
  }}

  for (const n of visibleNodes) {{
    if (!byGen[n.gen]) byGen[n.gen] = [];
    byGen[n.gen].push(n);
  }}

  const gens = Object.keys(byGen).map(Number).sort((a, b) => a - b);

  // Summary
  const totalVisible = visibleNodes.length;
  const avgHealth = visibleNodes.length ? (visibleNodes.reduce((s, n) => s + n.health, 0) / visibleNodes.length).toFixed(2) : '-';
  const males = visibleNodes.filter(n => n.sex === 'Male').length;
  const females = visibleNodes.filter(n => n.sex === 'Female').length;
  document.getElementById('summary').innerHTML =
    '<span class="summary-item">Agents: <strong>' + totalVisible + '</strong></span>' +
    '<span class="summary-item">Generations: <strong>' + gens.length + '</strong></span>' +
    '<span class="summary-item">Avg Health: <strong>' + avgHealth + '</strong></span>' +
    '<span class="summary-item">Sex: <strong>' + males + 'M / ' + females + 'F</strong></span>';

  // Render rows
  let html = '';
  for (const gen of gens) {{
    const agents = byGen[gen];
    html += '<div class="gen-row"><div class="gen-label">Gen ' + gen + '<br><span style="color:var(--green)">' + agents.length + '</span></div><div class="gen-nodes">';
    for (const n of agents) {{
      const isHighlighted = visibleSet ? visibleSet.has(n.id) : true;
      const sexClass = n.sex === 'Male' ? 'sex-m' : 'sex-f';
      const hlClass = (!showAll || isHighlighted) ? '' : ' dimmed';

      let barColor;
      if (colorMode === 'health') {{
        barColor = healthColor(n.health);
      }} else {{
        barColor = n.sex === 'Male' ? 'var(--blue)' : 'var(--pink)';
      }}

      html += '<div class="node ' + sexClass + hlClass + '" data-id="' + n.id + '" ' +
        'onmouseenter="showTip(event,\'' + n.id + '\')" onmouseleave="hideTip()" ' +
        'onclick="focusLineage(\'' + n.id + '\')">' +
        '<div class="node-name">' + esc(n.name) + '</div>' +
        '<div class="node-meta">' + n.sex[0] + ' &middot; ' +
        (colorMode === 'health' ? ('<span style="color:' + barColor + '">' + n.health + '</span>') : n.sex) +
        (n.conditions > 0 ? ' &middot; ' + n.conditions + ' cond' : '') +
        '</div>' +
        '<div class="node-bar"><div class="node-bar-fill" style="width:' + (n.health * 100) + '%;background:' + barColor + '"></div></div>' +
        '</div>';
    }}
    html += '</div></div>';
  }}

  document.getElementById('tree-area').innerHTML = html;
}}

function showTip(event, id) {{
  const n = nodeMap[id];
  if (!n) return;
  const tip = document.getElementById('tooltip');
  const hc = n.health >= 0.7 ? 'tt-good' : n.health >= 0.5 ? 'tt-mid' : 'tt-bad';
  tip.innerHTML =
    '<div class="tt-name">' + esc(n.name) + '</div>' +
    '<div class="tt-row"><span class="tt-label">Generation: </span>' + n.gen + ' &middot; ' + n.sex + '</div>' +
    (n.ancestry ? '<div class="tt-row"><span class="tt-label">Ancestry: </span>' + esc(n.ancestry) + '</div>' : '') +
    '<div class="tt-row"><span class="tt-label">Health: </span><span class="' + hc + '">' + n.health + '</span>' +
    (n.conditions > 0 ? ' &middot; ' + n.conditions + ' conditions' : '') +
    (n.mutations > 0 ? ' &middot; ' + n.mutations + ' mutations' : '') + '</div>' +
    '<div class="tt-row"><span class="tt-label">Strengths: </span>' + esc(n.top) + '</div>' +
    '<div class="tt-row"><span class="tt-label">Weaknesses: </span>' + esc(n.weak) + '</div>' +
    (n.parents[0] ? '<div class="tt-row"><span class="tt-label">Parents: </span>' + esc(n.parents[0]) + ' x ' + esc(n.parents[1]) + '</div>' : '<div class="tt-row"><span class="tt-label">FOUNDER</span></div>');
  tip.style.display = 'block';
  tip.style.left = Math.min(event.clientX + 15, window.innerWidth - 360) + 'px';
  tip.style.top = Math.min(event.clientY + 15, window.innerHeight - 200) + 'px';
}}

function hideTip() {{ document.getElementById('tooltip').style.display = 'none'; }}

function focusLineage(id) {{
  const n = nodeMap[id];
  if (!n) return;
  // If it's a founder, select it in the dropdown
  if (n.gen === 0) {{
    document.getElementById('founder-select').value = id;
    renderTree();
    return;
  }}
  // Otherwise, highlight this agent's lineage (ancestors + descendants)
  const lineage = new Set([...getAncestors(id), ...getDescendants(id)]);
  document.querySelectorAll('.node').forEach(el => {{
    if (lineage.has(el.dataset.id)) {{
      el.classList.add('highlighted');
      el.classList.remove('dimmed');
    }} else {{
      el.classList.remove('highlighted');
      el.classList.add('dimmed');
    }}
  }});
}}

function toggleShowAll(el) {{
  showAll = !showAll;
  el.classList.toggle('active');
  el.textContent = showAll ? 'Show lineage only' : 'Show all agents';
  renderTree();
}}

function esc(s) {{ if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }}

renderTree();
</script>
</body>
</html>'''


def main():
    parser = argparse.ArgumentParser(description="Genomebook Phylogenetic Tree")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-gen", type=int, default=None)
    args = parser.parse_args()

    genomes = load_all_genomes(max_gen=args.max_gen)
    print(f"Loaded {len(genomes)} genomes")

    nodes, edges, founders, children_of = build_tree_data(genomes)
    print(f"Nodes: {len(nodes)}, Edges: {len(edges)}, Founders: {len(founders)}")

    html = build_html(nodes, edges, founders)
    out = Path(args.output)
    out.write_text(html)
    print(f"Written: {out}")
    print(f"Size: {out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()

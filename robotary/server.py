"""Robotary — Robot Terry web interface for ClawBio skills.

Run:
    python robotary/server.py
    # → http://localhost:5112
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
ROBOTARY_DIR = Path(__file__).resolve().parent

# Manuel Corpas genome — used as default input for genome-based skills
GENOME_PATH = SKILLS_DIR / "genome-compare" / "data" / "manuel_corpas_23andme.txt.gz"

# Maps skill directory names → clawbio.py registry names (core skills only)
SKILL_REGISTRY_MAP: dict[str, str] = {
    "pharmgx-reporter": "pharmgx",
    "nutrigx_advisor": "nutrigx",
    "genome-compare": "compare",
    "gwas-prs": "prs",
    "clinpgx": "clinpgx",
    "gwas-lookup": "gwas",
    "profile-report": "profile",
}

# Skills that accept a genome file as --input (vs --demo)
GENOME_SKILLS = {"pharmgx-reporter", "nutrigx_advisor", "genome-compare", "gwas-prs"}

# Core skills exposed in Robotary (subset of all ClawBio skills)
CORE_SKILLS = {
    "pharmgx-reporter", "nutrigx_advisor", "genome-compare",
    "gwas-prs", "clinpgx", "gwas-lookup", "profile-report",
}


def build_skill_catalog() -> dict[str, str]:
    """Discover skills by scanning skills/*/SKILL.md. Returns {name: description}."""
    catalog = {}
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name not in CORE_SKILLS:
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        lines = skill_md.read_text().split("\n")
        desc_lines = []
        in_frontmatter = False
        for line in lines:
            if line.strip() == "---":
                in_frontmatter = not in_frontmatter
                continue
            if not in_frontmatter and line.strip():
                desc_lines.append(line.strip())
                if len(desc_lines) >= 3:
                    break
        catalog[skill_dir.name] = " ".join(desc_lines)[:200]
    return catalog


SKILL_CATALOG = build_skill_catalog()

import httpx

# LLM configuration (OpenAI-compatible API)
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("CLAWBIO_MODEL", "gpt-4o-mini")


ROUTING_PROMPT = """You are a skill router for ClawBio, a bioinformatics agent.
Given a user query, pick the single best skill to handle it.

Available skills:
{skills}

Respond with ONLY a JSON object:
{{"skill": "<skill-name>", "confidence": <0-1>, "reasoning": "<one sentence>", "params": {{<skill-specific params>}}}}

Param extraction rules:
- gwas-prs: extract {{"trait": "<disease/trait name>"}} from the query
- clinpgx: extract {{"gene": "<gene symbol>"}} if mentioned, else empty
- gwas-lookup: extract {{"rsid": "<rs number>"}} if mentioned, else empty
- All other skills: params should be {{}}

If no skill matches, respond: {{"skill": null, "confidence": 0, "reasoning": "<why>", "params": {{}}}}""".format(
    skills="\n".join(f"- {name}: {desc}" for name, desc in SKILL_CATALOG.items())
)


async def llm_chat(messages: list[dict], max_tokens: int = 300) -> dict:
    """Call OpenAI-compatible chat API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def route_query(query: str) -> dict:
    """Route a user query to the best skill. Returns {skill, confidence, reasoning, params}."""
    messages = [
        {"role": "system", "content": ROUTING_PROMPT},
        {"role": "user", "content": query},
    ]
    resp = await llm_chat(messages, max_tokens=200)
    text = resp["choices"][0]["message"]["content"].strip()

    # Strip markdown code fences if present
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"skill": None, "confidence": 0, "reasoning": f"Parse error: {text[:100]}", "params": {}}

    # Ensure params key exists
    if "params" not in result:
        result["params"] = {}

    return result


app = FastAPI(title="Robotary")

# Serve static assets if directory exists
_static_dir = ROBOTARY_DIR / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/")
async def index():
    html_path = ROBOTARY_DIR / "index.html"
    return HTMLResponse(html_path.read_text())


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Robotary — Robot Terry")
    print(f"  Skills loaded: {len(SKILL_CATALOG)}")
    print(f"  Genome: {GENOME_PATH.name}")
    print("  Open http://localhost:5112")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=5112)

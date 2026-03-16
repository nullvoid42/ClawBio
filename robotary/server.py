"""Robotary — Robot Terri web interface for ClawBio skills.

Run:
    python robotary/server.py
    # → http://localhost:5112
"""
from __future__ import annotations

import dotenv
dotenv.load_dotenv()

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
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

INTRO_MESSAGE = """Hey! I'm **Robot Terri** — your bioinformatics assistant powered by ClawBio.

I've got **Manuel Corpas's genome** loaded and ready to analyse. Here's what I can do:

| Skill | What it does | Try asking... |
|---|---|---|
| **Pharmacogenomics** | Drug-gene interactions & metabolism | *"What medications should I worry about?"* |
| **Nutrigenomics** | Diet & nutrition genetics | *"What should I eat based on my genes?"* |
| **Genome Compare** | Compare DNA to George Church | *"How similar am I to George Church?"* |
| **Polygenic Risk** | Disease risk scores | *"What's my risk for type 2 diabetes?"* |
| **ClinPGx** | Gene-drug database lookup | *"Look up CYP2D6 drug interactions"* |
| **GWAS Lookup** | Variant search across databases | *"Look up rs3798220"* |
| **Profile Report** | Full genomic profile summary | *"Give me a full profile report"* |

Just ask a question and I'll route it to the right skill!"""


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

# LLM configuration (OpenAI-compatible API)
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("CLAWBIO_MODEL", "gpt-4o-mini")

if not LLM_API_KEY:
    import warnings
    warnings.warn("LLM_API_KEY not set — LLM calls will fail. Set it in your environment.")


ROUTING_PROMPT = """You are a skill router for ClawBio, a bioinformatics agent.
Given a user query, pick the single best skill to handle it.

IMPORTANT: Always try to match a skill. Be generous — if the query is even loosely related to genomics, health, medications, diet, ancestry, or risk, pick the best-fit skill. For vague or broad queries (e.g. "what can you tell me about my genome", "analyse my data", "what medications should I worry about"), pick the most relevant skill. Only return null if the query is completely unrelated to biology or genomics (e.g. "what's the weather").

Default skill preferences for vague queries:
- General health/medications → pharmgx-reporter
- General diet/nutrition → nutrigx_advisor
- General risk/disease → gwas-prs
- General genome/DNA → profile-report
- "What can you do" / overview → profile-report

Available skills:
{skills}

Respond with ONLY a JSON object:
{{"skill": "<skill-name>", "confidence": <0-1>, "reasoning": "<one sentence>", "params": {{<skill-specific params>}}}}

Param extraction rules:
- gwas-prs: extract {{"trait": "<disease/trait name>"}} from the query. If no specific trait, use {{"trait": "type 2 diabetes"}} as default.
- clinpgx: extract {{"gene": "<gene symbol>"}} if mentioned, else empty
- gwas-lookup: extract {{"rsid": "<rs number>"}} if mentioned, else empty
- All other skills: params should be {{}}

If no skill matches, respond: {{"skill": null, "confidence": 0, "reasoning": "<why>", "params": {{}}}}""".format(
    skills="\n".join(f"- {name}: {desc}" for name, desc in SKILL_CATALOG.items())
)


import re

_INTRO_PATTERNS = re.compile(
    r"(^hi$|^hey|^hello|what can you do|what skills|help me|"
    r"who are you|what are you|introduce|overview|getting started|"
    r"how does this work|what do you know)",
    re.IGNORECASE,
)


def is_intro_query(query: str) -> bool:
    """Check if query is a greeting or request for overview."""
    return bool(_INTRO_PATTERNS.search(query.strip()))


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


def build_skill_command(skill_dir_name: str, params: dict) -> tuple[list[str], str]:
    """Build the subprocess command for a skill. Returns (cmd_args, tmpdir_path)."""
    tmpdir = tempfile.mkdtemp(prefix=f"robotary_{skill_dir_name}_")
    registry_name = SKILL_REGISTRY_MAP.get(skill_dir_name, skill_dir_name)

    cmd = [sys.executable, str(PROJECT_ROOT / "clawbio.py"), "run", registry_name]

    if skill_dir_name in GENOME_SKILLS:
        cmd.extend(["--input", str(GENOME_PATH)])
        # Add skill-specific params
        if skill_dir_name == "gwas-prs" and params.get("trait"):
            cmd.extend(["--trait", params["trait"]])
    elif skill_dir_name == "clinpgx":
        if params.get("gene"):
            cmd.extend(["--gene", params["gene"]])
        else:
            cmd.append("--demo")
    elif skill_dir_name == "gwas-lookup":
        if params.get("rsid"):
            cmd.extend(["--rsid", params["rsid"]])
        else:
            cmd.append("--demo")
    else:
        # profile-report and any other skill: use --demo
        cmd.append("--demo")

    cmd.extend(["--output", tmpdir])
    return cmd, tmpdir


async def run_skill(skill_dir_name: str, params: dict, on_log: callable) -> dict:
    """Run a skill subprocess, streaming stdout/stderr via on_log callback.

    Returns {success, report, result_json, stdout, stderr}.
    """
    cmd, tmpdir = build_skill_command(skill_dir_name, params)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )

        stdout_lines = []
        stderr_lines = []

        async def read_stream(stream, lines, is_stderr=False):
            async for raw_line in stream:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                lines.append(line)
                if on_log:
                    await on_log(line)

        await asyncio.gather(
            read_stream(proc.stdout, stdout_lines),
            read_stream(proc.stderr, stderr_lines, is_stderr=True),
        )
        await proc.wait()

        # Read outputs
        tmppath = Path(tmpdir)
        report_file = tmppath / "report.md"
        result_file = tmppath / "result.json"

        report = report_file.read_text() if report_file.exists() else None
        result_json = json.loads(result_file.read_text()) if result_file.exists() else None

        return {
            "success": proc.returncode == 0,
            "report": report,
            "result_json": result_json,
            "stdout": "\n".join(stdout_lines),
            "stderr": "\n".join(stderr_lines),
        }
    except Exception as e:
        return {"success": False, "report": None, "result_json": None, "error": str(e)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


INTERPRET_SYSTEM = """You are Robot Terri, a friendly bioinformatics assistant.
Summarise this genomic report conversationally. Be direct and helpful.

Rules:
- One short opening sentence answering the user's question
- 2-3 bullet points with the most important findings
- One sentence takeaway
- End with: "*ClawBio is a research and educational tool. It is not a medical device and does not provide clinical diagnoses. Consult a healthcare professional before making any medical decisions.*"
- No headers, no sections, no technical deep-dives
- Do NOT list every drug/gene — just the highlights
- This was run on Manuel Corpas's publicly available genome — mention that these are demo results"""


def build_interpret_messages(query: str, skill: str, report: str) -> list[dict]:
    """Build the messages for the interpretation LLM call."""
    if len(report) > 8000:
        report = report[:8000] + "\n\n... (report truncated) ..."

    user_content = f"User asked: \"{query}\"\nSkill: {skill}\n\nRaw report:\n---\n{report}\n---"

    return [
        {"role": "system", "content": INTERPRET_SYSTEM},
        {"role": "user", "content": user_content},
    ]


async def stream_interpret(query: str, skill: str, report: str, on_token: callable):
    """Stream the interpretation response token-by-token via on_token callback."""
    messages = build_interpret_messages(query, skill, report)

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "max_tokens": 500,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        await on_token(content)
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


app = FastAPI(title="Robotary")

# Serve static assets if directory exists
_static_dir = ROBOTARY_DIR / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/")
async def index():
    html_path = ROBOTARY_DIR / "index.html"
    return HTMLResponse(html_path.read_text())


# Serialization lock — single skill run at a time
_run_lock = asyncio.Lock()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    async def send_brain(stage: str, status: str, log: str):
        await ws.send_json({"type": "brain", "stage": stage, "status": status, "log": log})

    async def send_chat(content: str, done: bool = False):
        msg = {"type": "chat", "done": done}
        if content:
            msg["content"] = content
        await ws.send_json(msg)

    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") != "message":
                continue

            query = data.get("content", "").strip()
            if not query:
                continue

            async with _run_lock:
                # ── Intro shortcut (no LLM needed) ──
                if is_intro_query(query):
                    await send_brain("route", "done", "→ introduction")
                    await send_brain("load", "done", "No data needed")
                    await send_brain("run", "done", "Showing skill catalog")
                    await send_brain("interpret", "done", "Ready")
                    await send_chat(INTRO_MESSAGE)
                    await send_chat("", done=True)
                    continue

                # ── Stage 1: Route ──
                await send_brain("route", "active", "Routing query...")
                try:
                    route_result = await route_query(query)
                    skill = route_result.get("skill")
                    params = route_result.get("params", {})
                    confidence = route_result.get("confidence", 0)
                    reasoning = route_result.get("reasoning", "")

                    if skill:
                        await send_brain("route", "done",
                                         f"→ {skill} ({confidence:.2f}) — {reasoning}")
                    else:
                        await send_brain("route", "done", "→ introduction (no skill matched)")
                        await send_chat(INTRO_MESSAGE)
                        await send_chat("", done=True)
                        continue
                except Exception as e:
                    await send_brain("route", "error", f"Routing failed: {e}")
                    await send_chat(INTRO_MESSAGE)
                    await send_chat("", done=True)
                    continue

                # ── Stage 2: Load Data ──
                await send_brain("load", "active", "Preparing data...")
                if skill in GENOME_SKILLS:
                    await send_brain("load", "done",
                                     f"Manuel Corpas genome · 23andMe format · {GENOME_PATH.name}")
                else:
                    await send_brain("load", "done", f"Using demo/API mode for {skill}")

                # ── Stage 3: Run Skill ──
                await send_brain("run", "active", f"Running {skill}...")

                async def on_skill_log(line: str):
                    await send_brain("run", "active", line)

                try:
                    result = await run_skill(skill, params, on_log=on_skill_log)
                    if result["success"]:
                        await send_brain("run", "done", "Skill completed — report generated")
                    else:
                        error = result.get("error") or result.get("stderr", "Unknown error")
                        await send_brain("run", "error", f"Skill failed: {error[:200]}")
                        await send_chat(f"Something went wrong running {skill}. Try asking differently.")
                        await send_chat("", done=True)
                        continue
                except Exception as e:
                    await send_brain("run", "error", f"Execution error: {e}")
                    await send_chat(f"Something went wrong running {skill}. Try asking differently.")
                    await send_chat("", done=True)
                    continue

                # ── Stage 4: Interpret ──
                report = result.get("report") or result.get("stdout", "")
                if not report:
                    await send_brain("interpret", "error", "No report to interpret")
                    await send_chat(f"The {skill} skill ran but produced no output.")
                    await send_chat("", done=True)
                    continue

                await send_brain("interpret", "active", "Interpreting results...")

                async def on_token(token: str):
                    await send_chat(token)

                try:
                    await stream_interpret(query, skill, report, on_token=on_token)
                    await send_brain("interpret", "done", "Response ready")
                    await send_chat("", done=True)
                except Exception as e:
                    await send_brain("interpret", "error", f"Interpretation failed: {e}")
                    # Fallback: send raw report snippet
                    await send_chat(f"Here's the raw output from {skill}:\n\n{report[:2000]}")
                    await send_chat("", done=True)

    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Robotary — Robot Terri")
    print(f"  Skills loaded: {len(SKILL_CATALOG)}")
    print(f"  Genome: {GENOME_PATH.name}")
    print("  Open http://localhost:5112")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=5112)

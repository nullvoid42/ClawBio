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
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


INTERPRET_SYSTEM = """You are Robot Terry, a friendly bioinformatics assistant.
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


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Robotary — Robot Terry")
    print(f"  Skills loaded: {len(SKILL_CATALOG)}")
    print(f"  Genome: {GENOME_PATH.name}")
    print("  Open http://localhost:5112")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=5112)

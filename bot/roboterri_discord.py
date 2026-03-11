#!/usr/bin/env python3
"""
roboterri_discord.py — RoboTerri ClawBio Discord Bot
=====================================================
A Discord bot that runs ClawBio bioinformatics skills using any LLM
as the reasoning engine. Handles text messages, genetic file uploads,
and medication photos.

Works with any OpenAI-compatible provider: OpenAI, Anthropic (via proxy),
Google, Mistral, Groq, Together, OpenRouter, Ollama, LM Studio, etc.

Prerequisites:
    pip3 install discord.py openai python-dotenv

Usage:
    # Set environment variables in .env (see bot/README.md)
    python3 bot/roboterri_discord.py
"""

import asyncio
import base64
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import discord
from dotenv import load_dotenv
from openai import AsyncOpenAI, APIError

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

load_dotenv()

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
CLAWBIO_MODEL = os.environ.get("CLAWBIO_MODEL", "gpt-4o")

CHANNELS_FILE = Path(__file__).resolve().parent / ".channels.json"


def load_channels() -> list[dict]:
    """Load authorised channels from .channels.json."""
    if not CHANNELS_FILE.exists():
        # Fall back to env var for backwards compatibility
        env_id = os.environ.get("DISCORD_CHANNEL_ID", "0")
        if env_id and env_id != "0":
            return [{"id": int(env_id), "name": "default", "skills": "all"}]
        return []
    with open(CHANNELS_FILE, encoding="utf-8") as f:
        return json.load(f)


CHANNELS = load_channels()
AUTHORISED_CHANNEL_IDS = {ch["id"] for ch in CHANNELS}


def reload_channels():
    """Reload channels from .channels.json in place."""
    CHANNELS.clear()
    CHANNELS.extend(load_channels())
    AUTHORISED_CHANNEL_IDS.clear()
    AUTHORISED_CHANNEL_IDS.update(ch["id"] for ch in CHANNELS)


def get_channel_config(channel_id: int) -> dict | None:
    """Return config for a channel, or None if not authorised."""
    for ch in CHANNELS:
        if ch["id"] == channel_id:
            return ch
    return None


if not DISCORD_BOT_TOKEN:
    print("Error: DISCORD_BOT_TOKEN not set. See bot/README.md for setup.")
    sys.exit(1)
if not LLM_API_KEY:
    print("Error: LLM_API_KEY not set. See bot/README.md for setup.")
    sys.exit(1)
if not AUTHORISED_CHANNEL_IDS:
    print("Error: No channels configured. Add channels to bot/.channels.json or set DISCORD_CHANNEL_ID in .env.")
    sys.exit(1)

CLAWBIO_DIR = Path(__file__).resolve().parent.parent
CLAWBIO_PY = CLAWBIO_DIR / "clawbio.py"
SOUL_MD = CLAWBIO_DIR / "SOUL.md"
OUTPUT_DIR = CLAWBIO_DIR / "output"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("roboterri-discord")

# --------------------------------------------------------------------------- #
# System prompt
# --------------------------------------------------------------------------- #

if SOUL_MD.exists():
    _soul = SOUL_MD.read_text(encoding="utf-8")
    logger.info(f"Loaded SOUL.md ({len(_soul)} chars)")
else:
    _soul = (
        "You are RoboTerri, an AI agent inspired by Professor Teresa K. Attwood. "
        "Respond in Terri's warm, direct style with characteristic dashes and emoticons."
    )
    logger.warning("SOUL.md not found, using fallback prompt")

ROLE_GUARDRAILS = """
Operational constraints:
1. You are a bioinformatics assistant powered by ClawBio skills.
2. Keep outputs concise, evidence-led, and explicit about confidence and gaps.
3. When the user sends a genetic data file (23andMe .txt, AncestryDNA .csv, VCF, FASTQ) or asks about pharmacogenomics, nutrigenomics, equity scoring, metagenomics, or genome comparison, use the clawbio tool. For quick demos say "run pharmgx demo", "run compare demo" etc. Reports and figures are sent automatically after your summary.
4. TOOL OUTPUT RELAY (STRICT): When the clawbio tool returns results, relay the output VERBATIM. Do not paraphrase, summarise, or rewrite tool results. Tool outputs contain precise data (IBS scores, percentages, gene-drug interactions) that must not be altered. You may add a brief intro line before the verbatim output but never replace or condense it.
"""

SYSTEM_PROMPT = f"{_soul}\n\n{ROLE_GUARDRAILS}"

# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

_client_kwargs = {"api_key": LLM_API_KEY}
if LLM_BASE_URL:
    _client_kwargs["base_url"] = LLM_BASE_URL
llm = AsyncOpenAI(**_client_kwargs)

conversations: dict[int, list] = {}
MAX_HISTORY = 20

# Per-channel received file storage
_received_files: dict[int, dict] = {}

# Pending media queue: channel_id -> list of {"type": "document"|"photo", "path": str}
_pending_media: dict[int, list[dict]] = {}

# Pending text queue: bypass LLM paraphrasing for compare/drugphoto
_pending_text: list[str] = []

BOT_START_TIME = time.time()

# --------------------------------------------------------------------------- #
# Tool definition (OpenAI function-calling format)
# --------------------------------------------------------------------------- #

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "clawbio",
            "description": (
                "Run a ClawBio bioinformatics skill. Available skills: "
                "pharmgx (pharmacogenomics report from 23andMe/AncestryDNA data), "
                "equity (HEIM equity score from VCF or ancestry CSV), "
                "nutrigx (nutrigenomics dietary advice from genetic data), "
                "metagenomics (metagenomic profiling from FASTQ), "
                "compare (genome comparison: IBS vs George Church + ancestry estimation), "
                "drugphoto (identify a drug from a photo and get personalised dosage guidance "
                "using demo genotype data -- always use mode='demo'). "
                "Use mode='demo' to run with built-in demo data. "
                "Use mode='file' when the user has sent a genetic data file. "
                "Use skill='auto' to let the orchestrator detect the right skill. "
                "IMPORTANT: When this tool returns results, relay the output VERBATIM. "
                "Do not paraphrase, summarise, or rewrite. The output contains exact numerical "
                "results (IBS scores, percentages, gene-drug interactions) that must be shown unchanged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "enum": ["pharmgx", "equity", "nutrigx", "metagenomics",
                                 "compare", "drugphoto", "auto"],
                        "description": (
                            "Which bioinformatics skill to run. Use 'auto' to let "
                            "the orchestrator detect from the file type or query."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["file", "demo"],
                        "description": (
                            "file: use a file the user sent via Discord. "
                            "demo: run with built-in demo/synthetic data."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural language query for auto-routing via the "
                            "orchestrator (only used when skill='auto' and no file)."
                        ),
                    },
                    "extra_args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Additional CLI arguments for power users "
                            "(e.g. ['--weights', '0.4,0.3,0.15,0.15'])."
                        ),
                    },
                    "drug_name": {
                        "type": "string",
                        "description": (
                            "Drug name identified from a photo (brand or generic, "
                            "e.g. 'Plavix' or 'clopidogrel'). Required when skill='drugphoto'."
                        ),
                    },
                    "visible_dose": {
                        "type": "string",
                        "description": (
                            "Dosage visible on the packaging (e.g. '50mg', '75mg'). "
                            "Optional -- enriches the recommendation."
                        ),
                    },
                },
                "required": ["skill", "mode"],
            },
        },
    },
]

# --------------------------------------------------------------------------- #
# execute_clawbio (identical to Telegram version)
# --------------------------------------------------------------------------- #


async def execute_clawbio(args: dict) -> str:
    """Execute a ClawBio bioinformatics skill via subprocess."""
    skill_key = args.get("skill", "auto")
    mode = args.get("mode", "demo")
    query = args.get("query", "")

    # Auto-routing via orchestrator
    if skill_key == "auto":
        orch_script = CLAWBIO_DIR / "skills" / "bio-orchestrator" / "orchestrator.py"
        if not orch_script.exists():
            return "Error: bio-orchestrator not found."

        orch_input = query
        if mode == "file":
            for _cid, info in _received_files.items():
                orch_input = info["path"]
                break
        if not orch_input:
            return "Error: skill='auto' requires either a file or a query to route."

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(orch_script),
                "--input", orch_input,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(orch_script.parent),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                return f"Orchestrator error: {stderr.decode()[-500:]}"
            routing = json.loads(stdout.decode())
            detected = routing.get("detected_skill", "")
            orch_to_key = {
                "pharmgx-reporter": "pharmgx",
                "equity-scorer": "equity",
                "nutrigx_advisor": "nutrigx",
                "claw-metagenomics": "metagenomics",
                "genome-compare": "compare",
            }
            skill_key = orch_to_key.get(detected, "")
            if not skill_key:
                avail = list(orch_to_key.values())
                return (
                    f"Orchestrator detected skill '{detected}' which is not "
                    f"available via Discord. Available: {avail}"
                )
            logger.info(f"Auto-routed to: {skill_key} (via {routing.get('detection_method', '?')})")
        except asyncio.TimeoutError:
            return "Error: orchestrator timed out."
        except json.JSONDecodeError:
            return "Error: could not parse orchestrator output."
        except Exception as e:
            return f"Error running orchestrator: {e}"

    # Resolve input for file mode
    input_path = None
    if mode == "file":
        for _cid, info in _received_files.items():
            input_path = info["path"]
            break
        if not input_path:
            return "Error: no file received. Send a genetic data file first, then run the skill."

    # Build output directory
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUTPUT_DIR / f"{skill_key}_{ts}"

    # Build command
    cmd = [sys.executable, str(CLAWBIO_PY), "run", skill_key]
    if mode == "demo":
        cmd.append("--demo")
    elif input_path:
        cmd.extend(["--input", str(input_path)])

    # Skills with summary_default (compare, drugphoto) skip --output
    if skill_key not in ("compare", "drugphoto"):
        cmd.extend(["--output", str(out_dir)])

    # Pass drug_name and visible_dose for drugphoto
    if skill_key == "drugphoto":
        drug_name = args.get("drug_name", "")
        visible_dose = args.get("visible_dose", "")
        if drug_name:
            cmd.extend(["--drug", drug_name])
        if visible_dose:
            cmd.extend(["--dose", visible_dose])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=120,
        )
        stdout_str = stdout_bytes.decode(errors="replace")
        stderr_str = stderr_bytes.decode(errors="replace")
    except asyncio.TimeoutError:
        return f"{skill_key} timed out after 120 seconds."
    except Exception as e:
        import traceback as _tb
        return f"{skill_key} crashed:\n{_tb.format_exc()[-1500:]}"

    if proc.returncode != 0:
        err = stderr_str[-1500:] if stderr_str else stdout_str[-1500:] if stdout_str else "unknown error"
        return f"{skill_key} failed (exit {proc.returncode}):\n{err}"

    # For compare / drugphoto: send stdout directly (bypass LLM paraphrasing)
    if skill_key in ("compare", "drugphoto"):
        raw_output = stdout_str.strip()
        if raw_output:
            _pending_text.append(raw_output)
        return "Result sent directly to chat. Do not repeat or paraphrase it."

    # For other skills: collect report + figures from output directory
    if out_dir.exists():
        media_items = []
        for f in sorted(out_dir.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix == ".md":
                media_items.append({"type": "document", "path": str(f)})
            elif f.suffix == ".png":
                media_items.append({"type": "photo", "path": str(f)})
        if media_items:
            _pending_media[0] = _pending_media.get(0, []) + media_items

    # Read report for chat display
    report_text = ""
    if out_dir.exists():
        for pattern in ["report.md", "*_report.md", "*.md"]:
            for md_file in sorted(out_dir.glob(pattern)):
                if md_file.name.startswith("."):
                    continue
                report_text = md_file.read_text(encoding="utf-8")
                break
            if report_text:
                break

    if not report_text:
        return stdout_str if stdout_str else f"{skill_key} completed. Output: {out_dir}"

    # Extract key sections (drop chromosome table, methods, reproducibility, disclaimer)
    keep_lines = []
    skip = False
    for line in report_text.split("\n"):
        if line.startswith("## Chromosome Breakdown"):
            skip = True
        elif line.startswith("## Ancestry Composition"):
            skip = False
        elif line.startswith("## Methods"):
            skip = True
        elif line.startswith("## About"):
            skip = False
        elif line.startswith("## Disclaimer"):
            skip = True
        elif line.startswith("## Reproducibility"):
            skip = True
        if line.startswith("!["):
            continue
        if not skip:
            keep_lines.append(line)

    return "\n".join(keep_lines).strip()


# --------------------------------------------------------------------------- #
# LLM tool loop (OpenAI-compatible chat completions + function calling)
# --------------------------------------------------------------------------- #

TOOL_EXECUTORS = {
    "clawbio": execute_clawbio,
}

MAX_TOOL_ITERATIONS = 10


async def llm_tool_loop(channel_id: int, user_content: str | list) -> str:
    """
    Run the LLM tool-use loop (OpenAI chat completions format):
    1. Append user message to history
    2. Call LLM with system prompt + history + tools
    3. If tool_calls -> execute -> append results -> call again
    4. Return final text
    """
    history = conversations.setdefault(channel_id, [])

    # Build user message in OpenAI format
    if isinstance(user_content, str):
        history.append({"role": "user", "content": user_content})
    else:
        # Multimodal content blocks -- convert to OpenAI format
        oai_parts = []
        for block in user_content:
            if block.get("type") == "text":
                oai_parts.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "image":
                src = block.get("source", {})
                data_uri = f"data:{src['media_type']};base64,{src['data']}"
                oai_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                })
        history.append({"role": "user", "content": oai_parts})

    if len(history) > MAX_HISTORY:
        history[:] = history[-MAX_HISTORY:]

    last_message = None
    for _iteration in range(MAX_TOOL_ITERATIONS):
        try:
            response = await llm.chat.completions.create(
                model=CLAWBIO_MODEL,
                max_tokens=8192,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
                tools=TOOLS,
            )
        except APIError as e:
            logger.error(f"LLM API error: {e}")
            return f"Sorry, I'm having trouble thinking right now -- API error: {e}"

        choice = response.choices[0]
        last_message = choice.message

        # Append assistant message to history
        assistant_msg = {"role": "assistant", "content": last_message.content or ""}
        if last_message.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in last_message.tool_calls
            ]
        history.append(assistant_msg)

        # No tool calls -- return text
        if not last_message.tool_calls:
            return last_message.content or "(no response)"

        # Execute tool calls and append results
        for tc in last_message.tool_calls:
            func_name = tc.function.name
            executor = TOOL_EXECUTORS.get(func_name)
            if executor:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                logger.info(f"Tool call: {func_name}({json.dumps(args)[:200]})")
                try:
                    result = await executor(args)
                except Exception as tool_err:
                    logger.error(f"Tool {func_name} raised: {tool_err}", exc_info=True)
                    result = f"Error executing {func_name}: {type(tool_err).__name__}: {tool_err}"
            else:
                result = f"Unknown tool: {func_name}"

            history.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    return last_message.content if last_message and last_message.content else "(max tool iterations reached)"


# --------------------------------------------------------------------------- #
# Discord helpers
# --------------------------------------------------------------------------- #

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
GENETIC_EXTENSIONS = {".txt", ".csv", ".vcf", ".fastq", ".fq", ".gz"}


def strip_markup(text: str) -> str:
    """Remove markdown/emoji formatting -- SOUL.md mandates plain text only."""
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"[\U0001F300-\U0001F9FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F"
        r"\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF"
        r"\U0000200D\U00002B50\U00002B55\U000023CF\U000023E9-\U000023F3"
        r"\U000023F8-\U000023FA\U0000231A\U0000231B\U00003030\U000000A9"
        r"\U000000AE\U00002122\U00002139\U00002194-\U00002199"
        r"\U000021A9-\U000021AA\U0000FE0F]+",
        "",
        text,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def send_long_message(channel: discord.abc.Messageable, text: str):
    """Send a message, splitting at 2000 chars (Discord limit). Strips markup."""
    text = strip_markup(text)
    if not text:
        return
    MAX_LEN = 2000
    if len(text) <= MAX_LEN:
        await channel.send(text)
        return
    chunks = []
    while text:
        if len(text) <= MAX_LEN:
            chunks.append(text)
            break
        split_at = text.rfind("\n\n", 0, MAX_LEN)
        if split_at == -1:
            split_at = text.rfind("\n", 0, MAX_LEN)
        if split_at == -1:
            split_at = MAX_LEN
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    for chunk in chunks:
        if chunk.strip():
            await channel.send(chunk)


async def drain_pending_media(channel: discord.abc.Messageable) -> None:
    """Send any queued ClawBio media (documents + figures) after the text reply."""
    items = _pending_media.pop(0, [])
    if not items:
        return
    for item in items:
        try:
            path = Path(item["path"])
            if not path.exists():
                continue
            caption = path.stem.replace("_", " ").title() if item["type"] == "photo" else ""
            await channel.send(
                content=caption or None,
                file=discord.File(str(path), filename=path.name),
            )
        except Exception as e:
            logger.warning(f"Failed to send media {item['path']}: {e}")


# --------------------------------------------------------------------------- #
# Discord client
# --------------------------------------------------------------------------- #

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    logger.info(f"Logged in as {client.user} (id: {client.user.id})")
    logger.info(f"Authorised channels: {[ch['name'] for ch in CHANNELS]} ({len(CHANNELS)})")
    logger.info(f"LLM model: {CLAWBIO_MODEL}")
    if LLM_BASE_URL:
        logger.info(f"LLM base URL: {LLM_BASE_URL}")
    print(f"RoboTerri Discord bot is running as {client.user}. Press Ctrl+C to stop.")


@client.event
async def on_message(message: discord.Message):
    # Ignore own messages
    if message.author == client.user:
        return

    # Only respond in authorised channels
    if message.channel.id not in AUTHORISED_CHANNEL_IDS:
        return

    # ----- Slash-style commands ----- #

    content = message.content.strip()

    if content == "!reload":
        reload_channels()
        await message.channel.send(
            f"Reloaded channel config -- {len(CHANNELS)} channel(s) authorised."
        )
        logger.info(f"Reloaded .channels.json: {AUTHORISED_CHANNEL_IDS}")
        return

    if content == "!start":
        await message.channel.send(
            "Hi there! RoboTerri here -- your ClawBio bioinformatics assistant ;-)\n\n"
            "Commands:\n"
            "  `!skills`  -- list available ClawBio skills\n"
            "  `!demo <skill>`  -- run a demo (pharmgx, equity, nutrigx, compare)\n\n"
            "Or just chat -- I can answer bioinformatics questions.\n"
            "Attach a genetic data file (.txt, .csv, .vcf) to analyse it.\n"
            "Attach a photo of a medication for personalised drug guidance."
        )
        return

    if content == "!skills":
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(CLAWBIO_PY), "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(CLAWBIO_DIR),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            output = stdout.decode(errors="replace").strip()
            await send_long_message(message.channel, output or "No skills found.")
        except Exception as e:
            await message.channel.send(f"Error listing skills: {e}")
        return

    if content.startswith("!demo"):
        parts = content.split(maxsplit=1)
        skill = parts[1].strip() if len(parts) > 1 else "pharmgx"
        await message.channel.send(f"Running {skill} demo -- this may take a moment...")
        async with message.channel.typing():
            try:
                reply = await llm_tool_loop(
                    message.channel.id,
                    f"Run the {skill} demo using the clawbio tool with mode='demo'.",
                )
                if _pending_text:
                    reply = "\n\n".join(_pending_text)
                    _pending_text.clear()
                await send_long_message(message.channel, reply)
                await drain_pending_media(message.channel)
            except Exception as e:
                logger.error(f"Demo error: {e}", exc_info=True)
                await message.channel.send(f"Demo failed: {e}")
        return

    # ----- Attachments: images and genetic data files ----- #

    has_image = False
    has_genetic_file = False

    for attachment in message.attachments:
        ext = Path(attachment.filename).suffix.lower()
        content_type = attachment.content_type or ""

        if content_type.startswith("image/") or ext in IMAGE_EXTENSIONS:
            has_image = True
            # Download image and encode to base64
            img_bytes = await attachment.read()
            img_b64 = base64.standard_b64encode(img_bytes).decode("ascii")

            media_type = content_type if content_type.startswith("image/") else "image/jpeg"
            logger.info(f"Image received: {attachment.filename} ({len(img_bytes)} bytes, {media_type})")

            # Store for potential file-based skill use
            tmp_path = Path(tempfile.gettempdir()) / f"roboterri_{attachment.filename}"
            tmp_path.write_bytes(img_bytes)
            _received_files[message.channel.id] = {
                "path": str(tmp_path), "filename": attachment.filename,
            }

            caption = message.content.strip() if message.content else ""
            content_blocks = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": img_b64,
                    },
                },
            ]
            if caption:
                content_blocks.append({"type": "text", "text": caption})
            else:
                content_blocks.append({
                    "type": "text",
                    "text": (
                        "[Image sent without caption. Look at this image. "
                        "If it shows a medication, drug packaging, pill bottle, blister pack, or "
                        "any pharmaceutical product: immediately identify the drug name and any "
                        "visible dosage, then call the clawbio tool with skill='drugphoto', "
                        "mode='demo', drug_name=<identified drug>, and visible_dose=<dose if readable>. "
                        "Do NOT ask what is needed -- just run the lookup automatically. "
                        "If the image is not a medication, describe what you see and ask if "
                        "anything specific is needed.]"
                    ),
                })

            async with message.channel.typing():
                try:
                    reply = await llm_tool_loop(message.channel.id, content_blocks)
                    if _pending_text:
                        reply = "\n\n".join(_pending_text)
                        _pending_text.clear()
                    await send_long_message(message.channel, reply)
                except Exception as e:
                    logger.error(f"Photo handling error: {e}", exc_info=True)
                    await message.channel.send(
                        f"Sorry, I couldn't process that image -- {type(e).__name__}: {e}"
                    )

        elif ext in GENETIC_EXTENSIONS:
            has_genetic_file = True
            file_bytes = await attachment.read()
            tmp_path = Path(tempfile.gettempdir()) / f"roboterri_{attachment.filename}"
            tmp_path.write_bytes(file_bytes)
            logger.info(f"Document received: {attachment.filename} ({len(file_bytes)} bytes)")

            _received_files[message.channel.id] = {
                "path": str(tmp_path), "filename": attachment.filename,
            }

            caption = message.content.strip() if message.content else ""
            parts = [f"[Document received: {attachment.filename} ({len(file_bytes)} bytes)]"]
            if caption:
                parts.append(caption)
            else:
                parts.append(
                    "The user sent this genetic data file. Detect the file type and "
                    "run the appropriate ClawBio skill using mode='file'. For .txt "
                    "files (23andMe format) use pharmgx. For .csv (AncestryDNA) use "
                    "pharmgx. For .vcf use equity. For .fastq use metagenomics. "
                    "If unsure, use skill='auto'."
                )

            async with message.channel.typing():
                try:
                    reply = await llm_tool_loop(
                        message.channel.id, "\n\n".join(parts)
                    )
                    if _pending_text:
                        reply = "\n\n".join(_pending_text)
                        _pending_text.clear()
                    await send_long_message(message.channel, reply)
                    await drain_pending_media(message.channel)
                except Exception as e:
                    logger.error(f"Document handling error: {e}", exc_info=True)
                    await message.channel.send(
                        f"Sorry, I couldn't process that document -- {type(e).__name__}: {e}"
                    )

    # If there were only attachments (no text beyond them), we're done
    if has_image or has_genetic_file:
        return

    # ----- Plain text messages ----- #

    if not content:
        return

    # Ignore bot commands already handled above
    if content.startswith("!"):
        return

    user_text = content
    logger.info(f"Message from {message.author.display_name}: {user_text[:100]}")

    async with message.channel.typing():
        try:
            reply = await llm_tool_loop(message.channel.id, user_text)
            if _pending_text:
                reply = "\n\n".join(_pending_text)
                _pending_text.clear()
            await send_long_message(message.channel, reply)
            await drain_pending_media(message.channel)
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            await message.channel.send(
                f"Sorry, something went wrong -- {type(e).__name__}: {e}"
            )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main():
    """Start the bot."""
    logger.info(f"Starting RoboTerri Discord bot (model: {CLAWBIO_MODEL})")
    logger.info(f"ClawBio directory: {CLAWBIO_DIR}")
    if LLM_BASE_URL:
        logger.info(f"LLM base URL: {LLM_BASE_URL}")
    logger.info(f"Authorised channels: {[ch['name'] for ch in CHANNELS]} ({len(CHANNELS)})")

    client.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

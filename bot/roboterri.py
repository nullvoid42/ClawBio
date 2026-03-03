#!/usr/bin/env python3
"""
roboterri.py — RoboTerri ClawBio Telegram Bot
==============================================
A Telegram bot that runs ClawBio bioinformatics skills using Claude
as the reasoning engine. Handles text messages, genetic file uploads,
and medication photos.

Prerequisites:
    pip3 install python-telegram-bot[job-queue] anthropic python-dotenv

Usage:
    # Set environment variables in .env (see bot/README.md)
    python3 bot/roboterri.py
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

import anthropic
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAWBIO_MODEL = os.environ.get("CLAWBIO_MODEL", "claude-sonnet-4-5-20250929")

if not TELEGRAM_BOT_TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not set. See bot/README.md for setup.")
    sys.exit(1)
if not ANTHROPIC_API_KEY:
    print("Error: ANTHROPIC_API_KEY not set. See bot/README.md for setup.")
    sys.exit(1)
if not TELEGRAM_CHAT_ID:
    print("Error: TELEGRAM_CHAT_ID not set. See bot/README.md for setup.")
    sys.exit(1)

CLAWBIO_DIR = Path(__file__).resolve().parent.parent
CLAWBIO_PY = CLAWBIO_DIR / "clawbio.py"
SOUL_MD = CLAWBIO_DIR / "SOUL.md"
OUTPUT_DIR = CLAWBIO_DIR / "output"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("roboterri")

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

claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
conversations: dict[int, list] = {}
MAX_HISTORY = 20

# Per-chat received file storage
_received_files: dict[int, dict] = {}

# Pending media queue: chat_id -> list of {"type": "document"|"photo", "path": str}
_pending_media: dict[int, list[dict]] = {}

# Pending text queue: bypass LLM paraphrasing for compare/drugphoto
_pending_text: list[str] = []

BOT_START_TIME = time.time()

# --------------------------------------------------------------------------- #
# Tool definition (clawbio only)
# --------------------------------------------------------------------------- #

TOOLS = [
    {
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
        "input_schema": {
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
                        "file: use a file the user sent via Telegram. "
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
]

# --------------------------------------------------------------------------- #
# execute_clawbio
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
                    f"available via Telegram. Available: {avail}"
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
    output_files = sorted([f.name for f in out_dir.rglob("*") if f.is_file()]) if out_dir.exists() else []

    # Queue figures and reports for Telegram delivery
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
# Drain pending media
# --------------------------------------------------------------------------- #


async def _drain_pending_media(update: Update, context) -> None:
    """Send any queued ClawBio media (documents + figures) after the text reply."""
    items = _pending_media.pop(0, [])
    if not items:
        return
    chat_id = update.effective_chat.id
    for item in items:
        try:
            path = Path(item["path"])
            if not path.exists():
                continue
            if item["type"] == "document":
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=open(path, "rb"),
                    filename=path.name,
                )
            elif item["type"] == "photo":
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=open(path, "rb"),
                    caption=path.stem.replace("_", " ").title(),
                )
        except Exception as e:
            logger.warning(f"Failed to send media {item['path']}: {e}")


# --------------------------------------------------------------------------- #
# LLM tool loop
# --------------------------------------------------------------------------- #

TOOL_EXECUTORS = {
    "clawbio": execute_clawbio,
}

MAX_TOOL_ITERATIONS = 10


async def llm_tool_loop(chat_id: int, user_content: str | list) -> str:
    """
    Run the Claude tool-use loop:
    1. Append user message to history
    2. Call Claude with system_prompt + history + tools
    3. If tool_use blocks -> execute -> append results -> call again
    4. Return final text
    """
    history = conversations.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_content})

    if len(history) > MAX_HISTORY:
        history[:] = history[-MAX_HISTORY:]

    assistant_content = []
    for _iteration in range(MAX_TOOL_ITERATIONS):
        try:
            response = await claude.messages.create(
                model=CLAWBIO_MODEL,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=history,
                tools=TOOLS,
            )
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return f"Sorry, I'm having trouble thinking right now -- API error: {e}"

        assistant_content = response.content
        history.append({"role": "assistant", "content": assistant_content})

        tool_blocks = [b for b in assistant_content if b.type == "tool_use"]
        if not tool_blocks:
            text_parts = [b.text for b in assistant_content if b.type == "text"]
            return "\n".join(text_parts) if text_parts else "(no response)"

        tool_results = []
        for block in tool_blocks:
            executor = TOOL_EXECUTORS.get(block.name)
            if executor:
                logger.info(f"Tool call: {block.name}({json.dumps(block.input)[:200]})")
                try:
                    result = await executor(block.input)
                except Exception as tool_err:
                    logger.error(f"Tool {block.name} raised: {tool_err}", exc_info=True)
                    result = f"Error executing {block.name}: {type(tool_err).__name__}: {tool_err}"
            else:
                result = f"Unknown tool: {block.name}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        history.append({"role": "user", "content": tool_results})

    text_parts = [b.text for b in assistant_content if b.type == "text"]
    return "\n".join(text_parts) if text_parts else "(max tool iterations reached)"


# --------------------------------------------------------------------------- #
# Telegram helpers
# --------------------------------------------------------------------------- #


def is_authorised(update: Update) -> bool:
    """Check if the message is from the authorised chat."""
    return update.effective_chat.id == TELEGRAM_CHAT_ID


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


async def send_long_message(update: Update, text: str):
    """Send a message, splitting at 4096 chars if needed. Strips markup."""
    text = strip_markup(text)
    MAX_LEN = 4096
    if len(text) <= MAX_LEN:
        await update.message.reply_text(text)
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
            await update.message.reply_text(chunk)


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not is_authorised(update):
        return
    await update.message.reply_text(
        "Hi there! RoboTerri here -- your ClawBio bioinformatics assistant ;-)\n\n"
        "Commands:\n"
        "  /skills  -- list available ClawBio skills\n"
        "  /demo <skill>  -- run a demo (pharmgx, equity, nutrigx, compare)\n\n"
        "Or just chat -- I can answer bioinformatics questions.\n"
        "Send a genetic data file (.txt, .csv, .vcf) to analyse it.\n"
        "Send a photo of a medication for personalised drug guidance."
    )


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /skills command -- list available ClawBio skills."""
    if not is_authorised(update):
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(CLAWBIO_PY), "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(CLAWBIO_DIR),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode(errors="replace").strip()
        await send_long_message(update, output or "No skills found.")
    except Exception as e:
        await update.message.reply_text(f"Error listing skills: {e}")


async def cmd_demo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /demo <skill> command -- run a skill with demo data."""
    if not is_authorised(update):
        return
    skill = context.args[0] if context.args else "pharmgx"
    await update.message.reply_text(f"Running {skill} demo -- this may take a moment...")
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )
    try:
        reply = await llm_tool_loop(
            update.effective_chat.id,
            f"Run the {skill} demo using the clawbio tool with mode='demo'."
        )
        if _pending_text:
            reply = "\n\n".join(_pending_text)
            _pending_text.clear()
        await send_long_message(update, reply)
        await _drain_pending_media(update, context)
    except Exception as e:
        logger.error(f"Demo error: {e}", exc_info=True)
        await update.message.reply_text(f"Demo failed: {e}")


# --------------------------------------------------------------------------- #
# Message handlers
# --------------------------------------------------------------------------- #


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text messages via the LLM tool loop."""
    if not is_authorised(update):
        return
    if not update.message or not update.message.text:
        return

    user_text = update.message.text
    logger.info(f"Message from {update.effective_user.first_name}: {user_text[:100]}")

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        reply = await llm_tool_loop(update.effective_chat.id, user_text)
        if _pending_text:
            reply = "\n\n".join(_pending_text)
            _pending_text.clear()
        await send_long_message(update, reply)
        await _drain_pending_media(update, context)
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        await update.message.reply_text(
            f"Sorry, something went wrong -- {type(e).__name__}: {e}"
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos: download -> base64 -> Claude vision (drug detection)."""
    if not is_authorised(update):
        return
    if not update.message:
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        photo = update.message.photo[-1] if update.message.photo else None
        doc = update.message.document if not photo else None

        if not photo and not doc:
            return

        if doc:
            mime = doc.mime_type or ""
            if not mime.startswith("image/"):
                return
            file = await doc.get_file()
            media_type = mime
            filename = doc.file_name or "image.jpg"
        else:
            file = await photo.get_file()
            media_type = "image/jpeg"
            filename = "photo.jpg"

        img_bytes = await file.download_as_bytearray()
        img_b64 = base64.standard_b64encode(bytes(img_bytes)).decode("ascii")
        logger.info(f"Photo received: {len(img_bytes)} bytes, type={media_type}")

        # Store for potential file-based skill use
        tmp_path = Path(tempfile.gettempdir()) / f"roboterri_{filename}"
        tmp_path.write_bytes(bytes(img_bytes))
        _received_files[update.effective_chat.id] = {
            "path": str(tmp_path), "filename": filename,
        }

        caption = update.message.caption or ""
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

        reply = await llm_tool_loop(update.effective_chat.id, content_blocks)
        if _pending_text:
            reply = "\n\n".join(_pending_text)
            _pending_text.clear()
        await send_long_message(update, reply)

    except Exception as e:
        logger.error(f"Photo handling error: {e}", exc_info=True)
        await update.message.reply_text(
            f"Sorry, I couldn't process that image -- {type(e).__name__}: {e}"
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle documents: download -> detect genetic file -> route to skill."""
    if not is_authorised(update):
        return
    if not update.message or not update.message.document:
        return

    doc = update.message.document
    mime = doc.mime_type or ""

    # Images handled by handle_photo
    if mime.startswith("image/"):
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        file = await doc.get_file()
        filename = doc.file_name or "document"
        file_size = doc.file_size or 0

        tmp_path = Path(tempfile.gettempdir()) / f"roboterri_{filename}"
        await file.download_to_drive(str(tmp_path))
        logger.info(f"Document received: {filename} ({file_size} bytes, {mime})")

        _received_files[update.effective_chat.id] = {
            "path": str(tmp_path), "filename": filename,
        }

        caption = update.message.caption or ""
        parts = [f"[Document received: {filename} ({mime}, {file_size} bytes)]"]
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

        reply = await llm_tool_loop(
            update.effective_chat.id, "\n\n".join(parts)
        )
        if _pending_text:
            reply = "\n\n".join(_pending_text)
            _pending_text.clear()
        await send_long_message(update, reply)
        await _drain_pending_media(update, context)

    except Exception as e:
        logger.error(f"Document handling error: {e}", exc_info=True)
        await update.message.reply_text(
            f"Sorry, I couldn't process that document -- {type(e).__name__}: {e}"
        )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main():
    """Start the bot."""
    logger.info(f"Starting RoboTerri ClawBio bot (model: {CLAWBIO_MODEL})")
    logger.info(f"ClawBio directory: {CLAWBIO_DIR}")
    logger.info(f"Authorised chat ID: {TELEGRAM_CHAT_ID}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("skills", cmd_skills))
    app.add_handler(CommandHandler("demo", cmd_demo))

    # Message handlers
    app.add_handler(MessageHandler(
        filters.PHOTO | (filters.Document.IMAGE & ~filters.COMMAND),
        handle_photo,
    ))
    app.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.Document.IMAGE & ~filters.COMMAND,
        handle_document,
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message,
    ))

    print("RoboTerri ClawBio bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()

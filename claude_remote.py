#!/usr/bin/env python3
"""
claude-remote: Control Claude on your Linux desktop via Telegram.

Messages you send from your phone become Claude tasks executed locally.
Claude runs in a full agentic loop with bash + file tools, chaining as many
calls as it needs, then reports the outcome back over Telegram.

No port forwarding, no VPN — Telegram handles the transport.
"""

import os
import asyncio
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
import anthropic
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
AUTHORIZED_USERS  = {int(x) for x in os.getenv("AUTHORIZED_USER_IDS", "").split(",") if x.strip()}
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
WORKING_DIR       = os.getenv("WORKING_DIR", str(Path.home()))
LOG_FILE          = os.getenv("LOG_FILE", str(Path.home() / ".claude-remote" / "daemon.log"))

MODEL             = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_BASH_OUTPUT   = 8000    # chars — truncate long shell output
TELEGRAM_MAX_MSG  = 4000    # chars — Telegram hard limit is 4096; stay under
MAX_AGENT_TURNS   = 25      # safety cap on the tool-use loop per request

# ── Logging ────────────────────────────────────────────────────────────────────
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("claude-remote")

# ── Anthropic client ────────────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Tool definitions given to Claude ───────────────────────────────────────────
TOOLS = [
    {
        "name": "bash",
        "description": (
            "Run a bash command on the user's Linux desktop. "
            "Returns combined stdout+stderr. Use for inspecting the system, "
            "running scripts, managing services, installing packages, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to run."},
                "timeout": {"type": "integer", "description": "Seconds before kill (1-300). Default 30."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "write_file",
        "description": "Write (or append to) a file on the user's desktop. Creates parent dirs as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "append": {"type": "boolean", "description": "Append instead of overwrite. Default false."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file from the user's desktop and return its contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


def build_system_prompt() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""You are claude-remote, running as a daemon on the user's Linux desktop.
You have been given bash access and file tools. The user is sending commands remotely
from their phone or laptop.

Your job:
- Execute tasks fully and autonomously. Don't ask clarifying questions unless truly blocked.
- Report back clearly: what you did, what succeeded, what failed.
- For long-running tasks, do them and summarize the outcome.
- Working directory: {WORKING_DIR}
- Current time: {timestamp}

Be concise in your replies — these are Telegram messages. Lead with outcome, then detail.
Use plain text. Avoid markdown since Telegram formatting is limited in most contexts."""


# ── Tool execution ──────────────────────────────────────────────────────────────
def run_bash(command: str, timeout: int = 30) -> str:
    timeout = min(max(timeout, 1), 300)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=WORKING_DIR,
            env={**os.environ, "HOME": str(Path.home())},
        )
        out = result.stdout + result.stderr
        if len(out) > MAX_BASH_OUTPUT:
            out = out[:MAX_BASH_OUTPUT] + f"\n... [truncated, {len(out)} total chars]"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT after {timeout}s]"
    except Exception as e:
        return f"[ERROR] {e}"


def run_write_file(path: str, content: str, append: bool = False) -> str:
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with p.open("a") as f:
                f.write(content)
        else:
            p.write_text(content)
        return f"Written {len(content)} chars to {p}"
    except Exception as e:
        return f"[ERROR] {e}"


def run_read_file(path: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        content = p.read_text()
        if len(content) > MAX_BASH_OUTPUT:
            content = content[:MAX_BASH_OUTPUT] + "\n... [truncated]"
        return content
    except Exception as e:
        return f"[ERROR] {e}"


def dispatch_tool(name: str, inputs: dict) -> str:
    if name == "bash":
        return run_bash(inputs["command"], inputs.get("timeout", 30))
    if name == "write_file":
        return run_write_file(inputs["path"], inputs["content"], inputs.get("append", False))
    if name == "read_file":
        return run_read_file(inputs["path"])
    return f"[Unknown tool: {name}]"


# ── Agentic loop ────────────────────────────────────────────────────────────────
async def run_agent(user_message: str, status_callback) -> str:
    """Run Claude with tool use until it produces a final text response."""
    messages = [{"role": "user", "content": user_message}]
    system_prompt = build_system_prompt()
    collected_text: list[str] = []

    for turn in range(MAX_AGENT_TURNS):
        resp = await asyncio.to_thread(
            client.messages.create,
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        # Stash the assistant turn verbatim so tool_use ids line up.
        messages.append({"role": "assistant", "content": resp.content})

        for block in resp.content:
            if block.type == "text" and block.text.strip():
                collected_text.append(block.text.strip())

        if resp.stop_reason != "tool_use":
            break

        # Execute every tool call in this turn, feed results back.
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            label = block.input.get("command") or block.input.get("path") or block.name
            await status_callback(f"⚙ {block.name}: {str(label)[:120]}")
            log.info("tool=%s input=%s", block.name, str(block.input)[:300])
            result = await asyncio.to_thread(dispatch_tool, block.name, block.input)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": result}
            )

        messages.append({"role": "user", "content": tool_results})
    else:
        collected_text.append(f"[Stopped after {MAX_AGENT_TURNS} tool turns.]")

    return "\n\n".join(collected_text).strip() or "(done — no text output)"


# ── Telegram plumbing ────────────────────────────────────────────────────────────
def chunk_text(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i : i + size]


def authorized(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in AUTHORIZED_USERS


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        await update.message.reply_text("Unauthorized.")
        log.warning("Unauthorized /start from %s", update.effective_user.id if update.effective_user else "?")
        return
    await update.message.reply_text(
        "claude-remote is online. Send me a plain-English task and I'll run it on your desktop.\n\n"
        "Examples:\n"
        "  • is my streamlit annotator still running? restart it if not\n"
        "  • check disk space and tell me what's eating it\n"
        "  • clone <repo> into ~/projects and set it up"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        await update.message.reply_text("Unauthorized.")
        log.warning("Unauthorized message from %s", update.effective_user.id if update.effective_user else "?")
        return

    user_message = update.message.text
    log.info("[%s] %s", update.effective_user.id, user_message)

    status_msg = await update.message.reply_text("⏳ Working...")

    async def status_callback(text: str) -> None:
        try:
            await status_msg.edit_text(text[:TELEGRAM_MAX_MSG])
        except Exception:
            pass  # editing is best-effort; ignore "message not modified" etc.

    try:
        result = await run_agent(user_message, status_callback)
    except Exception as e:
        log.exception("agent error")
        await status_msg.edit_text(f"[ERROR] {e}")
        return

    try:
        await status_msg.delete()
    except Exception:
        pass

    for chunk in chunk_text(result, TELEGRAM_MAX_MSG):
        await update.message.reply_text(chunk)


# ── Entry point ──────────────────────────────────────────────────────────────────
def main() -> None:
    missing = [
        name
        for name, val in [
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_TOKEN),
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ]
        if not val
    ]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        raise SystemExit(1)
    if not AUTHORIZED_USERS:
        log.error("AUTHORIZED_USER_IDS is empty — refusing to start an open relay.")
        raise SystemExit(1)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("claude-remote online | model=%s | workdir=%s | authorized=%s",
             MODEL, WORKING_DIR, sorted(AUTHORIZED_USERS))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

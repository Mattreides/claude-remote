# claude-remote

Control Claude on your Linux desktop from anywhere — phone, laptop, wherever.
Send a plain-English task over Telegram; Claude runs it on your machine in a full
agentic loop and reports back.

No port forwarding. No VPN. No inbound ports at all — Telegram handles the transport.

```
 Your phone (Telegram)
        │  "is my streamlit annotator running? restart it if not"
        ▼
 Telegram servers
        │  long-poll
        ▼
 claude-remote daemon ───────► Anthropic API (Claude, tool use)
 (systemd service on              │
  your Linux desktop)             │  bash · write_file · read_file
        ▲                         ▼
        └──── plain-text reply ◄── executes locally, loops until done
```

## Why it's built this way

A remote shell normally means opening a port, configuring a VPN, or trusting a
third-party relay. This avoids all of that: the daemon makes only **outbound**
long-poll requests to Telegram, so there's nothing to expose to the internet.
Telegram is the message bus; Claude is the executor; your desktop is the runtime.

Claude runs in a real agentic loop — it can chain multiple `bash` calls, write
and read files, inspect the results, and keep going until the task is finished,
then summarizes the outcome in a Telegram-friendly plain-text reply.

## Features

- **Agentic tool-use loop** — `bash`, `write_file`, `read_file`, capped at 25 turns per request so it can't run away.
- **Outbound-only** — no open ports, no port forwarding, no VPN.
- **Authorization gate** — only Telegram user IDs in `AUTHORIZED_USER_IDS` can trigger anything; everyone else gets `Unauthorized`.
- **Live status** — edits a single "Working…" message with the current tool call instead of spamming the chat.
- **Output handling** — shell output is truncated at 8 KB, replies are auto-chunked under Telegram's 4 KB message limit.
- **Runs as a systemd service** — `Restart=on-failure`, full logging to `~/.claude-remote/daemon.log`.

## Setup

1. Message **@BotFather** on Telegram → `/newbot` → copy the `TELEGRAM_BOT_TOKEN`.
2. Message **@userinfobot** → copy your numeric user ID.
3. Clone this repo into `~/claude-remote/`, then:

   ```bash
   ./setup.sh
   ```

   This creates a venv, installs dependencies, scaffolds `.env`, and installs the
   systemd unit.

4. Fill in `.env` (`TELEGRAM_BOT_TOKEN`, `AUTHORIZED_USER_IDS`, `ANTHROPIC_API_KEY`).
5. Start it:

   ```bash
   sudo systemctl start claude-remote@$USER
   journalctl -u claude-remote@$USER -f
   ```

6. Message your bot `/start`. You're live.

## What you can send

```
run ~/scripts/backup.sh
is my streamlit annotator still running? restart it if not
clone <repo> into ~/projects and set it up
check disk space and tell me what's eating it
write a script to batch-rename files in ~/data then run it
```

## Configuration

| Variable              | Required | Purpose                                              |
| --------------------- | -------- | ---------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`  | yes      | Bot token from @BotFather                            |
| `AUTHORIZED_USER_IDS` | yes      | Comma-separated Telegram user IDs allowed to command |
| `ANTHROPIC_API_KEY`   | yes      | Anthropic API key                                    |
| `WORKING_DIR`         | no       | Default working directory (defaults to `$HOME`)      |
| `ANTHROPIC_MODEL`     | no       | Override the model (default `claude-sonnet-4-6`)     |
| `LOG_FILE`            | no       | Log path (default `~/.claude-remote/daemon.log`)     |

## Security model — read before running

Commands execute with the permissions of the account running the daemon. Claude has
the same access on the box that the operator does. Two controls bound this:

- **`AUTHORIZED_USER_IDS`** is the only gate. If it's empty, the daemon refuses to
  start rather than run as an open relay. The bot token and this list must stay private.
- **The systemd unit** runs as the operator's user with `NoNewPrivileges=true` and
  `ProtectSystem=full`. It does not run as root.

Anyone who controls an authorized Telegram account, or the bot token, can run
commands as the operator. Treat both like SSH keys.

## Stack

Python 3.11+ · `anthropic` · `python-telegram-bot` (async) · `python-dotenv` · systemd

## License

MIT — see [LICENSE](LICENSE).

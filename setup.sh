#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USERNAME="$(whoami)"

echo "════════════════════════════════════════"
echo "  claude-remote setup"
echo "════════════════════════════════════════"

# 1. Python venv
echo ""
echo "▶ Creating Python virtual environment..."
python3 -m venv "$SCRIPT_DIR/venv"
"$SCRIPT_DIR/venv/bin/pip" install --quiet --upgrade pip
"$SCRIPT_DIR/venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "  ✓ Dependencies installed"

# 2. .env file
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo ""
    echo "▶ Created .env — you MUST fill this in before starting:"
    echo "    $SCRIPT_DIR/.env"
    echo ""
    echo "  Required values:"
    echo "    TELEGRAM_BOT_TOKEN   → from @BotFather on Telegram"
    echo "    AUTHORIZED_USER_IDS  → your Telegram user ID (from @userinfobot)"
    echo "    ANTHROPIC_API_KEY    → from console.anthropic.com"
else
    echo ""
    echo "▶ .env already exists — skipping"
fi

# 3. Log directory
mkdir -p "/home/$USERNAME/.claude-remote"
echo "  ✓ Log directory: /home/$USERNAME/.claude-remote/daemon.log"

# 4. Systemd service
echo ""
echo "▶ Installing systemd service..."
sudo cp "$SCRIPT_DIR/claude-remote@.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "claude-remote@$USERNAME.service"
echo "  ✓ Service installed as: claude-remote@$USERNAME.service"

echo ""
echo "════════════════════════════════════════"
echo "  Next steps:"
echo ""
echo "  1. Edit your .env:    nano $SCRIPT_DIR/.env"
echo "  2. Start the daemon:  sudo systemctl start claude-remote@$USERNAME"
echo "  3. Check it's up:     systemctl status claude-remote@$USERNAME"
echo "                        journalctl -u claude-remote@$USERNAME -f"
echo "  4. Message your bot /start — it's live"
echo "════════════════════════════════════════"

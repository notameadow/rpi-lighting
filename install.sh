#!/usr/bin/env bash
# RPi Lighting — installer.
set -euo pipefail

INSTALL_DIR="/home/dev/lighting"
UNIT_DIR="/etc/systemd/system"

echo "=== RPi Lighting Install ==="
echo ""

# ── 1. Python venv + requirements ────────────────────────────────────────────
echo "→ Python venv..."
cd "$INSTALL_DIR"
if [ ! -d venv ]; then
    python3 -m venv venv
fi
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt
mkdir -p data/luminair
echo "  OK"

# ── 2. lighting-app systemd service ─────────────────────────────────────────
echo "→ lighting-app service..."
sudo cp "$INSTALL_DIR/systemd/lighting-app.service" "$UNIT_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable lighting-app
sudo systemctl restart lighting-app
sleep 3
if systemctl is-active --quiet lighting-app; then
    echo "  OK (running on port 5001)"
else
    echo "  ERROR: lighting-app failed to start — last logs:"
    journalctl -u lighting-app -n 20 --no-pager
    exit 1
fi

echo ""
echo "=== Install complete ==="
echo ""
IP=$(hostname -I | awk '{print $1}')
echo "  Controller : http://${IP}:5001/"
echo "  Username   : admin  (default)"
echo "  Password   : lighting  (default — change this!)"
echo ""
echo "  Set credentials via systemd drop-in:"
echo "    sudo systemctl edit lighting-app"
echo "  Then add:"
echo "    [Service]"
echo "    Environment=LIGHTING_USER=admin"
echo "    Environment=LIGHTING_PASS=yourpassword"
echo ""

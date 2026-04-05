#!/usr/bin/env bash
# Deploy lighting controller to Pi via rsync.
set -euo pipefail

if [ -z "${RPi_HOST:-}" ]; then
    echo "Error: RPi_HOST is not set."
    echo "Usage: RPi_HOST=dev@<pi-address> ./toolchain/deploy.sh"
    exit 1
fi

RPi_SSH_KEY="${RPi_SSH_KEY:-$HOME/.ssh/id_ed25519}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_DIR="/home/dev/lighting"

echo "→ Deploying to $RPi_HOST:$REMOTE_DIR"

rsync -av --delete \
  --exclude='.git/' \
  --exclude='data/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='venv/' \
  --exclude='*.icloud' \
  --exclude='memory/' \
  --exclude='.claude/' \
  -e "ssh -i $RPi_SSH_KEY" \
  "$PROJECT_DIR/" \
  "$RPi_HOST:$REMOTE_DIR/"

# Ensure scripts are executable (rsync from macOS may lose +x)
ssh -i "$RPi_SSH_KEY" "$RPi_HOST" "chmod +x $REMOTE_DIR/toolchain/*.sh $REMOTE_DIR/install.sh 2>/dev/null" || true

echo "→ Done."

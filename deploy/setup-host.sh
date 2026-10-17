#!/usr/bin/env bash
# ==============================================================================
# PakDir Host Setup Automation
# Optimized for Linux nodes (including minimal 2GB/4GB cloud VPS instances)
# ==============================================================================
set -euo pipefail

echo "🚀 [1/6] Evaluating memory profile & swap configuration..."
TOTAL_MEM_MB=$(free -m | awk '/^Mem:/{print $2}')

# Automatically provision 4GB swap if total RAM is under 4GB
if [ "$TOTAL_MEM_MB" -lt 4000 ] && [ ! -f /swapfile ]; then
    echo "⚡ Low-memory host detected (${TOTAL_MEM_MB}MB). Creating 4GB Swapfile & tuning sysctl..."
    sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

    sudo sysctl vm.swappiness=10
    sudo sysctl vm.vfs_cache_pressure=50
    grep -q "vm.swappiness" /etc/sysctl.conf || echo "vm.swappiness=10" | sudo tee -a /etc/sysctl.conf
    grep -q "vm.vfs_cache_pressure" /etc/sysctl.conf || echo "vm.vfs_cache_pressure=50" | sudo tee -a /etc/sysctl.conf
fi

echo "📦 [2/6] Installing system dependencies (git, sqlite3, python3-venv, zram, caddy)..."
sudo apt-get update -y
sudo apt-get install -y git curl unzip sqlite3 python3 python3-venv python3-pip zram-tools caddy

echo "⚙️ [3/6] Installing OpenCode CLI..."
if ! command -v opencode >/dev/null 2>&1; then
    curl -fsSL https://opencode.ai/install | bash || true
fi

echo "🐍 [4/6] Setting up Python virtual environment..."
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

# Prevent Git 'dubious ownership' issues across user/root boundaries
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
git config --global --add safe.directory '*' 2>/dev/null || true
sudo git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
sudo git config --global --add safe.directory '*' 2>/dev/null || true

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

# Generate initial dynamic org-chart
python3 -m orchestrator.agent_factory list >/dev/null

if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️ Generated .env from .env.example. Remember to populate your credentials!"
fi

echo "🌐 [5/6] Configuring Caddy for Architecture Portal (Port 8080)..."
sudo sed "s|/var/www/ai-company|$APP_DIR|g" deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl restart caddy
sudo systemctl enable caddy

echo "🛡️ [6/6] Registering PakDir Supervisor Service (MemoryMax=250M)..."
SERVICE_FILE="deploy/systemd/pakdir-orchestrator.service"
sudo sed "s|/var/www/ai-company|$APP_DIR|g" "$SERVICE_FILE" | sudo tee /etc/systemd/system/pakdir-orchestrator.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable pakdir-orchestrator.service

echo "=============================================================================="
echo "✅ PAKDIR SETUP COMPLETE!"
echo "1. Configure environment: nano $APP_DIR/.env"
echo "2. Start supervisor daemon: sudo systemctl start pakdir-orchestrator"
echo "3. Access Live Architecture Portal: http://<NODE_IP>:8080"
echo "=============================================================================="

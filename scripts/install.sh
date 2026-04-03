#!/usr/bin/env bash
# TruAni — Application Installer
# Runs inside a Debian/Ubuntu system (LXC container, VM, or bare metal).
# Installs TruAni with all dependencies and configures it as a systemd service.
#
# Standalone usage:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Rozzly/TruAni/main/scripts/install.sh)"

set -euo pipefail

GITHUB_REPO="Rozzly/TruAni"
APP_DIR="/opt/truani"
DATA_DIR="/opt/truani/data"
VENV_DIR="/opt/truani/.venv"
APP_USER="truani"
APP_PORT=5656

msg_info() { echo "[*] $1"; }
msg_ok()   { echo "[+] $1"; }
msg_err()  { echo "[-] $1" >&2; }

# --- Preflight ---
if [[ $(id -u) -ne 0 ]]; then
    msg_err "This script must be run as root."
    exit 1
fi

# --- Update OS ---
msg_info "Updating system packages"
apt-get update -qq
apt-get upgrade -y -qq
msg_ok "System updated"

# --- Install dependencies ---
msg_info "Installing dependencies"
apt-get install -y -qq python3 python3-pip python3-venv git curl >/dev/null 2>&1
msg_ok "Dependencies installed"

# --- Create app user ---
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$APP_DIR" "$APP_USER"
    msg_ok "Created user: $APP_USER"
fi

# --- Clone repository ---
if [[ -d "$APP_DIR/.git" ]]; then
    msg_info "Updating existing installation"
    cd "$APP_DIR"
    git pull origin main --quiet
    msg_ok "Repository updated"
else
    msg_info "Cloning TruAni"
    git clone --quiet "https://github.com/${GITHUB_REPO}.git" "$APP_DIR"
    msg_ok "Repository cloned"
fi

# --- Create virtual environment ---
msg_info "Setting up Python environment"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
msg_ok "Python environment ready"

# --- Data directory ---
mkdir -p "$DATA_DIR"
chown -R "$APP_USER":"$APP_USER" "$DATA_DIR"

# --- Environment file ---
if [[ ! -f "$APP_DIR/.env" ]]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    msg_ok "Created .env from template"
fi

# --- Ownership ---
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# --- Systemd service ---
msg_info "Creating systemd service"
cat > /etc/systemd/system/truani.service <<EOF
[Unit]
Description=TruAni - Seasonal Anime Manager for Sonarr
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${VENV_DIR}/bin/python app.py
Restart=on-failure
RestartSec=5
Environment=DB_PATH=${DATA_DIR}/truani.db

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --quiet truani
systemctl restart truani
msg_ok "Service created and started"

# --- Update command ---
cat > /usr/bin/update <<'UPDATEEOF'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="/opt/truani"
VENV_DIR="/opt/truani/.venv"

echo "[*] Updating TruAni..."
cd "$APP_DIR"
git pull origin main --quiet
"$VENV_DIR/bin/pip" install --quiet -r requirements.txt
systemctl restart truani
echo "[+] TruAni updated to v$(cat VERSION)"
UPDATEEOF
chmod +x /usr/bin/update
msg_ok "Update command installed (/usr/bin/update)"

# --- Verify ---
sleep 2
if systemctl is-active --quiet truani; then
    msg_ok "TruAni is running on port $APP_PORT"
else
    msg_err "Service failed to start. Check: journalctl -u truani"
    exit 1
fi

msg_ok "Installation complete"

#!/usr/bin/env bash

set -euo pipefail

CLIENT_IP="${CLIENT_IP:-}"
EXPORT_DIR="${EXPORT_DIR:-/root/.jiuwenclaw/agent/jiuwenclaw_workspace}"
MOUNT_POINT="${MOUNT_POINT:-/root/.jiuwenclaw/agent/jiuwenclaw_workspace}"
EXPORTS_FILE="/etc/exports.d/jiuwenclaw.exports"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --client-ip)
      CLIENT_IP="$2"
      shift 2
      ;;
    --export-dir)
      EXPORT_DIR="$2"
      shift 2
      ;;
    --mount-point)
      MOUNT_POINT="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage:
  sudo bash scripts/nfs/setup_nfs_server.sh [options]

Options:
  --client-ip <ip>       Allowed NFS client IP. Required unless CLIENT_IP is set
  --export-dir <path>    Server export directory. Default: /root/.jiuwenclaw/agent/jiuwenclaw_workspace
  --mount-point <path>   Local mount path. Default: /root/.jiuwenclaw/agent/jiuwenclaw_workspace
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo bash $0" >&2
  exit 1
fi

if [[ -z "${CLIENT_IP}" ]]; then
  echo "CLIENT_IP is required. Pass --client-ip <private-ip> or export CLIENT_IP first." >&2
  exit 1
fi

install_nfs_server() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y nfs-kernel-server
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y nfs-utils
  elif command -v yum >/dev/null 2>&1; then
    yum install -y nfs-utils
  else
    echo "Unsupported package manager. Install NFS server packages manually." >&2
    exit 1
  fi
}

enable_nfs_service() {
  if systemctl list-unit-files | grep -q '^nfs-kernel-server\.service'; then
    systemctl enable --now nfs-kernel-server
  else
    systemctl enable --now nfs-server
  fi
}

ensure_fstab_line() {
  local line="$1"
  local file="$2"
  if ! grep -Fqx "$line" "$file" 2>/dev/null; then
    printf '%s\n' "$line" >> "$file"
  fi
}

echo "[1/6] Installing NFS server packages"
install_nfs_server

echo "[2/6] Preparing shared directories"
mkdir -p "${EXPORT_DIR}"
chmod 755 "${EXPORT_DIR}"

echo "[3/6] Writing export rule to ${EXPORTS_FILE}"
mkdir -p /etc/exports.d
cat > "${EXPORTS_FILE}" <<EOF
${EXPORT_DIR} ${CLIENT_IP}(rw,sync,no_subtree_check,no_root_squash)
EOF

echo "[4/6] Reloading exports"
exportfs -rav

echo "[5/6] Enabling NFS service"
enable_nfs_service

echo "[6/6] Creating local bind mount at ${MOUNT_POINT}"
mkdir -p "${MOUNT_POINT}"
if [[ "${EXPORT_DIR}" != "${MOUNT_POINT}" ]]; then
  mountpoint -q "${MOUNT_POINT}" || mount --bind "${EXPORT_DIR}" "${MOUNT_POINT}"
  ensure_fstab_line "${EXPORT_DIR} ${MOUNT_POINT} none bind 0 0" /etc/fstab
fi

cat <<EOF

NFS server is ready.

Server node:
  export dir : ${EXPORT_DIR}
  mount path : ${MOUNT_POINT}
  client ip  : ${CLIENT_IP}

Next step on the client node:
  sudo bash scripts/nfs/setup_nfs_client.sh --server-ip <server-private-ip>

If a firewall is enabled on this server, open the required NFS ports manually.
EOF

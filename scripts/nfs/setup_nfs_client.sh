#!/usr/bin/env bash

set -euo pipefail

SERVER_IP="${SERVER_IP:-}"
EXPORT_DIR="${EXPORT_DIR:-/root/.jiuwenclaw/.agent_teams}"
MOUNT_POINT="${MOUNT_POINT:-/root/.jiuwenclaw/.agent_teams}"
FSTAB_LINE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-ip)
      SERVER_IP="$2"
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
  sudo bash scripts/nfs/setup_nfs_client.sh [options]

Options:
  --server-ip <ip>       NFS server IP. Required unless SERVER_IP is set
  --export-dir <path>    Server export directory. Default: /root/.jiuwenclaw/.agent_teams
  --mount-point <path>   Local mount path. Default: /root/.jiuwenclaw/.agent_teams
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

if [[ -z "${SERVER_IP}" ]]; then
  echo "SERVER_IP is required. Pass --server-ip <private-ip> or export SERVER_IP first." >&2
  exit 1
fi

FSTAB_LINE="${SERVER_IP}:${EXPORT_DIR} ${MOUNT_POINT} nfs4 vers=4.1,_netdev,defaults 0 0"

install_nfs_client() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y nfs-common
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y nfs-utils
  elif command -v yum >/dev/null 2>&1; then
    yum install -y nfs-utils
  else
    echo "Unsupported package manager. Install NFS client packages manually." >&2
    exit 1
  fi
}

ensure_fstab_line() {
  local line="$1"
  local file="$2"
  if ! grep -Fqx "$line" "$file" 2>/dev/null; then
    printf '%s\n' "$line" >> "$file"
  fi
}

backup_existing_mount_point() {
  if mountpoint -q "${MOUNT_POINT}"; then
    return
  fi

  if [[ -d "${MOUNT_POINT}" ]] && [[ -n "$(find "${MOUNT_POINT}" -mindepth 1 -maxdepth 1 2>/dev/null)" ]]; then
    local backup_dir="${MOUNT_POINT}.pre_nfs_backup_$(date +%Y%m%d_%H%M%S)"
    echo "Backing up existing local workspace to ${backup_dir}"
    mv "${MOUNT_POINT}" "${backup_dir}"
    mkdir -p "${MOUNT_POINT}"
  fi
}

echo "[1/4] Installing NFS client packages"
install_nfs_client

echo "[2/4] Creating mount point ${MOUNT_POINT}"
mkdir -p "${MOUNT_POINT}"
backup_existing_mount_point

echo "[3/4] Mounting ${SERVER_IP}:${EXPORT_DIR}"
mountpoint -q "${MOUNT_POINT}" || mount -t nfs4 -o vers=4.1 "${SERVER_IP}:${EXPORT_DIR}" "${MOUNT_POINT}"

echo "[4/4] Persisting mount to /etc/fstab"
ensure_fstab_line "${FSTAB_LINE}" /etc/fstab

cat <<EOF

NFS client is ready.

Client node:
  server     : ${SERVER_IP}
  export dir : ${EXPORT_DIR}
  mount path : ${MOUNT_POINT}

Quick verification:
  touch ${MOUNT_POINT}/nfs_client_probe.txt
  ls -la ${MOUNT_POINT}
EOF

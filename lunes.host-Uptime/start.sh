#!/bin/bash
set -e

echo "=========================================="
echo "  🚀 Uptime Kuma 启动中 (Lunes.host)"
echo "=========================================="

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${APP_DIR}/data"
KUMA_DIR="${APP_DIR}/uptime-kuma"

[ -f "${APP_DIR}/config.sh" ] && source "${APP_DIR}/config.sh" && echo "✓ 配置已加载"

mkdir -p "$DATA_DIR"
export DATA_DIR

# =========================
# 下载预构建版本
# =========================
if [ ! -f "$KUMA_DIR/server/server.js" ]; then
    
    if [ -z "${KUMA_DOWNLOAD_URL:-}" ]; then
        echo "[ERROR] 请设置 KUMA_DOWNLOAD_URL"
        exit 1
    fi
    
    echo "[INFO] 下载 Uptime Kuma..."
    
    rm -rf "$KUMA_DIR"
    mkdir -p "$KUMA_DIR"
    
    curl -sL "$KUMA_DOWNLOAD_URL" | tar -xz --strip-components=1 -C "$KUMA_DIR"
    
    if [ ! -f "$KUMA_DIR/server/server.js" ]; then
        echo "[ERROR] 下载失败"
        exit 1
    fi
    
    echo "✓ 下载完成"
fi

# 检查
if [ ! -d "$KUMA_DIR/node_modules" ]; then
    echo "[ERROR] node_modules 不存在"
    exit 1
fi

# =========================
# 首次启动恢复备份
# =========================
if [ -n "${WEBDAV_URL:-}" ] && [ ! -f "$DATA_DIR/kuma.db" ]; then
    echo "[INFO] 首次启动，检查 WebDAV 备份..."
    bash "${APP_DIR}/scripts/restore.sh" || echo "[WARN] 恢复失败或无备份"
fi

# =========================
# 备份守护进程
# =========================
if [ -n "${WEBDAV_URL:-}" ]; then
    (
        while true; do
            sleep 3600
            
            current_date=$(date +"%Y-%m-%d")
            current_hour=$(date +"%H")
            
            # 检查是否需要备份
            LAST_BACKUP_FILE="/tmp/last_backup_date"
            last_backup_date=""
            [ -f "$LAST_BACKUP_FILE" ] && last_backup_date=$(cat "$LAST_BACKUP_FILE")
            
            if [ "$current_hour" -eq "${BACKUP_HOUR:-4}" ] && [ "$last_backup_date" != "$current_date" ]; then
                echo "[INFO] 执行每日备份..."
                bash "${APP_DIR}/scripts/backup.sh" && echo "$current_date" > "$LAST_BACKUP_FILE"
            fi
        done
    ) &
    
    echo "✓ 备份守护进程已启动 (每天 ${BACKUP_HOUR:-4}:00)"
fi

# =========================
# 启动
# =========================
echo "[INFO] 启动 Uptime Kuma..."

export UPTIME_KUMA_PORT="${PORT:-3001}"
export NODE_OPTIONS="--max-old-space-size=256"

echo "=========================================="
echo "  端口: $UPTIME_KUMA_PORT"
echo "  数据: $DATA_DIR"
echo "=========================================="

cd "$KUMA_DIR"
exec node server/server.js

#!/bin/bash
# ============================================
# Uptime Kuma 配置文件
# ============================================

export PORT="${PORT:-2114}"
export TZ="Asia/Shanghai"

# 预构建包下载地址
export KUMA_DOWNLOAD_URL="https://github.com/oyz8/action/releases/download/2.0.2/uptime-kuma-2.0.2.tar.gz"

# ============================================
# WebDAV 备份配置
# ============================================
export WEBDAV_URL="https://zeze.teracloud.jp/dav/backup/Uptime-Kuma/"
export WEBDAV_USER="用户名"
export WEBDAV_PASS="密码"

# 备份密码（可选，留空则不加密）
export BACKUP_PASS=""

# 每天备份时间（小时，0-23）
export BACKUP_HOUR=4

# 保留备份天数
export KEEP_DAYS=5

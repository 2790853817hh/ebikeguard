#!/usr/bin/env bash
# ============================================================
# 数据备份脚本：SQLite 在线热备 + 图片目录增量同步 + 保留策略
# 建议由 cron 每日执行（deploy.sh 已自动注册）
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ev_audit}"
BACKUP_DIR="${BACKUP_DIR:-${APP_DIR}/data/backup}"
DB_FILE="${DB_FILE:-${APP_DIR}/data/ev_audit.db}"
IMAGES_DIR="${IMAGES_DIR:-${APP_DIR}/data/images}"
KEEP_DAYS="${KEEP_DAYS:-30}"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "${BACKUP_DIR}/db" "${BACKUP_DIR}/images"

# 1) SQLite 在线热备（VACUUM INTO 是原子快照，无需停服）
if [ -f "${DB_FILE}" ]; then
  sqlite3 "${DB_FILE}" "VACUUM INTO '${BACKUP_DIR}/db/ev_audit_${STAMP}.db'"
  gzip -f "${BACKUP_DIR}/db/ev_audit_${STAMP}.db"
  echo "[ok] 数据库已备份：${BACKUP_DIR}/db/ev_audit_${STAMP}.db.gz"
else
  echo "[warn] 未找到数据库文件 ${DB_FILE}"
fi

# 2) 图片目录增量同步（硬链接快照，节省空间）
LATEST_LINK="${BACKUP_DIR}/images/latest"
rsync -a --delete --link-dest="${LATEST_LINK}" "${IMAGES_DIR}/" "${BACKUP_DIR}/images/${STAMP}/"
rm -f "${LATEST_LINK}"
ln -s "${BACKUP_DIR}/images/${STAMP}" "${LATEST_LINK}"
echo "[ok] 图片已同步：${BACKUP_DIR}/images/${STAMP}"

# 3) 保留策略
find "${BACKUP_DIR}/db" -name 'ev_audit_*.db.gz' -mtime "+${KEEP_DAYS}" -delete
find "${BACKUP_DIR}/images" -maxdepth 1 -type d -mtime "+${KEEP_DAYS}" ! -name images -exec rm -rf {} \;

# 4) （可选）上传到阿里云 OSS 异地保存
# ossutil64 cp -r "${BACKUP_DIR}/db" oss://your-backup-bucket/ev_audit/db/ --update

echo "[done] 备份完成于 ${STAMP}"

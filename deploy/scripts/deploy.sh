#!/usr/bin/env bash
# ============================================================
# 阿里云 Linux 部署脚本（CentOS / Alibaba Cloud Linux / Ubuntu 均可）
#
# 推荐流程（脚本会把代码 rsync 到 /opt/ev_audit）：
#   1) 在 Windows 上打包上传到服务器的 /tmp/ev_audit_src（见 docs 2.5）
#   2) sudo bash /tmp/ev_audit_src/deploy/scripts/deploy.sh
# 注意：REPO_SRC 取脚本上两级目录，所以不要在 /opt/ev_audit 里执行本脚本（会变成自我拷贝）。
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ev_audit}"
APP_USER="${APP_USER:-evaudit}"
REPO_SRC="${REPO_SRC:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY_BIN="${PY_BIN:-python3}"

echo "==> 部署目录：${APP_DIR}"
echo "==> 代码来源：${REPO_SRC}"

# 1) 系统依赖（Nginx / Python / 编译工具）
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y nginx python3 python3-venv python3-pip sqlite3 rsync
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y nginx python3 python3-pip sqlite rsync
else
  yum install -y nginx python3 python3-pip sqlite rsync
fi

# 2) 用户与目录
id -u "${APP_USER}" >/dev/null 2>&1 || useradd -r -m -s /sbin/nologin "${APP_USER}"
mkdir -p "${APP_DIR}"

# 3) 同步代码（排除本地运行数据、虚拟环境与服务器端配置）
#    .env 必须排除：既防止本机的开发配置（DEBUG=true / 本机路径）被推到服务器，
#    也保证重复部署时服务器上的密钥不会被 --delete 删掉而重新生成（否则 SECRET_KEY 变化会使已发 token 全部失效）。
rsync -a --delete \
  --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
  --exclude '.git' --exclude '.pytest_cache' --exclude '.env' \
  "${REPO_SRC}/" "${APP_DIR}/"

mkdir -p "${APP_DIR}/data/images" "${APP_DIR}/data/logs"
mkdir -p "${APP_DIR}/data/backup"

# 4) 虚拟环境与依赖
cd "${APP_DIR}"
[ -d .venv ] || "${PY_BIN}" -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

# 4.5) 可选依赖（阿里云短信 SDK / OSS SDK）
#      缺了它们，SMS_PROVIDER=aliyun_dypns 与 STORAGE_BACKEND=oss 会在运行时报「缺包」。
#      安装失败只告警、不中断部署（本地 Mock 全流程不依赖它们）。
if [ -f requirements-optional.txt ]; then
  ./.venv/bin/pip install -r requirements-optional.txt \
    || echo "==> 警告：可选依赖安装失败，短信 / OSS 通道将不可用；网络恢复后重跑本步骤即可"
fi

# 5) 环境变量
if [ ! -f "${APP_DIR}/.env" ]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  SECRET=$(head -c 48 /dev/urandom | base64 | tr -d '\n/+=' | head -c 48)
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" "${APP_DIR}/.env"
  sed -i "s|^DEBUG=.*|DEBUG=false|" "${APP_DIR}/.env"
  echo "==> 已生成 .env（请按需补充 OSS / 地图 / 短信密钥）"
fi
chmod 600 "${APP_DIR}/.env"

# 6) 初始化数据库与种子数据
./.venv/bin/python scripts/init_db.py

# 7) 权限
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
chmod -R u+rwX,g+rwX "${APP_DIR}/data"

# 8) Nginx 站点配置
#    顺序说明：本步与下一步（cron）都放在「启动服务」之前 —— 脚本是 set -e，
#    若先启动服务而它失败（例如曾经出现的 status=203/EXEC），后面的 Nginx reload 与
#    cron 都会被跳过，表现为「服务好了但 443 不监听、每日备份不存在」。
#    本步容错：证书未就位时 nginx -t 会失败，此时只告警、不中断整个部署。
cp "${APP_DIR}/deploy/nginx/ev_proxy_params.inc" /etc/nginx/conf.d/ev_proxy_params.inc
cp "${APP_DIR}/deploy/nginx/ev_audit.conf" /etc/nginx/conf.d/ev_audit.conf
if nginx -t; then
  systemctl reload nginx
else
  echo "==> 警告：Nginx 配置检查未通过（最常见原因：证书还没放到 /etc/nginx/ssl/）"
  echo "    配置已就位，处理完证书后执行：sudo nginx -t && sudo systemctl reload nginx"
fi

# 9) 定时任务：每日备份
CRON_FILE=/etc/cron.d/ev_audit_backup
echo "0 3 * * * ${APP_USER} bash ${APP_DIR}/deploy/scripts/backup.sh >> ${APP_DIR}/data/logs/backup.log 2>&1" > "${CRON_FILE}"
chmod 644 "${CRON_FILE}"

# 10) systemd 服务（放最后：它失败时脚本以非零退出，但上面 Nginx 与 cron 都已就位）
cp "${APP_DIR}/deploy/gunicorn/ev_audit.service" /etc/systemd/system/ev_audit.service
systemctl daemon-reload
systemctl enable ev_audit
# 必须 restart 而不是 enable --now：
#   --now 的 start 对「已在运行」的服务是 no-op（重复部署时正是这种情况），
#   而 Python 进程启动后不会因代码文件被 rsync 覆盖而加载新代码（gunicorn 未开 reload）——
#   表现就是「部署完成但跑的还是旧代码」。restart 对未运行的服务等价于 start，两种情况都覆盖。
systemctl restart ev_audit

echo "==> 部署完成"
echo "    服务状态： systemctl status ev_audit"
echo "    查看日志： journalctl -u ev_audit -f"
echo "    访问地址： https://<你的域名>/   （域名与证书路径已在 deploy/nginx/ev_audit.conf 中填好，换域名时改这三处）"
echo "    别忘了：   补 ${APP_DIR}/.env 里的短信 / 高德密钥，然后 systemctl restart ev_audit"

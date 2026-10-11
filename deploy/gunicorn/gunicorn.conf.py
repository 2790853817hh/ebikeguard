"""Gunicorn 配置（Uvicorn worker）。

启动：
    cd /opt/ev_audit
    .venv/bin/gunicorn -c deploy/gunicorn/gunicorn.conf.py app.main:app
"""
from __future__ import annotations

import multiprocessing
import os

# 监听地址：仅监听本机，由 Nginx 反向代理（不要直接暴露 0.0.0.0）
bind = os.getenv("GUNICORN_BIND", "127.0.0.1:8000")

# SQLite 单写者特性：worker 数不宜过多，建议 2~4
workers = int(os.getenv("GUNICORN_WORKERS", max(2, min(4, multiprocessing.cpu_count()))))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120
graceful_timeout = 30
keepalive = 5

# 日志
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")
access_log_format = '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sus'

# 进程名便于运维识别
proc_name = "ev_audit"

# 优雅重启（配合 systemd）
preload_app = False
max_requests = 2000
max_requests_jitter = 200

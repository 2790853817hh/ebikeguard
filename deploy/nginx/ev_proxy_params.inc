# Nginx 反向代理公共参数（被 ev_audit.conf include）
# 放置到 /etc/nginx/conf.d/ev_proxy_params.inc

proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Upgrade           $http_upgrade;
proxy_set_header Connection        "upgrade";

proxy_connect_timeout 10s;
proxy_send_timeout    120s;
proxy_read_timeout    120s;

proxy_buffering on;
proxy_buffers 8 32k;
proxy_busy_buffers_size 64k;

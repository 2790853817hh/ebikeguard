# 电动车不安全使用行为识别系统

> 第一期：**拍照上传平台 + AI/人工双重审核闭环**  
> 技术栈：FastAPI + SQLAlchemy 2.0 + SQLite(WAL) + 原生 HTML/CSS/JS（移动端优先）  
> 部署：阿里云 Linux（Nginx + Gunicorn/Uvicorn worker + systemd）

---

## 一、系统做什么

市民或巡查人员发现电动车不安全使用行为（进楼入户、飞线充电、楼道停放、堵塞消防通道、室内充电、电动车进电梯等）时，  
用手机拍照上传 → **AI 自动初审**（当前为规则占位引擎，可无缝替换为视觉大模型）→ **人工复核** →  
结果通过站内消息反馈给上报人 → 支持申诉复议。全过程留痕、可审计。

### 三条架构铁律

| 铁律                | 说明                                                                                       |
| ----------------- | ---------------------------------------------------------------------------------------- |
| 状态只能走状态机          | 一切 `report.status` 变更必须经 `AuditStateMachine.apply_transition()`，保证「规则校验 + 状态写入 + 留痕」原子完成 |
| 图片只走存储抽象          | 业务层只认 `storage_key`，本地磁盘 ↔ 阿里云 OSS 可零改代码切换                                               |
| AI 只走 Provider 协议 | 业务层只依赖 `VisionAuditProvider`，换模型不改调用方                                                    |

---

## 二、快速开始（本机）

> 以下命令为 **PowerShell** 写法（本机默认终端）。在项目根目录下执行：
> `cd "E:\cg\Documents\WorkBuddy\电动车识别"`

```powershell
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 初始化数据库 + 种子数据（行为类型字典、系统配置、管理端账号）
.\.venv\Scripts\python.exe scripts\init_db.py

# 3. 启动
.\.venv\Scripts\python.exe run.py
# 浏览器打开 http://127.0.0.1:8000/
```

> Linux / macOS 上把 `.\.venv\Scripts\python.exe` 换成 `./.venv/bin/python` 即可；
> 服务器侧的部署脚本（`deploy/scripts/*.sh`）仍是 bash。

### 管理端账号

| 角色  | 账号          | 密码                        | 说明                                                      |
| --- | ----------- | ------------------------- | ------------------------------------------------------- |
| 管理员 | `admin`     | 初始化时设定 / 随机生成并打印一次 | 统计后台、配置、申诉处理                                            |
| 审核员 | `reviewer1` | 初始化时设定 / 随机生成并打印一次 | 审核工作台                                                   |
| 审核员 | `reviewer2` | 初始化时设定 / 随机生成并打印一次 | 用于疑难二次复核演示                                              |
| 上报人 | 任意 11 位手机号  | 短信验证码                     | Mock 环境固定验证码 `888888`（`.env` 的 `SMS_MOCK_CODE`），登录即自动注册 |

> **密码不再写死在代码或登录页中**（2026-10-15 起）。三种设定方式：
> 1. 在 `.env` 中设置 `SEED_ADMIN_PASSWORD` / `SEED_REVIEWER1_PASSWORD` / `SEED_REVIEWER2_PASSWORD`，
>    再执行 `scripts/init_db.py`（仅对**新建**账号生效，已存在的账号不会被覆盖）；
> 2. 不配置则由初始化脚本随机生成强密码，**仅在首次运行的控制台打印一次**，请立即记录；
> 3. 随时修改既有账号：`.\.venv\Scripts\python.exe scripts\set_password.py --username admin --generate`
>    （服务器上为 `./.venv/bin/python scripts/set_password.py --username admin --generate`）。
>    改密后该账号此前签发的登录凭证立即失效，需重新登录。


> 手机端访问：本机启动后用手机访问 `http://<电脑局域网IP>:8000/`。  
> 浏览器定位（Geolocation）与手机拍照都需要 **HTTPS** 才能在真机上工作，内网演示可用自签证书或 Nginx 反代。

### 接口文档

- Swagger UI：`http://127.0.0.1:8000/docs`
- ReDoc：`http://127.0.0.1:8000/redoc`
- 平台能力自述：`GET /api/v1/meta`

### 接入外部服务（短信 / 域名证书 / 高德 / OSS）

系统带 Mock 兜底，**不配任何外部凭证也能跑通全流程**。要上生产时按需接入，操作步骤、控制台路径、
报错对照与安全守则见 **[docs/外部服务接入指南.md](docs/外部服务接入指南.md)**。

填完 `.env` 后用自检脚本一键确认（PowerShell，项目根目录下执行）：

```powershell
.\.venv\Scripts\python.exe scripts\check_external.py                    # 静态检查，零外部调用
.\.venv\Scripts\python.exe scripts\check_external.py --probe            # OSS 读写探针 + 高德一次配额
.\.venv\Scripts\python.exe scripts\check_external.py --probe --sms 手机号  # 真实发一条验证码短信
.\.venv\Scripts\python.exe scripts\check_external.py --domain 你的域名    # DNS 解析 + 证书剩余天数
```

外部服务的可选依赖单独放在 `requirements-optional.txt`（阿里云短信 SDK、oss2），按需安装。
安装前建议先清空代理变量（本机环境注入了 `HTTP_PROXY`，不清会连不上源）：

```powershell
$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:ALL_PROXY=""
.\.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
```

---

## 三、核心流程

```
用户拍照 → 前端带 EXIF/定位提交 (POST /api/v1/reports, client_uuid 幂等)
   → 落库 report(SUBMITTED) + report_image(sha256 去重) + 存储原图/缩略图
   → 状态机 START_AI → PENDING_AI
   → AuditOrchestrator 调用 VisionAuditProvider → 写 ai_audit_result
   → 按策略分流：
        · 保守档（默认）：所有“疑似违规”一律 → AI_DONE_PENDING_HUMAN（人工队列）
        · 仅“疑似正常”且置信度 ≥ 阈值且开关开启时 → 自动结案
   → 审核员工作台：认领（原子 UPDATE 防抢单）→ 看图 + 参考 AI 建议 →
        通过 / 不通过(必填原因) / 退回补充 / 疑难升级
   → 状态机校验 → 写 human_audit_record + audit_action_log → 通知上报人
   → 上报人可在「我的上报」查看结果、补充材料、发起申诉
```

### 审核状态机

| 状态                                     | 含义              |
| -------------------------------------- | --------------- |
| `SUBMITTED`                            | 已提交，待启动 AI      |
| `PENDING_AI`                           | AI 审核中          |
| `AI_DONE_PENDING_HUMAN`                | AI 已审，待人工       |
| `HUMAN_REVIEWING`                      | 人工审核中（已认领）      |
| `NEED_SUPPLEMENT`                      | 退回补充材料          |
| `APPROVED` / `REJECTED`                | 审核通过 / 不通过      |
| `ESCALATED`                            | 疑难升级（需他人二次复核）   |
| `APPEALING`                            | 申诉处理中           |
| `ARCHIVED` / `WITHDRAWN` / `CANCELLED` | 已归档 / 已撤回 / 已作废 |

完整流转表见 `app/services/state_machine.py::TRANSITIONS`（含每个动作的合法前置状态与角色约束）。

---

## 四、目录结构

```
app/
├─ main.py                  FastAPI 入口（中间件、异常、静态资源、启动补偿）
├─ config.py                配置（Pydantic Settings，读 .env）
├─ database.py              引擎/会话/Base（SQLite WAL + 外键 + busy_timeout）
├─ core/                    security(JWT/PBKDF2) / deps / exceptions / logging / utils / constants
├─ models/                  11 张 ORM 表
├─ schemas/                 Pydantic 契约（请求/响应）
├─ services/
│   ├─ state_machine.py     ★ 审核状态机（唯一合法流转入口）
│   ├─ audit_orchestrator.py★ AI 审核编排 + 保守档分流策略
│   ├─ report_service.py    上报提交/查询/撤回/补充/申诉
│   ├─ review_service.py    队列/认领/结论/二次复核/申诉复议
│   ├─ image_service.py     校验/EXIF/压缩/缩略图/哈希/签名
│   ├─ notification_service.py  站内信 + 多通道外发（邮件/Webhook/短信路由）
│   ├─ stats_service.py     统计聚合
│   └─ config_service.py    系统配置（带缓存）
├─ adapters/                ★ 可替换外部服务
│   ├─ ai/                  base(协议) / rule_based(占位) / qwen_vl / factory
│   ├─ storage/             base / local_disk / aliyun_oss(预留) / factory
│   ├─ sms/                 base / mock / aliyun_dypns / aliyun / factory
│   ├─ notify/              base / mock / email_smtp / webhook / wechat(预留) / factory
│   └─ geocoding/           base / stub / amap / factory
├─ api/v1/                  auth / reports / images / review / notifications / behavior_types / admin
├─ static/                  前端（index / upload / my-reports / report-detail / notifications / review-workbench / admin）
├─ seeds/                   行为类型字典、默认系统配置
├─ scripts/                 init_db / demo_data / check_external / enable_sms·enable_ai·enable_notify（一键启用+真实探测+失败回滚）
├─ docs/                    外部服务接入指南
├─ tests/                   180 个测试（状态机 / 上报 / 审核 / 通知通道 / 前端静态约束）
└─ deploy/                  nginx / gunicorn / systemd / deploy.sh / backup.sh / cron_tasks.sh
```

---

## 五、数据库表

| 表                     | 用途                                                     |
| --------------------- | ------------------------------------------------------ |
| `app_user`            | 用户（reporter / reviewer / admin），含接收通知的 `email`（选填）；`token_version` 用于登出吊销 |
| `behavior_type`       | 行为类型字典（含风险权重、夜间敏感标记）                                   |
| `report`              | 上报记录（状态、定位、结论、审派人、幂等键…）                                |
| `report_image`        | 图片（sha256 去重、pHash 相似检测、EXIF/GPS）                      |
| `ai_audit_result`     | AI 审核结果（结论、置信度、可解释理由、模型版本、分流状态）                        |
| `human_audit_record`  | 人工审核记录（结论、驳回原因、二次复核标记）                                 |
| `audit_action_log`    | 状态流转全留痕（谁、何时、从什么状态到什么状态）                               |
| `operation_audit_log` | 管理动作审计（改配置、改角色、导出…）                                    |
| `notification`        | 通知（站内信必达 + 邮件/Webhook/短信外部通道，多通道并行；发送状态如实记录） |
| `system_config`       | 系统配置（阈值、SLA、开关，后台可改）                                   |
| `appeal` / `sms_code` | 申诉 / 短信验证码                                             |

时间统一以 **UTC ISO8601** 存储，展示层转换为北京时间。

---

## 六、关键配置（`system_config`，后台可改）

| 键                                                | 默认              | 含义                               |
| ------------------------------------------------ | --------------- | -------------------------------- |
| `ai.auto_close_normal_enabled`                   | `false`         | 是否允许 AI 高置信“正常”自动结案（**保守档默认关闭**） |
| `ai.high_confidence_threshold`                   | `0.90`          | 高置信阈值                            |
| `ai.violation_threshold` / `ai.normal_threshold` | `0.62` / `0.34` | 规则引擎风险分阈值                        |
| `review.supplement_sla_hours`                    | `72`            | 退回补充材料的时限（超期自动不通过）               |
| `review.task_sla_hours`                          | `24`            | 人工审核时限（超期在队列中高亮）                 |
| `appeal.window_days`                             | `7`             | 终态后可申诉天数                         |
| `report.auto_archive_days`                       | `30`            | 终态后自动归档天数                        |
| `notify.enabled`                                 | `true`          | 外部通知总开关（关 = 仅站内信）                |
| `notify.email_enabled` / `notify.webhook_enabled` | `true` / `true` | 邮件 / 群机器人通道开关（配置就绪才实发）           |
| `notify.reporter_channels` / `notify.internal_channels` | `email` / `email,webhook` | 上报人 / 内部事件的外部通道名单（逗号分隔，可含 `sms`） |
| `notify.rate_limit_per_hour`                     | `20`            | 单用户每小时外部发送上限                     |

---

## 七、接入真实 AI 模型

本期 `RuleBasedProvider` 只依据结构化元数据（行为类型风险权重、图片数量与分辨率、EXIF 拍摄时间、  
定位一致性、备注关键词、历史相似上报）给出**可解释的初审建议**，且在理由中明确标注  
「本期为规则占位引擎，尚未进行图像内容识别」，不会掩盖能力边界。

接入视觉大模型只需三步（**上层零改动**）：

1. `pip install dashscope`（或其他厂商 SDK）；
2. 实现 `app/adapters/ai/qwen_vl.py::QwenVLProvider.audit()`，把模型输出映射为统一的 `AuditResult`；
3. `.env` 中设置 `AI_PROVIDER=qwenvl`、`DASHSCOPE_API_KEY=...`。

因为保守档下「疑似违规」一律走人工复核，即使模型误判也不会造成误裁决；同时 `ai_audit_result`  
保留 `model_name / model_version / raw_json`，便于 A/B 对比与追溯。

---

## 八、安全与合规

- **鉴权**：上报人手机号+短信验证码（Mock 可切阿里云短信）；审核员/管理员账号密码（PBKDF2-HMAC-SHA256）；  
  JWT 短时效 + 刷新令牌；`token_version` 实现即时吊销。
- **数据级权限**：上报人仅见自己的记录；审核员见队列；管理员全量。
- **图片访问**：*短时签名（默认 10 分钟）+ 登录态权限* 双重校验；不暴露磁盘路径；  
  非授权访问磁盘路径返回 403/404。本地存本地磁盘，生产可切 OSS 私有 Bucket + 签名 URL。
- **上传校验**：MIME 魔数校验（不信任扩展名）、大小限制、Pillow 重编码剥离潜在恶意载荷、  
  SHA-256 去重 + pHash 相似检测、`client_uuid` 幂等 + 频次限流。
- **隐私（PIPL）**：上传页告知「勿拍可识别人脸/车牌」；预留 `FacePlateDetector` 接口（P1 自动打码）；  
  手机号脱敏展示；保留期限可配（默认 5 年）；操作全审计。
- **传输与部署**：全站 HTTPS + HSTS、Nginx 限流（登录/短信/上传重点限流）、请求体上限、  
  非 root 运行、systemd 沙箱（`ProtectSystem` / `NoNewPrivileges`）。

---

## 九、运维（**以下命令在阿里云 Linux 服务器上执行**，仍是 bash）

```bash
# 生产部署（阿里云 Linux）
# 先在本机打包上传到服务器 /tmp/ev_audit_src，再在“副本目录”里执行：
#   mkdir -p /tmp/ev_audit_src && tar -xzf /tmp/ev_audit_src.tar.gz -C /tmp/ev_audit_src
#   sudo bash /tmp/ev_audit_src/deploy/scripts/deploy.sh
# 不要在 /opt/ev_audit 内执行本脚本（REPO_SRC 取脚本上两级目录，会变成自我拷贝）。
# 详细步骤见 docs/外部服务接入指南.md 第 2.5 节。
sudo bash /tmp/ev_audit_src/deploy/scripts/deploy.sh
systemctl status ev_audit
journalctl -u ev_audit -f

# 每日备份（deploy.sh 已自动注册 cron）
bash deploy/scripts/backup.sh

# 定时任务：超期未补充自动驳回 + 终态自动归档
bash deploy/scripts/cron_tasks.sh
```

**SQLite → PostgreSQL 迁移**：全程使用 SQLAlchemy ORM + Alembic，不写方言专有 SQL；  
切换只需把 `DATABASE_URL` 改为 `postgresql+psycopg://user:pwd@host/db`，并执行 `alembic upgrade head`。  
SQLite 单写者限制下建议 worker 数 2~4；写 QPS 持续升高时应尽快切换。

---

## 十、测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# 95 passed
```

覆盖：状态机全部合法/非法流转与角色约束、规则引擎结论与阈值、上报全链路（上传→AI 分流→列表/详情→撤回）、  
图片访问控制（签名/权限/伪造签名）、审核闭环（认领并发冲突、驳回必填原因、退回补充、疑难二次复核、  
申诉改判与维持）、统计与配置管理。

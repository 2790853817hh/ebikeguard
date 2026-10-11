"""FastAPI 应用入口。"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


async def _recover_stuck_ai_tasks() -> None:
    """服务重启后补偿：把卡在 PENDING_AI 的记录重新送入 AI 审核。"""
    await asyncio.sleep(5)
    try:
        from app.core.constants import ReportStatus
        from app.core.utils import parse_iso, utc_now
        from app.database import SessionLocal
        from app.models import Report
        from app.services import audit_orchestrator

        with SessionLocal() as db:
            rows = db.query(Report).filter(Report.status == ReportStatus.PENDING_AI).all()
            stuck = []
            for r in rows:
                started = parse_iso(r.updated_at or r.submitted_at)
                if started and (utc_now() - started).total_seconds() > 60:
                    stuck.append(r.id)
        for rid in stuck:
            logger.warning("补偿重跑 AI 审核：上报 %s", rid)
            await asyncio.to_thread(audit_orchestrator.run_ai_audit, rid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI 审核补偿任务异常（忽略）：%s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings.ensure_dirs()

    if settings.auto_create_tables:
        from app.database import Base, engine
        from app import models  # noqa: F401

        Base.metadata.create_all(bind=engine)
        logger.info("数据表检查完成（AUTO_CREATE_TABLES=true）")
        _bootstrap_seed_data()

    logger.info("%s 启动完成，访问 http://127.0.0.1:8000/", settings.app_name)
    _log_external_readiness()
    task = asyncio.create_task(_recover_stuck_ai_tasks())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _log_external_readiness() -> None:
    """启动时打印各外部服务就绪状态，避免「配了但没生效」的隐性问题。"""
    try:
        from app.adapters.sms.factory import describe_sms_provider

        sms = describe_sms_provider()
        if sms["ready"]:
            logger.info("外部服务｜短信：%s 已就绪（%s）", sms["provider"], sms.get("detail", ""))
        else:
            logger.warning("外部服务｜短信：%s 未就绪 —— %s", sms["provider"], sms.get("detail", ""))

        status = {
            "图片存储": (settings.storage_backend, settings.oss_ready() if settings.storage_backend == "oss" else True),
            "逆地理编码": (settings.geocoding_provider, settings.amap_ready() if settings.geocoding_provider == "amap" else True),
            "AI 审核": (settings.ai_provider, settings.ai_vl_ready() if settings.ai_provider == "qwenvl" else True),
        }
        for name, (provider, ready) in status.items():
            if ready:
                logger.info("外部服务｜%s：%s 已就绪", name, provider)
            else:
                logger.warning("外部服务｜%s：%s 配置不完整，将回退或不可用（详见 docs/外部服务接入指南.md）", name, provider)
    except Exception as exc:  # noqa: BLE001
        logger.warning("外部服务就绪状态检查异常（忽略）：%s", exc)


def _bootstrap_seed_data() -> None:
    """首次启动时补齐字典与默认配置（幂等）。"""
    try:
        from app.core.constants import DEFAULT_SYSTEM_CONFIG
        from app.database import SessionLocal
        from app.models import BehaviorType, SystemConfig
        from app.services import config_service

        import json
        from pathlib import Path

        seed_dir = Path(__file__).resolve().parent.parent / "seeds"
        with SessionLocal() as db:
            if db.query(SystemConfig).count() == 0:
                for key, (value, group, desc) in DEFAULT_SYSTEM_CONFIG.items():
                    db.add(SystemConfig(cfg_key=key, cfg_value=value, cfg_group=group, description=desc))
                db.commit()
                config_service.invalidate_cache()
                logger.info("已写入默认系统配置")

            if db.query(BehaviorType).count() == 0:
                seed_file = seed_dir / "behavior_types.json"
                if seed_file.exists():
                    items = json.loads(seed_file.read_text(encoding="utf-8"))
                    for item in items:
                        db.add(BehaviorType(**item))
                    db.commit()
                    logger.info("已写入 %d 条行为类型字典", len(items))
    except Exception as exc:  # noqa: BLE001
        logger.warning("种子数据初始化异常（忽略）：%s", exc)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "电动车不安全使用行为识别系统 —— 第一期：拍照上传平台 + AI/人工双重审核。\n\n"
        "**核心流程**：市民拍照上传 → AI 自动初审（规则占位引擎，可替换为视觉大模型）→ 人工复审 → 结果反馈与申诉。"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- 统一错误响应


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": exc.code, "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"ok": False, **detail})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    message = first.get("msg", "请求参数不合法")
    return JSONResponse(
        status_code=422,
        content={"ok": False, "code": "VALIDATION_ERROR", "message": f"参数校验失败：{loc} {message}", "detail": {"errors": errors}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        content = {"ok": False, **exc.detail}
    else:
        content = {"ok": False, "code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=content)


# ---------------------------------------------------------------- 路由


@app.get("/healthz", tags=["系统"], summary="健康检查")
def healthz() -> dict:
    return {"ok": True, "app": settings.app_name, "version": settings.app_version}


@app.get("/api/v1/meta", tags=["系统"], summary="平台能力自述")
def meta() -> dict:
    from app.adapters.sms.factory import describe_sms_provider
    from app.services import config_service

    sms = describe_sms_provider()
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "ai_provider": settings.ai_provider,
        "storage_backend": settings.storage_backend,
        "sms_provider": settings.sms_provider,
        "geocoding_provider": settings.geocoding_provider,
        "external_services": {
            "sms": {"provider": sms["provider"], "ready": sms["ready"], "detail": sms.get("detail")},
            "storage": {"provider": settings.storage_backend, "ready": settings.oss_ready() if settings.storage_backend == "oss" else True},
            "geocoding": {"provider": settings.geocoding_provider, "ready": settings.amap_ready() if settings.geocoding_provider == "amap" else True},
            "ai": {"provider": settings.ai_provider, "ready": bool(settings.dashscope_api_key) if settings.ai_provider == "qwenvl" else True},
        },
        "strategy": {
            "mode": "conservative",
            "description": "AI 只作建议，违规一律人工复核；仅高置信“正常”可自动放行（默认关闭）",
            "auto_close_normal_enabled": config_service.get_config_bool("ai.auto_close_normal_enabled", False),
        },
    }


app.include_router(api_router, prefix=settings.api_prefix)

# 静态前端（放在所有 API 路由之后挂载，避免抢占 /api 路径）
if settings.static_dir.exists():
    app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")
else:  # pragma: no cover
    logger.warning("静态目录不存在：%s", settings.static_dir)

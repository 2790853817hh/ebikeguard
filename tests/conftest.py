"""pytest 夹具：使用独立的临时数据库与图片目录，保证测试互不干扰。"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在导入 app 之前设置环境变量
TMP_DIR = Path(tempfile.mkdtemp(prefix="ev_audit_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(TMP_DIR / 'test.db').as_posix()}"
os.environ["DATA_DIR"] = str(TMP_DIR / "data")
os.environ["IMAGES_DIR"] = str(TMP_DIR / "data" / "images")
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["DEBUG"] = "true"
os.environ["SMS_PROVIDER"] = "mock"
os.environ["SMS_MOCK_CODE"] = "888888"
os.environ["AI_PROVIDER"] = "rule"
os.environ["GEOCODING_PROVIDER"] = "stub"
os.environ["SECRET_KEY"] = "test-secret-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import config_service  # noqa: E402

API = "/api/v1"
MOCK_CODE = "888888"


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    Base.metadata.create_all(bind=engine)
    _seed()
    yield
    shutil.rmtree(TMP_DIR, ignore_errors=True)


def _seed() -> None:
    import json

    from app.core.constants import DEFAULT_SYSTEM_CONFIG, Role
    from app.core.security import hash_password
    from app.models import AppUser, BehaviorType, SystemConfig

    with SessionLocal() as db:
        if db.query(SystemConfig).count() == 0:
            for key, (value, group, desc) in DEFAULT_SYSTEM_CONFIG.items():
                db.add(SystemConfig(cfg_key=key, cfg_value=value, cfg_group=group, description=desc))
        if db.query(BehaviorType).count() == 0:
            items = json.loads((ROOT / "seeds" / "behavior_types.json").read_text(encoding="utf-8"))
            for item in items:
                db.add(BehaviorType(**item))
        if db.query(AppUser).count() == 0:
            db.add(
                AppUser(
                    username="admin",
                    password_hash=hash_password("Admin@12345"),
                    real_name="系统管理员",
                    role=Role.ADMIN,
                    status="active",
                )
            )
            db.add(
                AppUser(
                    username="reviewer1",
                    password_hash=hash_password("Review@12345"),
                    real_name="审核员一",
                    role=Role.REVIEWER,
                    status="active",
                )
            )
            db.add(
                AppUser(
                    username="reviewer2",
                    password_hash=hash_password("Review@12345"),
                    real_name="审核员二",
                    role=Role.REVIEWER,
                    status="active",
                )
            )
        db.commit()
    config_service.invalidate_cache()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- 辅助


def make_image(color=(200, 60, 60), size=(1600, 1200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture(scope="session")
def image_bytes() -> bytes:
    return make_image()


@pytest.fixture(scope="session")
def reporter_token(client) -> str:
    phone = "13300000001"
    client.post(f"{API}/auth/sms/send", json={"phone": phone})
    res = client.post(f"{API}/auth/sms/login", json={"phone": phone, "code": MOCK_CODE})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def reviewer_token(client) -> str:
    res = client.post(f"{API}/auth/login", json={"username": "reviewer1", "password": "Review@12345"})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def admin_token(client) -> str:
    res = client.post(f"{API}/auth/login", json={"username": "admin", "password": "Admin@12345"})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}

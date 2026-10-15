"""通用工具：UTC 时间、编号生成、脱敏、近似哈希等。"""
from __future__ import annotations

import hashlib
import hmac
import io
import random
import secrets
import time
from datetime import datetime, timedelta, timezone

from PIL import Image

CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """统一存储 format：UTC ISO8601，精确到秒，带 Z。"""
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def to_cn(iso_str: str | None) -> str | None:
    """UTC ISO 字符串 -> 北京时间 ISO 字符串（展示用）。"""
    if not iso_str:
        return None
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return iso_str
    return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def report_no_day() -> str:
    """对外单号的日期段（北京时间）。

    单一时区来源：`generate_report_no()` 用它拼查询前缀，`new_report_no()` 用它拼编号。
    两者必须同源——若一边用 UTC、一边用北京时间，北京时间 00:00~08:00（UTC 还在前一天）这段里，
    前缀查不到任何记录 → 流水号恒为 1 → 连续两条上报编号相同 →
    `UNIQUE constraint failed: report.report_no`。
    """
    return utc_now().astimezone(CN_TZ).strftime("%Y%m%d")


def new_report_no(seq: int) -> str:
    """对外单号：EV + 年月日（北京时间）+ 4 位流水。"""
    return f"EV{report_no_day()}{seq:04d}"


def mask_phone(phone: str | None) -> str | None:
    if not phone or len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def average_hash(img: Image.Image, hash_size: int = 8) -> str:
    """感知哈希（aHash）：抗轻微缩放/压缩，用于相似图与重复上报检测。

    使用 Pillow 内置实现，避免引入 numpy/scipy 依赖。
    """
    small = img.convert("L").resize((hash_size, hash_size), Image.Resampling.LANCZOS)
    pixels = list(small.tobytes())  # 灰度图每像素 1 字节，避免使用已废弃的 getdata()
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)
    return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"


def hamming_distance(hex_a: str, hex_b: str) -> int:
    if not hex_a or not hex_b:
        return 999
    try:
        return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")
    except ValueError:
        return 999


def sign_image_token(image_id: str, expires_at: int, secret: str) -> str:
    """图片访问短时签名，防止链接被转发/爬取。"""
    msg = f"{image_id}:{expires_at}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def make_image_token(image_id: str, secret: str, ttl_seconds: int) -> tuple[str, int]:
    expires_at = int(time.time()) + ttl_seconds
    return sign_image_token(image_id, expires_at, secret), expires_at


def verify_image_token(image_id: str, expires_at: int, token: str, secret: str) -> bool:
    if expires_at < int(time.time()):
        return False
    expected = sign_image_token(image_id, expires_at, secret)
    return hmac.compare_digest(expected, token or "")


def gen_numeric_code(length: int = 6) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(length))


# 密码字符集：排除易混淆字符（I O l o 0 1）与 shell 敏感字符（$ ` " ' \ 空格），
# 便于在终端/配置文件中直接粘贴而不被转义
_PWD_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_PWD_LOWER = "abcdefghijkmnpqrstuvwxyz"
_PWD_DIGITS = "23456789"
_PWD_SYMBOLS = "@#%+=?"
_PWD_ALL = _PWD_UPPER + _PWD_LOWER + _PWD_DIGITS + _PWD_SYMBOLS


def gen_password(length: int = 12) -> str:
    """生成强随机密码（大写/小写/数字/符号四类齐全，使用 secrets 熵源）。

    用于初始化账号与改密脚本；与 `gen_numeric_code` 不同，此处必须使用
    cryptographically secure 的 `secrets`，不可退回 `random`。
    """
    if length < 8:
        raise ValueError("密码长度至少 8 位")
    while True:
        pwd = "".join(secrets.choice(_PWD_ALL) for _ in range(length))
        if (
            any(c in _PWD_UPPER for c in pwd)
            and any(c in _PWD_LOWER for c in pwd)
            and any(c in _PWD_DIGITS for c in pwd)
            and any(c in _PWD_SYMBOLS for c in pwd)
        ):
            return pwd


def detect_mime_by_magic(data: bytes) -> str | None:
    """按文件头判断真实类型，不信任扩展名。"""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"):
        return "image/heic"
    return None


def read_image_size(data: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return None, None

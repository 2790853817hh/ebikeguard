"""图片处理服务：校验、EXIF 提取、压缩、缩略图、哈希与去重特征。

所有图片读写都经此服务 -> StorageBackend，业务层不接触磁盘路径。
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image, ImageOps

from app.adapters.storage.factory import get_storage
from app.config import settings
from app.core.exceptions import BadRequest, Forbidden, PayloadTooLarge
from app.core.logging import get_logger
from app.core.utils import (
    average_hash,
    detect_mime_by_magic,
    make_image_token,
    sha256_bytes,
    utc_now,
    utc_now_iso,
    verify_image_token,
)

logger = get_logger(__name__)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic"}
JPEG_EXT = "jpg"


@dataclass
class ProcessedImage:
    image_id: str
    original_name: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    sha256: str
    phash: str
    phash_bucket: int
    taken_at: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    exif: dict = field(default_factory=dict)
    origin_bytes: bytes = b""
    thumb_bytes: bytes = b""


# ---------------------------------------------------------------- EXIF


def _ratio_to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return value.numerator / value.denominator
        except Exception:  # noqa: BLE001
            return 0.0


def _gps_to_deg(values) -> float | None:
    try:
        d = _ratio_to_float(values[0])
        m = _ratio_to_float(values[1])
        s = _ratio_to_float(values[2])
        return d + m / 60.0 + s / 3600.0
    except Exception:  # noqa: BLE001
        return None


def _extract_exif(im: Image.Image) -> tuple[str | None, float | None, float | None, dict]:
    taken_at: str | None = None
    lat: float | None = None
    lng: float | None = None
    flat: dict = {}
    try:
        exif = im.getexif()
        if not exif:
            return None, None, None, {}

        raw_dt = None
        try:
            sub = exif.get_ifd(0x8769)  # Exif SubIFD
            raw_dt = sub.get(36867) or sub.get(36868)
        except Exception:  # noqa: BLE001
            raw_dt = None
        raw_dt = raw_dt or exif.get(306)  # DateTime
        if raw_dt:
            raw_dt = str(raw_dt).strip()
            for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    taken_at = datetime.strptime(raw_dt, fmt).strftime("%Y-%m-%dT%H:%M:%S")
                    break
                except ValueError:
                    continue
            flat["DateTimeOriginal"] = raw_dt

        try:
            gps = exif.get_ifd(0x8825) or {}
        except Exception:  # noqa: BLE001
            gps = {}
        if gps:
            lat = _gps_to_deg(gps.get(2)) if gps.get(2) else None
            lng = _gps_to_deg(gps.get(4)) if gps.get(4) else None
            if lat is not None and str(gps.get(1, "N")).upper().startswith("S"):
                lat = -lat
            if lng is not None and str(gps.get(3, "E")).upper().startswith("W"):
                lng = -lng
            flat["GPSLatitude"] = lat
            flat["GPSLongitude"] = lng

        for key in ("Make", "Model", "Software"):
            tag = {"Make": 271, "Model": 272, "Software": 305}[key]
            if exif.get(tag):
                flat[key] = str(exif.get(tag))
    except Exception as exc:  # noqa: BLE001
        logger.warning("EXIF 解析失败（忽略）：%s", exc)
    return taken_at, lat, lng, flat


# ---------------------------------------------------------------- 处理


def _resize_keep_ratio(im: Image.Image, max_edge: int) -> Image.Image:
    w, h = im.size
    longest = max(w, h)
    if longest <= max_edge:
        return im
    scale = max_edge / float(longest)
    return im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)


def _encode_jpeg(im: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def process_upload(raw: bytes, filename: str | None = None) -> ProcessedImage:
    """校验并处理单张上传图片。"""
    if not raw:
        raise BadRequest("上传的图片为空")
    if len(raw) > settings.max_image_bytes:
        raise PayloadTooLarge(f"单张图片不得超过 {settings.max_image_mb} MB")

    mime = detect_mime_by_magic(raw)
    if mime is None or mime not in ALLOWED_MIME:
        raise BadRequest("图片格式不受支持，请上传 JPG / PNG / WEBP 格式照片")

    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as exc:  # noqa: BLE001
        raise BadRequest("图片文件已损坏或无法解析") from exc

    taken_at, gps_lat, gps_lng, exif_flat = _extract_exif(im)

    # 依 EXIF 方向旋转，避免手机竖拍照片被横置
    try:
        oriented = ImageOps.exif_transpose(im) or im
    except Exception:  # noqa: BLE001
        oriented = im

    sha = sha256_bytes(raw)
    phash = average_hash(oriented)
    bucket = int(phash[:4], 16)

    origin = _encode_jpeg(_resize_keep_ratio(oriented, settings.origin_max_edge), settings.image_jpeg_quality)
    thumb = _encode_jpeg(_resize_keep_ratio(oriented, settings.thumb_max_edge), 78)

    with Image.open(io.BytesIO(origin)) as probe:
        final_w, final_h = probe.size

    return ProcessedImage(
        image_id=str(uuid.uuid4()),
        original_name=(filename or "photo.jpg")[:255],
        mime_type="image/jpeg",  # 统一重编码为 JPEG，剥离潜在恶意载荷
        size_bytes=len(origin),
        width=final_w,
        height=final_h,
        sha256=sha,
        phash=phash,
        phash_bucket=bucket,
        taken_at=taken_at,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        exif=exif_flat,
        origin_bytes=origin,
        thumb_bytes=thumb,
    )


# ---------------------------------------------------------------- 存储键


def build_key(kind: str, report_id: int, image_id: str) -> str:
    now = utc_now()
    return f"{kind}/{now:%Y/%m/%d}/{report_id}/{image_id}.{JPEG_EXT}"


def store_image(report_id: int, img: ProcessedImage) -> tuple[str, str]:
    storage = get_storage()
    origin_key = build_key("origin", report_id, img.image_id)
    thumb_key = build_key("thumb", report_id, img.image_id)
    storage.save(origin_key, img.origin_bytes)
    storage.save(thumb_key, img.thumb_bytes)
    return origin_key, thumb_key


def read_image_bytes(storage_key: str) -> bytes:
    return get_storage().read(storage_key)


# ---------------------------------------------------------------- 访问签名


def _sign_subject(image_id: str, thumb: bool) -> str:
    return f"{image_id}:{'t' if thumb else 'o'}"


def build_image_url(image_id: str, thumb: bool = False) -> str:
    token, exp = make_image_token(_sign_subject(image_id, thumb), settings.secret_key, settings.image_url_ttl_seconds)
    suffix = "/thumb" if thumb else ""
    return f"{settings.api_prefix}/images/{image_id}{suffix}?e={exp}&s={token}"


def assert_image_token(image_id: str, thumb: bool, expires_at: int | None, token: str | None) -> None:
    """签名校验：防止图片链接被转发或爬取。"""
    if expires_at is None or not token:
        raise Forbidden("图片访问链接缺少签名参数")
    if not verify_image_token(_sign_subject(image_id, thumb), int(expires_at), token, settings.secret_key):
        raise Forbidden("图片访问链接已过期或签名无效")


def local_path_for_nginx(storage_key: str) -> str | None:
    """本地存储时返回绝对路径，供 Nginx X-Accel-Redirect 高效传输。"""
    return get_storage().local_path(storage_key)


def now_iso() -> str:
    return utc_now_iso()

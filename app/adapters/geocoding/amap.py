"""高德地图逆地理编码实现。"""
from __future__ import annotations

import httpx

from app.adapters.geocoding.base import GeocodingProvider
from app.config import settings
from app.core.exceptions import AppError

AMAP_REVERSE_URL = "https://restapi.amap.com/v3/geocode/regeo"


class AmapGeocodingProvider(GeocodingProvider):
    name = "amap"

    def __init__(self) -> None:
        if not settings.amap_key:
            raise AppError("未配置 AMAP_KEY，无法启用高德逆地理编码")

    def reverse(self, longitude: float, latitude: float) -> dict:
        params = {
            "key": settings.amap_key,
            "location": f"{longitude:.6f},{latitude:.6f}",
            "extensions": "base",
            "output": "JSON",
        }
        resp = httpx.get(AMAP_REVERSE_URL, params=params, timeout=6.0)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            raise AppError(f"逆地理编码失败：{data.get('info')}")
        regeo = data.get("regeocode", {})
        comp = regeo.get("addressComponent", {}) or {}
        return {
            "address": regeo.get("formatted_address"),
            "province": comp.get("province"),
            "city": comp.get("city") if isinstance(comp.get("city"), str) else None,
            "district": comp.get("district"),
            "street": comp.get("township"),
            "source": self.name,
            "raw": data,
        }

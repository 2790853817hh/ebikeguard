"""Stub 逆地理编码：不外呼，返回经纬度占位地址。

生产环境切换 GEOCODING_PROVIDER=amap 并配置 AMAP_KEY，
请求统一由后端代理转发（避免前端暴露 Key）。
"""
from __future__ import annotations

from app.adapters.geocoding.base import GeocodingProvider


class StubGeocodingProvider(GeocodingProvider):
    name = "stub"

    def reverse(self, longitude: float, latitude: float) -> dict:
        address = f"经度 {longitude:.5f}，纬度 {latitude:.5f}（未接入地图服务，请手动填写详细地址）"
        return {
            "address": address,
            "province": None,
            "city": None,
            "district": None,
            "street": None,
            "source": self.name,
            "raw": {"longitude": longitude, "latitude": latitude},
        }

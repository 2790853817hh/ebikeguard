"""逆地理编码抽象接口（经纬度 -> 结构化地址）。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class GeocodingProvider(Protocol):
    name: str

    def reverse(self, longitude: float, latitude: float) -> dict:
        """返回 {address, province, city, district, street, source, raw}。"""
        ...

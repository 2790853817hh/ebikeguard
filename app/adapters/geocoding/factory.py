"""地理编码工厂。"""
from __future__ import annotations

from app.adapters.geocoding.base import GeocodingProvider
from app.adapters.geocoding.stub import StubGeocodingProvider
from app.config import settings


def get_geocoding_provider() -> GeocodingProvider:
    name = (settings.geocoding_provider or "stub").lower()
    if name == "amap":
        from app.adapters.geocoding.amap import AmapGeocodingProvider

        return AmapGeocodingProvider()
    return StubGeocodingProvider()

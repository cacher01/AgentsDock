from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import Settings


class WeatherTool:
    name = "weather"
    description = "Get current weather by city name using WeatherAPI.com."

    def __init__(
        self,
        settings: Settings,
        weather_func: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.weather_func = weather_func

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        city = str(arguments.get("city") or arguments.get("q") or "").strip()
        if not city:
            raise ValueError("city is required")
        if self.weather_func is not None:
            data = self.weather_func(city)
        else:
            data = await self._call_weatherapi(city)
        return self._normalize(data)

    async def _call_weatherapi(self, city: str) -> dict[str, Any]:
        if not self.settings.weather_api_key:
            raise RuntimeError("WeatherAPI key is not configured")
        base_url = self.settings.weather_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.get(
                f"{base_url}/current.json",
                params={"key": self.settings.weather_api_key, "q": city, "aqi": "no"},
            )
            response.raise_for_status()
            return response.json()

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        location = data.get("location") or {}
        current = data.get("current") or {}
        condition = current.get("condition") or {}
        return {
            "city": location.get("name", ""),
            "country": location.get("country", ""),
            "local_time": location.get("localtime", ""),
            "temperature_c": current.get("temp_c"),
            "condition": condition.get("text", ""),
            "humidity": current.get("humidity"),
            "wind_kph": current.get("wind_kph"),
            "provider": "weatherapi",
        }

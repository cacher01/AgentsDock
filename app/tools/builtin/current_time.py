from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


class CurrentTimeTool:
    name = "current_time"
    description = "Return current time for a timezone or UTC offset."

    async def run(self, arguments: dict) -> dict:
        tz_name = str(arguments.get("timezone") or "").strip()
        city = str(arguments.get("city") or "").strip().lower()
        utc_offset = str(arguments.get("utc_offset") or "").strip()

        if not tz_name and city in {"beijing", "shanghai", "china", "北京", "上海"}:
            tz_name = "Asia/Shanghai"

        if tz_name:
            tz = ZoneInfo(tz_name)
            now = datetime.now(tz)
            label = tz_name
        elif utc_offset:
            tz = _offset_timezone(utc_offset)
            now = datetime.now(tz)
            label = f"UTC{utc_offset}"
        else:
            now = datetime.now(timezone.utc)
            label = "UTC"

        return {
            "timezone": label,
            "iso_time": now.isoformat(),
            "local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        }


def _offset_timezone(value: str) -> timezone:
    sign = 1 if value.startswith("+") else -1
    raw = value[1:] if value[:1] in {"+", "-"} else value
    hours_text, _, minutes_text = raw.partition(":")
    hours = int(hours_text or "0")
    minutes = int(minutes_text or "0")
    return timezone(sign * timedelta(hours=hours, minutes=minutes))

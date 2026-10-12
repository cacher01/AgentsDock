from datetime import date, timedelta


class DateCalculatorTool:
    name = "date_calculator"
    description = "Add or subtract days/weeks from a date."

    async def run(self, arguments: dict) -> dict:
        base = date.fromisoformat(str(arguments.get("base_date") or date.today()))
        operation = str(arguments.get("operation") or "add").lower()
        days = int(arguments.get("days") or 0)
        weeks = int(arguments.get("weeks") or 0)
        delta = timedelta(days=days + weeks * 7)
        result = base - delta if operation in {"subtract", "minus"} else base + delta
        return {
            "base_date": base.isoformat(),
            "operation": operation,
            "result_date": result.isoformat(),
        }

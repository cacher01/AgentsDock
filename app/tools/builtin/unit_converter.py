class UnitConverterTool:
    name = "unit_converter"
    description = "Convert common units."

    _factors = {
        ("km", "m"): 1000.0,
        ("m", "km"): 0.001,
        ("kg", "g"): 1000.0,
        ("g", "kg"): 0.001,
        ("c", "f"): None,
        ("f", "c"): None,
    }

    async def run(self, arguments: dict) -> dict:
        value = float(arguments["value"])
        source = str(arguments["source_unit"]).lower()
        target = str(arguments["target_unit"]).lower()
        if source == target:
            converted = value
        elif (source, target) == ("c", "f"):
            converted = value * 9 / 5 + 32
        elif (source, target) == ("f", "c"):
            converted = (value - 32) * 5 / 9
        elif (source, target) in self._factors:
            converted = value * self._factors[(source, target)]
        else:
            raise ValueError(f"Unsupported conversion: {source} to {target}")
        return {
            "value": value,
            "source_unit": source,
            "target_unit": target,
            "converted_value": converted,
        }

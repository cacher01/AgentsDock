from typing import Protocol


class Tool(Protocol):
    name: str
    description: str

    async def run(self, arguments: dict) -> dict: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        return self._tools[name]

    def list_names(self) -> list[str]:
        return sorted(self._tools)

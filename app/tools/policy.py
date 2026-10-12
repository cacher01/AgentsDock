from dataclasses import dataclass


@dataclass
class ToolPolicy:
    max_tool_calls: int

    def check_allowed(self, tool_name: str, allowed_tools: list[str]) -> None:
        if tool_name not in allowed_tools:
            raise PermissionError(f"Tool not allowed: {tool_name}")

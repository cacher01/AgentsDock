class OrchestrationError(Exception):
    """Base exception for orchestration failures."""


class SchemaValidationError(OrchestrationError):
    """Raised when structured LLM output cannot be validated."""


class ToolExecutionError(OrchestrationError):
    """Raised when a tool fails in a controlled way."""


class TaskCancelledError(OrchestrationError):
    """Raised when a cancelled task reaches a cooperative checkpoint."""


class TaskTimeoutError(OrchestrationError):
    """Raised when a task exceeds its configured runtime."""

from contextvars import ContextVar, Token
from typing import Any, Callable

from app.db.repositories import Repository
from app.llm.client import LLMClient, LLMResponse


class TaskAwareLLMClient:
    def __init__(
        self,
        delegate: LLMClient,
        repository: Repository,
        checkpoint: Callable[[str], None],
    ) -> None:
        self.delegate = delegate
        self.repository = repository
        self.checkpoint = checkpoint
        self._task_id: ContextVar[str | None] = ContextVar("llm_task_id", default=None)
        self._warnings: dict[str, list[str]] = {}

    def bind_task(self, task_id: str) -> Token:
        self._warnings.setdefault(task_id, [])
        return self._task_id.set(task_id)

    def reset_task(self, token: Token) -> None:
        self._task_id.reset(token)

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        task_id = self._task_id.get()
        if task_id is not None:
            self.checkpoint(task_id)
        if task_id is not None:
            kwargs.setdefault("task_id", task_id)
        response = await self.delegate.chat(messages, **kwargs)
        if task_id is not None:
            raw = response.raw or {}
            warnings = [str(item) for item in raw.get("warnings", [])]
            if raw.get("fallback_from") and not warnings:
                warnings = ["Primary model failed; fallback model was used."]
            for warning in warnings:
                if warning not in self._warnings[task_id]:
                    self._warnings[task_id].append(warning)
            self.repository.increment_task_tokens(task_id, response.token_estimate)
            self.checkpoint(task_id)
        return response

    def consume_warnings(self, task_id: str) -> list[str]:
        return self._warnings.pop(task_id, [])

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

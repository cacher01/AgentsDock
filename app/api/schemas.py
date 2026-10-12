from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    input: str
    session_id: str | None = None
    input_file_ids: list[str] = Field(default_factory=list)
    model_variant_id: str | None = None


class SessionCreateRequest(BaseModel):
    title: str = "New session"

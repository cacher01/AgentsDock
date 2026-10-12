from app.db.repositories import Repository
from app.schemas.workflow import FinalSynthesis


class ResultService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def save(self, task_id: str, synthesis: FinalSynthesis) -> dict:
        return self.repository.save_result(
            task_id=task_id,
            answer=synthesis.answer,
            citations=[citation.model_dump() for citation in synthesis.citations],
            limitations=synthesis.limitations,
            confidence=synthesis.confidence,
            used_workflow=synthesis.used_workflow.value,
        )

    def get(self, task_id: str) -> dict | None:
        return self.repository.get_result(task_id)

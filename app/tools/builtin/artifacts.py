from app.services.artifact_service import ArtifactService


class ArtifactWriterTool:
    name = "artifact_writer"
    description = "Create a task-scoped Markdown, text, JSON, or CSV artifact."

    def __init__(self, service: ArtifactService) -> None:
        self.service = service

    async def run(self, arguments: dict) -> dict:
        return self.service.write(
            task_id=str(arguments["task_id"]),
            filename=str(arguments["filename"]),
            format=str(arguments["format"]),
            content=arguments.get("content"),
            rows=arguments.get("rows"),
        )


class ArtifactArchiverTool:
    name = "artifact_archiver"
    description = "Create a ZIP from registered artifacts of the same task."

    def __init__(self, service: ArtifactService) -> None:
        self.service = service

    async def run(self, arguments: dict) -> dict:
        artifact_ids = arguments.get("artifact_ids")
        if artifact_ids is not None and not isinstance(artifact_ids, list):
            raise ValueError("artifact_ids must be a list")
        return self.service.archive(
            task_id=str(arguments["task_id"]),
            filename=str(arguments.get("filename", "artifacts.zip")),
            artifact_ids=artifact_ids,
        )

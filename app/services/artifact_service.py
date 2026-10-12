import csv
import json
import os
import re
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any

from app.core.config import Settings
from app.db.repositories import Repository


_FORMAT_DETAILS = {
    "md": (".md", "text/markdown"),
    "txt": (".txt", "text/plain"),
    "json": (".json", "application/json"),
    "csv": (".csv", "text/csv"),
}
_WINDOWS_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ArtifactService:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository
        self.root = Path(settings.artifact_root).expanduser()

    def write(
        self,
        task_id: str,
        filename: str,
        format: str,
        *,
        content: Any = None,
        rows: Any = None,
    ) -> dict[str, Any]:
        artifact_format = format.lower().lstrip(".")
        if artifact_format not in _FORMAT_DETAILS:
            raise ValueError("Unsupported artifact format")
        extension, media_type = _FORMAT_DETAILS[artifact_format]
        safe_name = self._validated_filename(filename, extension)
        task_dir = self._task_directory(task_id, create=True)
        self._check_artifact_count(task_id)
        output_path = self._available_path(task_dir, safe_name)

        try:
            self._serialize(output_path, artifact_format, content, rows)
            size_bytes = output_path.stat().st_size
            if size_bytes > self.settings.artifact_max_file_bytes:
                raise ValueError("Artifact exceeds single-file size limit")
            return self.repository.register_artifact(
                task_id=task_id,
                filename=output_path.name,
                media_type=media_type,
                size_bytes=size_bytes,
                relative_path=self._relative_path(task_id, output_path.name),
            )
        except Exception:
            self._remove_regular_file(output_path)
            raise

    def archive(
        self,
        task_id: str,
        filename: str = "artifacts.zip",
        *,
        artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        safe_name = self._validated_filename(filename, ".zip")
        task_dir = self._task_directory(task_id, create=True)
        self._check_artifact_count(task_id)
        artifacts = self._archive_artifacts(task_id, artifact_ids)
        if not artifacts:
            raise ValueError("No non-ZIP artifacts are available to archive")

        source_files: list[tuple[dict[str, Any], Path]] = []
        total_size = 0
        for artifact in artifacts:
            if (
                artifact["media_type"] == "application/zip"
                or Path(artifact["filename"]).suffix.lower() == ".zip"
            ):
                raise ValueError("ZIP artifacts cannot be included in an archive")
            source_path = self.resolve_artifact_path(artifact)
            if source_path.is_symlink():
                raise ValueError("Artifact source cannot be a symbolic link")
            if not source_path.is_file():
                raise ValueError(f"Artifact file not found: {artifact['artifact_id']}")
            size_bytes = source_path.stat().st_size
            total_size += size_bytes
            if total_size > self.settings.artifact_max_archive_input_bytes:
                raise ValueError("Artifact archive input exceeds size limit")
            source_files.append((artifact, source_path))

        output_path = self._available_path(task_dir, safe_name)
        try:
            with zipfile.ZipFile(
                output_path, mode="x", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for artifact, source_path in source_files:
                    archive.write(source_path, arcname=artifact["filename"])
            size_bytes = output_path.stat().st_size
            if size_bytes > self.settings.artifact_max_file_bytes:
                raise ValueError("Artifact exceeds single-file size limit")
            return self.repository.register_artifact(
                task_id=task_id,
                filename=output_path.name,
                media_type="application/zip",
                size_bytes=size_bytes,
                relative_path=self._relative_path(task_id, output_path.name),
            )
        except Exception:
            self._remove_regular_file(output_path)
            raise

    def resolve_artifact_path(self, artifact: dict[str, Any] | None) -> Path:
        if artifact is None:
            raise ValueError("Artifact not found")
        task_id = str(artifact["task_id"])
        task_dir = self._task_directory(task_id, create=False)
        relative_path = Path(str(artifact["relative_path"]))
        if (
            relative_path.is_absolute()
            or PureWindowsPath(str(artifact["relative_path"])).is_absolute()
        ):
            raise ValueError("Artifact path must be relative")
        expected = Path(task_id) / str(artifact["filename"])
        if relative_path != expected:
            raise ValueError("Artifact path does not match registered metadata")
        candidate = self.root / relative_path
        self._ensure_below(candidate, task_dir)
        return candidate

    def _archive_artifacts(
        self, task_id: str, artifact_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        if artifact_ids is None:
            return [
                artifact
                for artifact in self.repository.list_artifacts(task_id)
                if artifact["media_type"] != "application/zip"
                and Path(artifact["filename"]).suffix.lower() != ".zip"
            ]
        selected = []
        seen: set[str] = set()
        for artifact_id in artifact_ids:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            artifact = self.repository.get_artifact(task_id, artifact_id)
            if artifact is None:
                raise ValueError(f"Artifact not found for task: {artifact_id}")
            selected.append(artifact)
        return selected

    def _task_directory(self, task_id: str, *, create: bool) -> Path:
        if self.repository.get_task(task_id) is None:
            raise ValueError(f"Task not found: {task_id}")
        if not task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
            raise ValueError("Invalid task identifier")
        if self.root.exists() and self.root.is_symlink():
            raise ValueError("Artifact root cannot be a symbolic link")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        root_resolved = self.root.resolve(strict=create)
        task_dir = self.root / task_id
        if task_dir.exists() and task_dir.is_symlink():
            raise ValueError("Artifact task directory cannot be a symbolic link")
        if create:
            task_dir.mkdir(exist_ok=True)
        self._ensure_below(task_dir, root_resolved)
        return task_dir

    def _validated_filename(self, filename: str, extension: str) -> str:
        name = str(filename).strip()
        windows_path = PureWindowsPath(name)
        if (
            not name
            or Path(name).is_absolute()
            or windows_path.is_absolute()
            or "/" in name
            or "\\" in name
            or ".." in name
            or name.startswith(".")
            or name.endswith((" ", "."))
            or _INVALID_WINDOWS_CHARS.search(name)
        ):
            raise ValueError("Unsafe artifact filename")
        suffix = Path(name).suffix.lower()
        if suffix and suffix != extension:
            raise ValueError(f"Filename extension must be {extension}")
        if not suffix:
            name += extension
        device_stem = name.split(".", 1)[0].rstrip(" .").upper()
        if device_stem in _WINDOWS_DEVICE_NAMES:
            raise ValueError("Reserved Windows device filename")
        return name

    def _available_path(self, task_dir: Path, filename: str) -> Path:
        path = task_dir / filename
        self._ensure_below(path, task_dir)
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        index = 2
        registered_names = {
            item["filename"] for item in self.repository.list_artifacts(task_dir.name)
        }
        while path.exists() or path.name in registered_names:
            path = task_dir / f"{stem}-{index}{suffix}"
            self._ensure_below(path, task_dir)
            index += 1
        return path

    def _serialize(
        self,
        output_path: Path,
        artifact_format: str,
        content: Any,
        rows: Any,
    ) -> None:
        if artifact_format in {"md", "txt"}:
            if content is None:
                raise ValueError("Text content is required")
            with output_path.open("x", encoding="utf-8", newline="") as handle:
                handle.write(str(content))
            return
        if artifact_format == "json":
            value = rows if rows is not None else content
            if value is None:
                raise ValueError("JSON content or rows are required")
            with output_path.open("x", encoding="utf-8", newline="") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            return
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise ValueError("CSV rows must be a non-empty list of objects")
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                key_text = str(key)
                if key_text not in fieldnames:
                    fieldnames.append(key_text)
        normalized_rows = [
            {str(key): self._safe_csv_value(value) for key, value in row.items()} for row in rows
        ]
        with output_path.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(normalized_rows)

    def _check_artifact_count(self, task_id: str) -> None:
        if self.repository.count_artifacts(task_id) >= self.settings.artifact_max_files_per_task:
            raise ValueError("Artifact count limit reached for task")

    def _relative_path(self, task_id: str, filename: str) -> str:
        return (Path(task_id) / filename).as_posix()

    @staticmethod
    def _ensure_below(candidate: Path, parent: Path) -> None:
        candidate_resolved = candidate.resolve(strict=False)
        parent_resolved = parent.resolve(strict=False)
        try:
            candidate_resolved.relative_to(parent_resolved)
        except ValueError as exc:
            raise ValueError("Artifact path escapes task directory") from exc

    @staticmethod
    def _remove_regular_file(path: Path) -> None:
        if path.exists() and not path.is_symlink() and path.is_file():
            os.unlink(path)

    @staticmethod
    def _safe_csv_value(value: Any) -> Any:
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return f"'{value}"
        return value

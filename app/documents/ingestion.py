from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.documents.parser import parse_document
from app.domain.models import InputFileSnapshot, RequestContext


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".json", ".txt", ".md", ".csv", ".tsv"}


class DocumentIngestionService:
    def __init__(
        self,
        *,
        root: str,
        max_file_bytes: int,
        assets,
        documents,
    ) -> None:
        self.root = Path(root).resolve()
        self.max_file_bytes = max_file_bytes
        self.assets = assets
        self.documents = documents

    def validate_metadata(self, filename: str, media_type: str) -> tuple[str, str]:
        safe_name = _SAFE_NAME.sub("_", Path(filename).name).strip(" .")
        if not safe_name:
            raise ValueError("filename is invalid")
        normalized_media_type = (
            media_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
        )
        suffix = Path(safe_name).suffix.lower()
        if (
            suffix not in _SUPPORTED_SUFFIXES
            and normalized_media_type != "application/pdf"
            and not normalized_media_type.startswith("text/")
        ):
            raise ValueError(f"unsupported document type: {suffix or normalized_media_type}")
        return safe_name, normalized_media_type

    async def ingest(
        self,
        context: RequestContext,
        *,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> InputFileSnapshot:
        if not content:
            raise ValueError("file is empty")
        if len(content) > self.max_file_bytes:
            raise ValueError("file exceeds configured size limit")
        safe_name, media_type = self.validate_metadata(filename, media_type)

        input_file_id = f"input_{uuid.uuid4().hex}"
        relative = Path(context.workspace_id) / context.user_id / input_file_id / safe_name
        target = (self.root / relative).resolve()
        if self.root not in target.parents:
            raise ValueError("file path escapes input root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        snapshot = InputFileSnapshot(
            input_file_id=input_file_id,
            task_id=None,
            object_key=relative.as_posix(),
            filename=safe_name,
            media_type=media_type or "application/octet-stream",
            size_bytes=len(content),
            sha256=checksum,
            parse_status="pending",
            parse_error="",
            created_at=datetime.now(timezone.utc),
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
        )
        try:
            saved = await self.assets.save_input_file(context, snapshot)
            await self.assets.update_parse_status(context, input_file_id, "processing")
            blocks = parse_document(input_file_id, target, media_type)
            await self.documents.replace_blocks(context, input_file_id, blocks)
            return await self.assets.update_parse_status(context, input_file_id, "completed")
        except Exception as exc:
            try:
                await self.assets.update_parse_status(
                    context, input_file_id, "failed", str(exc)[:1000]
                )
            except Exception:
                target.unlink(missing_ok=True)
            raise

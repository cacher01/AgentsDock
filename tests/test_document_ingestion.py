import asyncio
import zipfile
from dataclasses import replace

import pytest

from app.documents.ingestion import DocumentIngestionService
from app.documents.parser import parse_document
from app.domain.models import RequestContext


class Assets:
    def __init__(self):
        self.saved = None
        self.statuses = []

    async def save_input_file(self, _context, snapshot):
        self.saved = snapshot
        return snapshot

    async def update_parse_status(self, _context, _file_id, status, error=""):
        self.statuses.append((status, error))
        return replace(
            self.saved,
            parse_status=status,
            parse_error=error,
        )


class Documents:
    def __init__(self):
        self.blocks = []

    async def replace_blocks(self, _context, _file_id, blocks):
        self.blocks = blocks


def context():
    return RequestContext(
        request_id="request_1",
        user_id="user_1",
        workspace_id="workspace_1",
        role="member",
    )


def test_parse_text_document_into_ordered_blocks(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("Heading\n\n" + ("content " * 1000), encoding="utf-8")

    blocks = parse_document("input_1", path, "text/markdown")

    assert len(blocks) >= 2
    assert [block.block_index for block in blocks] == list(range(len(blocks)))
    assert blocks[0].input_file_id == "input_1"


def test_parse_docx_without_external_office_dependency(tmp_path):
    path = tmp_path / "report.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
        "Quarterly report"
        "</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)

    blocks = parse_document(
        "input_docx",
        path,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert blocks[0].text == "Quarterly report"


def test_ingestion_sanitizes_filename_and_persists_blocks(tmp_path):
    assets = Assets()
    documents = Documents()
    service = DocumentIngestionService(
        root=str(tmp_path),
        max_file_bytes=1024,
        assets=assets,
        documents=documents,
    )

    result = asyncio.run(
        service.ingest(
            context(),
            filename="../../meeting notes.txt",
            media_type="text/plain",
            content=b"Decision: ship the governed task platform.",
        )
    )

    assert result.parse_status == "completed"
    assert result.filename == "meeting notes.txt"
    assert result.object_key.startswith("workspace_1/user_1/input_")
    assert documents.blocks[0].text.startswith("Decision:")
    assert [status for status, _ in assets.statuses] == [
        "processing",
        "completed",
    ]


@pytest.mark.parametrize(
    ("filename", "media_type", "message"),
    [
        ("...", "text/plain", "filename is invalid"),
        ("payload.bin", "application/octet-stream", "unsupported document type"),
    ],
)
def test_ingestion_rejects_invalid_metadata_before_writing(
    tmp_path, filename: str, media_type: str, message: str
):
    assets = Assets()
    service = DocumentIngestionService(
        root=str(tmp_path),
        max_file_bytes=1024,
        assets=assets,
        documents=Documents(),
    )

    with pytest.raises(ValueError, match=message):
        asyncio.run(
            service.ingest(
                context(),
                filename=filename,
                media_type=media_type,
                content=b"content",
            )
        )

    assert assets.saved is None
    assert list(tmp_path.rglob("*")) == []
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from app.domain.models import DocumentBlockSnapshot


_BLOCK_CHARS = 4000


def parse_document(
    input_file_id: str,
    path: Path,
    media_type: str,
) -> list[DocumentBlockSnapshot]:
    suffix = path.suffix.lower()
    if suffix == ".pdf" or media_type == "application/pdf":
        text = _pdf_text(path)
    elif suffix == ".docx":
        text = _docx_text(path)
    elif suffix == ".xlsx":
        text = _xlsx_text(path)
    elif suffix == ".json":
        text = json.dumps(
            json.loads(path.read_text(encoding="utf-8-sig")),
            ensure_ascii=False,
            indent=2,
        )
    elif suffix in {".txt", ".md", ".csv", ".tsv"} or media_type.startswith("text/"):
        text = path.read_text(encoding="utf-8-sig")
    else:
        raise ValueError(f"unsupported document type: {suffix or media_type}")
    return _blocks(input_file_id, text)


def _blocks(
    input_file_id: str,
    text: str,
) -> list[DocumentBlockSnapshot]:
    normalized = "\n".join(
        line.rstrip() for line in text.replace("\r\n", "\n").splitlines()
    ).strip()
    if not normalized:
        raise ValueError("document contains no extractable text")
    chunks: list[str] = []
    cursor = 0
    while cursor < len(normalized):
        end = min(cursor + _BLOCK_CHARS, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind("\n", cursor, end)
            if boundary > cursor + _BLOCK_CHARS // 2:
                end = boundary
        chunks.append(normalized[cursor:end].strip())
        cursor = end
        while cursor < len(normalized) and normalized[cursor] == "\n":
            cursor += 1
    offsets: list[int] = []
    offset = 0
    for chunk in chunks:
        offsets.append(offset)
        offset += len(chunk)
    return [
        DocumentBlockSnapshot(
            document_block_id=f"{input_file_id}_block_{index}",
            input_file_id=input_file_id,
            block_index=index,
            block_type="text",
            locator={"character_start": offsets[index]},
            text=chunk,
            metadata={},
        )
        for index, chunk in enumerate(chunks)
        if chunk
    ]


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF parsing requires the documents dependency") from exc
    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(namespace + "p"):
        value = "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
        if value.strip():
            paragraphs.append(value)
    return "\n".join(paragraphs)


def _xlsx_text(path: Path) -> str:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter(namespace + "si"):
                shared.append("".join(node.text or "" for node in item.iter(namespace + "t")))
        lines: list[str] = []
        sheets = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        for sheet in sheets:
            root = ElementTree.fromstring(archive.read(sheet))
            for row in root.iter(namespace + "row"):
                values = []
                for cell in row.iter(namespace + "c"):
                    value = cell.find(namespace + "v")
                    raw = value.text if value is not None and value.text else ""
                    if cell.attrib.get("t") == "s" and raw.isdigit():
                        index = int(raw)
                        raw = shared[index] if index < len(shared) else raw
                    values.append(raw)
                if values:
                    lines.append("\t".join(values))
    return "\n".join(lines)

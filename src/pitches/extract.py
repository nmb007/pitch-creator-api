"""Read the one prospect file into text before the graph starts."""

from __future__ import annotations

from pathlib import Path

ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx"}


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace").strip()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    if suffix == ".docx":
        from docx import Document

        document = Document(str(path))
        parts = [
            paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()
        ]
        return "\n".join(parts).strip()
    raise ValueError(f"Unsupported prospect file type: {suffix}")

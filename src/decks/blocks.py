"""Read a PowerPoint into ordered slide blocks with Docling."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from src.decks.drafts import ImageDraft


@dataclass
class TextBlock:
    number: int
    slide: int
    text: str
    label: str


@dataclass
class SlideBlocks:
    number: int
    blocks: list[TextBlock] = field(default_factory=list)
    images: list[ImageDraft] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        body = "\n".join(block.text for block in self.blocks)
        return hashlib.sha256(body.encode()).hexdigest()


def read_pptx_blocks(path: Path, *, deck_id: UUID | None = None) -> list[SlideBlocks]:
    """Return body-layer text blocks per slide, plus pictures from python-pptx."""
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import ContentLayer

    converter = DocumentConverter(allowed_formats=[InputFormat.PPTX])
    document = converter.convert(str(path)).document
    by_slide: dict[int, list[TextBlock]] = {}
    next_number = 1
    for item, _level in document.iterate_items(
        included_content_layers={ContentLayer.BODY},
    ):
        text = getattr(item, "text", None)
        if not text or not str(text).strip():
            continue
        label = str(getattr(item, "label", "paragraph"))
        page_no = item.prov[0].page_no if getattr(item, "prov", None) else None
        if page_no is None:
            continue
        block = TextBlock(
            number=next_number,
            slide=int(page_no),
            text=str(text).strip(),
            label=label,
        )
        by_slide.setdefault(block.slide, []).append(block)
        next_number += 1

    images_by_slide = _images_by_slide(path)
    slide_numbers = sorted(set(by_slide) | set(images_by_slide))
    slides = [
        SlideBlocks(
            number=number,
            blocks=by_slide.get(number, []),
            images=images_by_slide.get(number, []),
        )
        for number in slide_numbers
    ]
    block_count = sum(len(slide.blocks) for slide in slides)
    image_count = sum(len(slide.images) for slide in slides)
    if deck_id is not None:
        from src.decks.ingest_log import IngestPhase, ingest_info

        ingest_info(
            deck_id,
            IngestPhase.READ,
            f"finished reading. {len(slides)} slides, {block_count} text blocks, "
            f"{image_count} images.",
        )
    return slides


def _images_by_slide(path: Path) -> dict[int, list[ImageDraft]]:
    presentation = Presentation(str(path))
    result: dict[int, list[ImageDraft]] = {}
    for number, slide in enumerate(presentation.slides, start=1):
        images: list[ImageDraft] = []
        for shape in slide.shapes:
            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                continue
            image = shape.image
            images.append(ImageDraft(data=image.blob, ext=image.ext or "png"))
        if images:
            result[number] = images
    return result

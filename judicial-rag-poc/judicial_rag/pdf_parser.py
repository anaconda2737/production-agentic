import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class JudgmentParagraph:
    page_number: int
    paragraph_number: int
    text: str


def _clean_page_text(text: str) -> str:
    text = text.replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"-\n(?=\w)", "", text)
    # Join ordinary wrapped lines while preserving blank-line paragraph breaks and headings.
    text = re.sub(r"(?<=[a-z,;])\n(?=[a-z])", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_pdf(path: Path, ocr_scanned: bool = False) -> list[JudgmentParagraph]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Install PyMuPDF from requirements.txt to parse PDFs") from exc

    paragraphs: list[JudgmentParagraph] = []
    with fitz.open(path) as document:
        for page_index, page in enumerate(document):
            text = _clean_page_text(page.get_text("text", sort=True))
            if not text and ocr_scanned:
                try:
                    text_page = page.get_textpage_ocr(language="eng", dpi=200)
                    text = _clean_page_text(page.get_text("text", textpage=text_page, sort=True))
                except Exception as exc:
                    raise RuntimeError(
                        f"OCR failed on page {page_index + 1}; install Tesseract and verify its language data: {exc}"
                    ) from exc

            if not text:
                continue

            page_paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
            # Some PDFs have no blank lines; retain short heading lines with the following paragraph.
            if len(page_paragraphs) == 1:
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                page_paragraphs = [" ".join(lines)] if lines else []
            for local_number, paragraph in enumerate(page_paragraphs, start=1):
                paragraphs.append(
                    JudgmentParagraph(page_number=page_index + 1, paragraph_number=local_number, text=paragraph)
                )

    if not paragraphs:
        raise ValueError(f"No extractable text found in {path}. Enable OCR for scanned PDFs or check the file.")
    return paragraphs


def whole_text(paragraphs: Iterable[JudgmentParagraph]) -> str:
    return "\n".join(paragraph.text for paragraph in paragraphs)


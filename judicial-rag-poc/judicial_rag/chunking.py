from dataclasses import dataclass

from judicial_rag.pdf_parser import JudgmentParagraph


@dataclass(frozen=True)
class TextChunk:
    text: str
    page_start: int
    page_end: int
    paragraph_start: int
    paragraph_end: int
    paragraph_locations: list[dict[str, int]]
    chunk_index: int


def make_chunks(paragraphs: list[JudgmentParagraph], max_words: int, overlap_words: int) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    buffer: list[JudgmentParagraph] = []
    buffer_words = 0

    def emit(items: list[JudgmentParagraph], text_override: str | None = None) -> None:
        if not items:
            return
        chunks.append(
            TextChunk(
                text=text_override or "\n\n".join(item.text for item in items),
                page_start=min(item.page_number for item in items),
                page_end=max(item.page_number for item in items),
                paragraph_start=min(item.paragraph_number for item in items),
                paragraph_end=max(item.paragraph_number for item in items),
                paragraph_locations=[
                    {"page_number": page, "paragraph_number": paragraph}
                    for page, paragraph in sorted({(item.page_number, item.paragraph_number) for item in items})
                ],
                chunk_index=len(chunks),
            )
        )

    for paragraph in paragraphs:
        words = paragraph.text.split()
        if len(words) > max_words:
            if buffer:
                emit(buffer)
                buffer = []
                buffer_words = 0
            step = max(1, max_words - overlap_words)
            for start in range(0, len(words), step):
                window = words[start : start + max_words]
                if not window:
                    break
                emit([paragraph], " ".join(window))
                if start + max_words >= len(words):
                    break
            continue

        if buffer and buffer_words + len(words) > max_words:
            previous = buffer[-1]
            emit(buffer)
            tail_words = previous.text.split()[-overlap_words:] if overlap_words else []
            buffer = []
            if tail_words:
                buffer.append(
                    JudgmentParagraph(
                        page_number=previous.page_number,
                        paragraph_number=previous.paragraph_number,
                        text=" ".join(tail_words),
                    )
                )
            buffer_words = len(tail_words)

        buffer.append(paragraph)
        buffer_words += len(words)

    emit(buffer)
    return chunks


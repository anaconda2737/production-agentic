import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from judicial_rag.pdf_parser import JudgmentParagraph


def normalize_identifier(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    value = re.sub(r"\bversus\b|\bvs\.?\b|\bv\.?\b", " vs ", value)
    value = value.replace("delhi", "del")
    return re.sub(r"[^a-z0-9]+", "", value)


def split_party_query(query: str) -> tuple[str, str]:
    parts = re.split(r"\s+(?:versus|vs?\.?)\s+", query.strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise ValueError("Enter both parties, for example 'State of Maharashtra vs H.N. Dhillon'.")
    return parts[0].strip(), parts[1].strip()


CASE_CITATION_PATTERNS = [
    re.compile(r"\b(?:19|20)\d{2}\s*:\s*[A-Z][A-Z.]{1,10}\s*:\s*\d{1,7}\b", re.IGNORECASE),
    re.compile(r"\(\s*(?:19|20)\d{2}\s*\)\s*SCC\s*On\s*Line\s+[A-Z][A-Za-z. ]{0,25}?\s+\d{1,7}\b", re.IGNORECASE),
    re.compile(r"\bAIR\s+(?:19|20)\d{2}\s+[A-Z][A-Za-z. ]{0,25}?\s+\d{1,5}\b", re.IGNORECASE),
    re.compile(r"\(\s*(?:19|20)\d{2}\s*\)\s*\d{1,2}\s*SCC\s*\d{1,5}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:W\.?\s*P\.?\s*\(\s*[A-Z]+\s*\)|WPC|SLP|Crl\.?\s*A\.?|CRL\.\s*M\.C\.|FAO|RSA|LPA|RFA)"
        r"\s*(?:No\.?\s*)?\d+[A-Z]?(?:\s*/\s*|\s+of\s+)?(?:19|20)\d{2}\b",
        re.IGNORECASE,
    ),
]

PROVISION_PATTERN = re.compile(
    r"\b(?P<kind>articles?|arts?\.?|sections?|secs?\.?|s\.)\s*"
    r"(?P<number>\d+[A-Za-z]?(?:\s*\([0-9A-Za-z]+\))*(?:\s*[,/&and-]+\s*\d+[A-Za-z]?)*)",
    re.IGNORECASE,
)

STATUTE_NAMES = {
    "constitution": "Constitution of India",
    "constitutionofindia": "Constitution of India",
    "indianpenalcode": "Indian Penal Code, 1860",
    "ipc": "Indian Penal Code, 1860",
    "codeofcriminalprocedure": "Code of Criminal Procedure, 1973",
    "crpc": "Code of Criminal Procedure, 1973",
    "codeofcivilprocedure": "Code of Civil Procedure, 1908",
    "cpc": "Code of Civil Procedure, 1908",
    "bharatiyanyayasanhita": "Bharatiya Nyaya Sanhita, 2023",
    "bns": "Bharatiya Nyaya Sanhita, 2023",
    "bharatiyanagariksurakshasanhita": "Bharatiya Nagarik Suraksha Sanhita, 2023",
    "bnss": "Bharatiya Nagarik Suraksha Sanhita, 2023",
    "indianevidenceact": "Indian Evidence Act, 1872",
    "bharatiyasakshyaadhiniyam": "Bharatiya Sakshya Adhiniyam, 2023",
    "bsa": "Bharatiya Sakshya Adhiniyam, 2023",
}


@dataclass(frozen=True)
class ExtractedOccurrence:
    value: str
    normalized_value: str
    page_number: int
    paragraph_number: int
    snippet: str
    confidence: float


@dataclass(frozen=True)
class ExtractedProvision:
    provision_type: str
    provision_number: str
    statute_name: str
    statute_key: str
    raw_reference: str
    page_number: int
    paragraph_number: int
    snippet: str


def extract_case_citations(paragraphs: Iterable[JudgmentParagraph]) -> list[ExtractedOccurrence]:
    found: dict[tuple[int, int, int, int, str], ExtractedOccurrence] = {}
    for paragraph in paragraphs:
        for pattern in CASE_CITATION_PATTERNS:
            for match in pattern.finditer(paragraph.text):
                raw = re.sub(r"\s+", " ", match.group(0)).strip(" .,;:")
                normalized = normalize_identifier(raw)
                if not normalized:
                    continue
                key = (paragraph.page_number, paragraph.paragraph_number, match.start(), match.end(), normalized)
                found.setdefault(
                    key,
                    ExtractedOccurrence(
                        value=raw,
                        normalized_value=normalized,
                        page_number=paragraph.page_number,
                        paragraph_number=paragraph.paragraph_number,
                        snippet=paragraph.text[:1500],
                        confidence=0.65,
                    ),
                )
    return list(found.values())


def _infer_statute(context: str) -> tuple[str, str]:
    context_key = normalize_identifier(context)
    # Prefer long statute names before short aliases.
    for alias in sorted(STATUTE_NAMES, key=len, reverse=True):
        if alias in context_key:
            canonical = STATUTE_NAMES[alias]
            return canonical, normalize_identifier(canonical)
    return "unspecified statute", "unspecified"


def extract_provisions(paragraphs: Iterable[JudgmentParagraph]) -> list[ExtractedProvision]:
    found: list[ExtractedProvision] = []
    for paragraph in paragraphs:
        for match in PROVISION_PATTERN.finditer(paragraph.text):
            kind = match.group("kind").casefold()
            provision_type = "article" if kind.startswith("art") else "section"
            # Split lists into individual provision identifiers while keeping parenthetical subclauses.
            numbers = re.split(r"\s*(?:,|/|&|\band\b|-)\s*", match.group("number"), flags=re.IGNORECASE)
            context = paragraph.text[max(0, match.start() - 80) : min(len(paragraph.text), match.end() + 140)]
            statute_name, statute_key = _infer_statute(context)
            for number in numbers:
                number = re.sub(r"\s+", "", number.strip(".,;"))
                if not number:
                    continue
                raw = f"{provision_type.title()} {number}"
                if statute_name != "unspecified statute":
                    raw += f" {statute_name}"
                found.append(
                    ExtractedProvision(
                        provision_type=provision_type,
                        provision_number=number,
                        statute_name=statute_name,
                        statute_key=statute_key,
                        raw_reference=raw,
                        page_number=paragraph.page_number,
                        paragraph_number=paragraph.paragraph_number,
                        snippet=paragraph.text[:1500],
                    )
                )
    return found


def parse_provision_query(query: str) -> tuple[str, str, str | None]:
    match = PROVISION_PATTERN.search(query)
    if not match:
        raise ValueError("Use a reference such as 'Article 21' or 'Section 302 IPC'.")
    kind = match.group("kind").casefold()
    provision_type = "article" if kind.startswith("art") else "section"
    number = re.split(r"\s*(?:,|/|&|\band\b|-)\s*", match.group("number"), flags=re.IGNORECASE)[0]
    remainder = query[match.end() :]
    prefix = query[max(0, match.start() - 80) : match.start()]
    statute_name, statute_key = _infer_statute(f"{prefix} {remainder}")
    if statute_name == "unspecified statute":
        return provision_type, number, None
    return provision_type, number, statute_key


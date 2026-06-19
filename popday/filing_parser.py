"""Lightweight SEC filing parser for 8-K style submissions."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin


ABBREVIATIONS = (
    "Inc.",
    "Corp.",
    "Co.",
    "Ltd.",
    "U.S.",
    "a.m.",
    "p.m.",
    "Mr.",
    "Ms.",
    "Dr.",
    "St.",
    "No.",
    "Jr.",
    "Sr.",
    "e.g.",
    "i.e.",
)
DATE_HINT_RE = re.compile(
    r"\b(?:"
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?"
    r")\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s+\d{4}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{4}\b",
    re.IGNORECASE,
)
DOC_META_RE = {
    "type": re.compile(r"(?im)^<TYPE>(.*)$"),
    "filename": re.compile(r"(?im)^<FILENAME>(.*)$"),
    "description": re.compile(r"(?im)^<DESCRIPTION>(.*)$"),
}
HEADER_KEY_RE = re.compile(r"^([A-Z][A-Z0-9 .&/()-]+):\s*(.*)$")
DOCUMENT_RE = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.IGNORECASE | re.DOTALL)
TEXT_RE = re.compile(r"<TEXT>(.*?)</TEXT>", re.IGNORECASE | re.DOTALL)
ANCHOR_RE = re.compile(
    r"<a\b[^>]*\bhref=[\"']?([^\"'>\s]+)[\"']?[^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
RAW_URL_RE = re.compile(r"\bhttps?://[^\s<>\"]+", re.IGNORECASE)
TRIGGER_DEFAULTS = ("investor day", "analyst day")
TITLECASE_SHORT_WORDS = {"INC", "CORP", "CO", "LTD", "PLC", "LLC", "LP"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return normalize_text(" ".join(self.parts))


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\xa0", " ").replace("\u200b", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def html_to_text(value: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        extractor = _TextExtractor()
        try:
            extractor.feed(value or "")
            return extractor.text()
        except Exception:
            fallback = re.sub(r"<[^>]+>", " ", value or "")
            return normalize_text(fallback)
    return normalize_text(BeautifulSoup(value or "", "html.parser").get_text(" "))


def extract_links(value: str, *, base_url: str = "") -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in ANCHOR_RE.finditer(value or ""):
        href = html.unescape(match.group(1).strip())
        text = html_to_text(match.group(2))
        if href.startswith("#") or href.lower().startswith(("mailto:", "javascript:")):
            continue
        url = urljoin(base_url, href) if base_url else href
        if url and url not in seen:
            links.append({"url": url, "text": text})
            seen.add(url)
    for match in RAW_URL_RE.finditer(html.unescape(value or "")):
        url = match.group(0).rstrip(").,;]")
        if url and url not in seen:
            links.append({"url": url, "text": ""})
            seen.add(url)
    return links


def _iso_date(raw: str) -> str:
    text = normalize_text(raw)
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    return text


def _clean_company_name(value: str) -> str:
    text = normalize_text(value)
    if not text or text != text.upper():
        return text

    def convert_token(token: str) -> str:
        prefix_match = re.match(r"^\W*", token)
        suffix_match = re.search(r"\W*$", token)
        prefix = prefix_match.group(0) if prefix_match else ""
        suffix = suffix_match.group(0) if suffix_match else ""
        core = token[len(prefix) : len(token) - len(suffix) if suffix else len(token)]
        if not core:
            return token
        upper_core = core.upper()
        if upper_core in TITLECASE_SHORT_WORDS:
            converted = upper_core.capitalize()
        elif upper_core.isalpha() and len(upper_core) <= 4:
            converted = upper_core
        else:
            converted = core.capitalize()
        return f"{prefix}{converted}{suffix}"

    return " ".join(convert_token(part) for part in text.split())


def _mask_abbreviations(text: str) -> tuple[str, dict[str, str]]:
    masked = text
    replacements: dict[str, str] = {}
    for index, abbreviation in enumerate(ABBREVIATIONS):
        token = f"__ABBR_{index}__"
        safe = abbreviation.replace(".", token)
        masked = masked.replace(abbreviation, safe)
        replacements[token] = "."
    return masked, replacements


def split_sentences(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    masked, replacements = _mask_abbreviations(normalized)
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", masked) if part.strip()]
    restored: list[str] = []
    for part in parts:
        restored_part = part
        for token, value in replacements.items():
            restored_part = restored_part.replace(token, value)
        restored.append(normalize_text(restored_part))
    return restored


def _trim(text: str, limit: int = 300) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return f"{clipped}..."


@dataclass(frozen=True)
class ParsedFiling:
    accession: str
    form_type: str
    items: list[str]
    company_name: str
    cik: str
    filed_date: str
    documents: list[dict]

    @property
    def press_releases(self) -> list[str]:
        matches: list[str] = []
        fallback: list[str] = []
        for document in self.documents:
            doc_type = str(document.get("type") or "").upper()
            description = str(document.get("description") or "").lower()
            text = normalize_text(str(document.get("text") or ""))
            if not text:
                continue
            if doc_type.startswith("EX-99") or "press release" in description:
                matches.append(text)
            elif any(trigger in text.lower() for trigger in TRIGGER_DEFAULTS):
                fallback.append(text)
        return matches or fallback

    @property
    def has_press_release(self) -> bool:
        return bool(self.press_releases)

    @property
    def cover_text(self) -> str:
        for document in self.documents:
            doc_type = str(document.get("type") or "").upper()
            if doc_type == self.form_type.upper():
                return normalize_text(str(document.get("text") or ""))
        for document in self.documents:
            text = normalize_text(str(document.get("text") or ""))
            if text:
                return text
        return ""


def parse_sec_filing(raw: str) -> ParsedFiling:
    header_match = re.search(r"<SEC-HEADER>(.*?)</SEC-HEADER>", raw, re.IGNORECASE | re.DOTALL)
    header = header_match.group(1) if header_match else ""

    accession = ""
    form_type = ""
    company_name = ""
    cik = ""
    filed_date = ""
    items: list[str] = []

    for line in header.splitlines():
        match = HEADER_KEY_RE.match(line.strip())
        if not match:
            continue
        key = match.group(1).strip()
        value = normalize_text(match.group(2))
        if key == "ACCESSION NUMBER" and not accession:
            accession = value
        elif key == "CONFORMED SUBMISSION TYPE" and not form_type:
            form_type = value
        elif key == "COMPANY CONFORMED NAME" and not company_name:
            company_name = _clean_company_name(value)
        elif key == "CENTRAL INDEX KEY" and not cik:
            cik = value.zfill(10)
        elif key == "FILED AS OF DATE" and not filed_date:
            filed_date = _iso_date(value)
        elif key == "ITEM INFORMATION" and value:
            items.append(value)

    blocks = DOCUMENT_RE.findall(raw) or [raw]
    documents: list[dict] = []
    for block in blocks:
        metadata = {
            key: normalize_text((regex.search(block).group(1) if regex.search(block) else ""))
            for key, regex in DOC_META_RE.items()
        }
        text_match = TEXT_RE.search(block)
        body = text_match.group(1) if text_match else block
        documents.append(
            {
                "type": metadata["type"],
                "filename": metadata["filename"],
                "description": metadata["description"],
                "text": html_to_text(body),
                "links": extract_links(body),
            }
        )

    return ParsedFiling(
        accession=accession,
        form_type=form_type,
        items=items,
        company_name=company_name,
        cik=cik.zfill(10) if cik else "",
        filed_date=filed_date,
        documents=documents,
    )


def best_nugget(parsed: ParsedFiling, triggers: tuple[str, ...] = TRIGGER_DEFAULTS) -> str:
    candidate_docs = list(parsed.press_releases)
    if parsed.cover_text:
        candidate_docs.append(parsed.cover_text)

    best_sentence = ""
    best_score = -1
    lowered_triggers = tuple(trigger.lower() for trigger in triggers)

    for doc_index, text in enumerate(candidate_docs):
        for sentence in split_sentences(text):
            lowered = sentence.lower()
            if not any(trigger in lowered for trigger in lowered_triggers):
                continue
            score = 0
            if DATE_HINT_RE.search(sentence):
                score += 3
            if any(cue in lowered for cue in ("will host", "will hold", "to host", "to hold", "scheduled", "will present")):
                score += 2
            if doc_index == 0:
                score += 2
            if len(sentence) <= 300:
                score += 1
            if score > best_score:
                best_score = score
                best_sentence = sentence

    return _trim(best_sentence)

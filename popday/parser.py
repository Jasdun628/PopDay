"""Filing document parsing with location-weighted text sections."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class Section:
    location: str
    text: str


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return normalize_text(" ".join(self.parts))


def normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_tags(value: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(value)
        return extractor.text()
    except Exception:
        fallback = re.sub(r"<[^>]+>", " ", value)
        return normalize_text(fallback)


def _document_blocks(raw: str) -> list[str]:
    blocks = re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", raw, flags=re.IGNORECASE | re.DOTALL)
    return blocks or [raw]


def _tag_value(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\s*(.*?)(?:\n|<)", block, flags=re.IGNORECASE | re.DOTALL)
    return normalize_text(match.group(1)) if match else ""


def parse_filing_sections(raw: str) -> list[Section]:
    sections: list[Section] = []

    for index, block in enumerate(_document_blocks(raw)):
        doc_type = _tag_value(block, "TYPE").upper()
        filename = _tag_value(block, "FILENAME")
        description = _tag_value(block, "DESCRIPTION")
        label = doc_type or filename or f"document_{index + 1}"

        if description:
            sections.append(Section(f"exhibit_description:{label}", description))
        if filename:
            sections.append(Section(f"filing_or_exhibit_headline:{label}", filename))

        text_match = re.search(r"<TEXT>(.*?)</TEXT>", block, flags=re.IGNORECASE | re.DOTALL)
        text_body = text_match.group(1) if text_match else block
        plain_text = strip_tags(text_body)
        if not plain_text:
            continue

        sentences = re.split(r"(?<=[.!?])\s+", plain_text)
        first_paragraphs = " ".join(sentences[:8])
        titleish = " ".join(sentences[:2])
        location_prefix = "press_release" if "EX-99" in doc_type or "99" in label else "filing"

        if titleish:
            sections.append(Section(f"{location_prefix}_title:{label}", titleish))
        if first_paragraphs:
            sections.append(Section(f"{location_prefix}_first_paragraphs:{label}", first_paragraphs))
        sections.append(Section(f"full_body:{label}", plain_text))

    return sections

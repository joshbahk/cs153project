"""Readable-text extraction from HTML landing pages."""

from __future__ import annotations

from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "form"}
_CAPTURE_TAGS = {
    "title",
    "h1",
    "h2",
    "h3",
    "h4",
    "p",
    "li",
    "td",
    "th",
    "figcaption",
    "blockquote",
}


class ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._capture = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag in _CAPTURE_TAGS:
            self._capture = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in _CAPTURE_TAGS:
            self._capture = False
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._capture:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self._chunks.append(cleaned + " ")

    def text(self) -> str:
        return "\n".join(chunk.strip() for chunk in self._chunks if chunk.strip())


def html_to_text(html: bytes | str) -> str:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    parser = ReadableHTMLParser()
    parser.feed(html)
    return parser.text()

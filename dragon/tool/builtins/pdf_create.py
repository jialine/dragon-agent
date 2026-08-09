"""
Dragon Agent — Zero-dependency PDF Creator
==========================================

Creates simple PDF documents from markdown text.
No external dependencies — uses only Python stdlib.

Tool:
    - pdf_create: Create a PDF from markdown text.
"""

from __future__ import annotations

import json
import logging
import re
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dragon.tool.builtins.pdf_create")

# ── Minimal PDF writer (zero dependencies) ──────────────────────────

class SimplePDF:
    """Write a basic text PDF using raw PDF format. Supports:
    - Headings (#, ##, ###)
    - Bold (**text**), italic (*text*), code (`text`)
    - Unordered lists (- / *)
    - Paragraphs
    - Page breaks (---)
    """

    def __init__(self):
        self.objects: List[bytes] = []
        self.pages: List[int] = []
        self._font_registered = False

    def _obj(self, data: bytes) -> int:
        """Add an indirect object, return its 1-indexed object number."""
        self.objects.append(data)
        return len(self.objects)

    def _write_header(self) -> bytes:
        return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"

    def _build_font_obj(self) -> int:
        """Standard Type1 Helvetica font."""
        font = (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        )
        return self._obj(font)

    def _build_font_bold_obj(self) -> int:
        font = (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\n"
        )
        return self._obj(font)

    def _build_font_italic_obj(self) -> int:
        font = (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >>\n"
        )
        return self._obj(font)

    def _build_font_mono_obj(self) -> int:
        font = (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\n"
        )
        return self._obj(font)

    def add_page(self, text: str, title: str = "") -> None:
        """Add a page with markdown text."""
        if not self._font_registered:
            self.font_normal = self._build_font_obj()
            self.font_bold = self._build_font_bold_obj()
            self.font_italic = self._build_font_italic_obj()
            self.font_mono = self._build_font_mono_obj()
            self._font_registered = True

        # Build content stream
        stream = self._render_markdown(text, title)
        compressed = zlib.compress(stream)

        stream_obj_num = len(self.objects) + 1
        length_obj = self._obj(f"{len(compressed)}\n".encode())

        stream_data = (
            f"<< /Length {stream_obj_num} 0 R /Filter /FlateDecode >>\n"
            f"stream\n"
        ).encode() + compressed + b"\nendstream\n"

        content_obj = self._obj(stream_data)

        # Page object
        page = (
            f"<< /Type /Page /Parent 0 R\n"
            f"   /MediaBox [0 0 595 842]\n"
            f"   /Contents {content_obj} 0 R\n"
            f"   /Resources << /Font <<"
            f" /F1 {self.font_normal} 0 R"
            f" /F2 {self.font_bold} 0 R"
            f" /F3 {self.font_italic} 0 R"
            f" /F4 {self.font_mono} 0 R"
            f" >> >>\n"
            f">>\n"
        ).encode()
        self.pages.append(self._obj(page))

    def _render_markdown(self, text: str, title: str = "") -> bytes:
        """Render markdown text into PDF content stream operators."""
        lines = []
        # Page settings
        margin_left = 50
        margin_right = 545
        margin_top = 792
        margin_bottom = 50
        line_height = 14
        font_size_normal = 10
        font_size_h1 = 18
        font_size_h2 = 14
        font_size_h3 = 12
        font_size_code = 9

        y = margin_top - 40

        def _add_line(txt: str, font: str, size: float, y_pos: float, nl: bool = True):
            nonlocal y
            y = y_pos
            ops = []
            ops.append(b"BT\n")
            ops.append(f"/{font} {size} Tf\n".encode())
            ops.append(f"1 0 0 1 {margin_left} {y} Tm\n".encode())
            # Escape PDF string
            escaped = txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({escaped}) Tj\n".encode())
            ops.append(b"ET\n")
            y -= line_height if nl else 0
            return b"".join(ops)

        # Title
        if title:
            lines.append(_add_line(title, "F2", font_size_h1, y))
            y -= 8  # extra space after title

        # Parse markdown
        paragraphs = text.split("\n\n")
        for para in paragraphs:
            if not para.strip():
                continue

            # Check for page break
            if para.strip() == "---":
                # Page break — handled at add_page level
                continue

            for line in para.split("\n"):
                if y < margin_bottom:
                    break  # Let caller handle page breaks

                stripped = line.strip()
                if not stripped:
                    y -= line_height / 2
                    continue

                # Headings
                h1 = re.match(r"^# (.+)", stripped)
                h2 = re.match(r"^## (.+)", stripped)
                h3 = re.match(r"^### (.+)", stripped)

                if h1:
                    lines.append(_add_line(h1.group(1), "F2", font_size_h1, y))
                    y -= 4
                elif h2:
                    lines.append(_add_line(h2.group(1), "F2", font_size_h2, y))
                    y -= 3
                elif h3:
                    lines.append(_add_line(h3.group(1), "F2", font_size_h3, y))
                    y -= 2
                else:
                    # Inline formatting: **bold**, *italic*, `code`
                    # Simple approach: detect and split
                    segments = self._parse_inline(stripped)
                    x = margin_left
                    current_y = y
                    for seg_text, seg_style in segments:
                        font = {"bold": "F2", "italic": "F3", "code": "F4", "normal": "F1"}.get(seg_style, "F1")
                        size = font_size_code if seg_style == "code" else font_size_normal
                        escaped = seg_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                        ops = []
                        ops.append(b"BT\n")
                        ops.append(f"/{font} {size} Tf\n".encode())
                        ops.append(f"1 0 0 1 {x} {current_y} Tm\n".encode())
                        ops.append(f"({escaped}) Tj\n".encode())
                        ops.append(b"ET\n")
                        lines.append(b"".join(ops))
                        x += len(seg_text) * (size * 0.5)  # rough spacing
                    y -= line_height

        return b"\n".join(lines)

    def _parse_inline(self, text: str) -> List[tuple]:
        """Parse inline formatting: **bold**, *italic*, `code`."""
        segments = []
        i = 0
        while i < len(text):
            # Bold
            if text[i:i+2] == "**":
                end = text.find("**", i+2)
                if end > i:
                    segments.append((text[i+2:end], "bold"))
                    i = end + 2
                    continue
            # Italic
            if text[i] == "*" and text[i+1:i+2] != "*":
                end = text.find("*", i+1)
                if end > i:
                    segments.append((text[i+1:end], "italic"))
                    i = end + 1
                    continue
            # Code
            if text[i] == "`":
                end = text.find("`", i+1)
                if end > i:
                    segments.append((text[i+1:end], "code"))
                    i = end + 1
                    continue
            # Normal text
            j = i
            while j < len(text) and text[j] not in "*`":
                j += 1
            segments.append((text[i:j], "normal"))
            i = j
        return segments

    def build(self) -> bytes:
        """Assemble the full PDF document."""
        # Pages object
        kids = b" ".join(f"{p} 0 R".encode() for p in self.pages)
        pages_obj = self._obj(
            f"<< /Type /Pages /Kids [{kids.decode()}] /Count {len(self.pages)} >>\n".encode()
        )

        # Fix parent reference in page objects
        for i, obj in enumerate(self.objects):
            self.objects[i] = obj.replace(b"/Parent 0 R", f"/Parent {pages_obj} 0 R".encode())

        # Catalog
        catalog = self._obj(
            f"<< /Type /Catalog /Pages {pages_obj} 0 R >>\n".encode()
        )

        # Assemble
        output = [self._write_header()]
        for i, obj in enumerate(self.objects, 1):
            output.append(f"{i} 0 obj\n".encode())
            output.append(obj)
            output.append(b"endobj\n")

        # Cross-reference table
        xref_offset = sum(len(o) for o in output)
        output.append(b"xref\n")
        output.append(f"0 {len(self.objects) + 1}\n".encode())
        output.append(b"0000000000 65535 f \n")
        byte_pos = len(self._write_header())
        for i in range(len(self.objects)):
            output.append(f"{byte_pos:010d} 00000 n \n".encode())
            # Calculate offset for next object
            obj_header = f"{i+1} 0 obj\n".encode()
            obj_footer = b"endobj\n"
            byte_pos += len(obj_header) + len(self.objects[i]) + len(obj_footer)

        # Trailer
        output.append(b"trailer\n")
        output.append(f"<< /Size {len(self.objects) + 1} /Root {catalog} 0 R >>\n".encode())
        output.append(b"startxref\n")
        output.append(f"{xref_offset}\n".encode())
        output.append(b"%%EOF\n")

        return b"".join(output)


# ── Tool function ────────────────────────────────────────────────────

async def tool_pdf_create(
    path: str,
    content: str,
    title: str = "",
    author: str = "Dragon Agent",
) -> str:
    """Create a PDF document from markdown-formatted text.

    Supports basic formatting: # headings, **bold**, *italic*, `code`,
    unordered lists, and paragraphs. Separate pages with --- on its own line.

    Args:
        path: Output PDF file path (e.g., /tmp/report.pdf).
        content: Markdown text for the PDF body.
        title: Optional document title displayed on first page.
        author: Optional author metadata.

    Returns:
        JSON with file path and status.
    """
    p = Path(path).expanduser().resolve()

    try:
        pdf = SimplePDF()

        # Split content by page breaks
        pages = content.split("\n---\n") if "---" in content else [content]

        for i, page_text in enumerate(pages):
            page_title = title if i == 0 and title else ""
            pdf.add_page(page_text.strip(), page_title)

        pdf_bytes = pdf.build()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(pdf_bytes)

        return json.dumps({
            "file": str(p),
            "pages": len(pages),
            "size_bytes": len(pdf_bytes),
            "title": title or "(untitled)",
            "author": author,
        })
    except Exception as e:
        logger.exception("PDF creation failed")
        return json.dumps({"error": str(e)})

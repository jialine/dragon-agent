"""
Dragon Agent — Zero-dependency PDF Creator
==========================================

Creates simple PDF documents from markdown text.
No external dependencies — uses only Python stdlib.
Supports Chinese/CJK via embedded TrueType font.

Tool:
    - pdf_create: Create a PDF from markdown text.
"""

from __future__ import annotations

import json
import logging
import re
import struct
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dragon.tool.builtins.pdf_create")

# ── Default CJK font path ────────────────────────────────────────────

_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts-droid-fallback/truetype/DroidSansFallback.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _find_cjk_font() -> Optional[str]:
    for path in _CJK_FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


# ── Minimal TTF parser for cmap + hmtx ────────────────────────────────

class TTFCJKFont:
    """Parse a TrueType font to get cmap (Unicode→glyph) and glyph widths.

    Only reads the tables needed for CJK PDF embedding — does NOT
    parse the full font.
    """

    def __init__(self, ttf_path: str):
        self.path = ttf_path
        self._data: bytes = b""
        self._glyph_map: Dict[int, int] = {}  # unicode codepoint → glyph index
        self._glyph_widths: Dict[int, int] = {}  # glyph index → advance width (funits)
        self._units_per_em: int = 1000
        self._ascent: int = 800
        self._descent: int = -200
        self._bbox: Tuple[int, int, int, int] = (0, -200, 1000, 800)
        self._load()

    def _load(self) -> None:
        with open(self.path, "rb") as f:
            self._data = f.read()

        if len(self._data) < 12:
            raise ValueError("Invalid TTF: too small")

        # ── Offset table ──
        sf_version, num_tables, _, _, _ = struct.unpack(">IHHHH", self._data[:12])
        if sf_version == 0x00010000:
            pass  # TrueType
        elif sf_version == 0x4F54544F:  # 'OTTO'
            pass  # OpenType with CFF outlines — cmap still works
        else:
            raise ValueError(f"Unknown sfVersion: 0x{sf_version:08X}")

        # ── Table directory ──
        tables: Dict[str, Tuple[int, int]] = {}
        for i in range(num_tables):
            off = 12 + i * 16
            tag = self._data[off : off + 4].decode("ascii", errors="replace")
            check_sum, offset, length = struct.unpack(">III", self._data[off + 4 : off + 16])
            tables[tag] = (offset, length)

        # ── head table: unitsPerEm, bbox ──
        if "head" in tables:
            head_off, _ = tables["head"]
            h = self._data
            self._units_per_em = struct.unpack(">H", h[head_off + 18 : head_off + 20])[0]
            xmin, ymin, xmax, ymax = struct.unpack(">hhhh", h[head_off + 36 : head_off + 44])
            self._bbox = (xmin, ymin, xmax, ymax)

        # ── hhea table: ascent, descent, numberOfHMetrics ──
        num_hmetrics = 0
        if "hhea" in tables:
            hhea_off, _ = tables["hhea"]
            h = self._data
            self._ascent = struct.unpack(">h", h[hhea_off + 4 : hhea_off + 6])[0]
            self._descent = struct.unpack(">h", h[hhea_off + 6 : hhea_off + 8])[0]
            num_hmetrics = struct.unpack(">H", h[hhea_off + 34 : hhea_off + 36])[0]

        # ── cmap table: Unicode → glyph index ──
        if "cmap" in tables:
            cmap_off, cmap_len = tables["cmap"]
            self._parse_cmap(cmap_off)

        # ── hmtx table: glyph widths ──
        if "hmtx" in tables and num_hmetrics > 0:
            hmtx_off, _ = tables["hmtx"]
            self._parse_hmtx(hmtx_off, num_hmetrics)

    def _parse_cmap(self, cmap_off: int) -> None:
        """Parse cmap table for format 4 (BMP) or format 12 (full Unicode)."""
        h = self._data
        version, num_subtables = struct.unpack(">HH", h[cmap_off : cmap_off + 4])

        # Find best subtable: prefer format 12 (full), then format 4 (BMP)
        best_sub_off = 0
        best_format = 0

        for i in range(num_subtables):
            rec_off = cmap_off + 4 + i * 8
            platform_id, encoding_id, sub_offset = struct.unpack(
                ">HHI", h[rec_off : rec_off + 8]
            )
            sub_start = cmap_off + sub_offset
            fmt = struct.unpack(">H", h[sub_start : sub_start + 2])[0]

            # Prioritize Unicode cmaps
            if platform_id == 3 and encoding_id == 10 and fmt == 12:
                # Unicode full — best
                best_sub_off = sub_start
                best_format = 12
                break
            elif platform_id == 3 and encoding_id == 1 and fmt == 4:
                # Unicode BMP
                if best_format < 4:
                    best_sub_off = sub_start
                    best_format = 4
            elif platform_id == 0 and fmt == 4 and best_format == 0:
                best_sub_off = sub_start
                best_format = 4

        if best_format == 12 and best_sub_off:
            self._parse_cmap_format12(best_sub_off)
        elif best_format == 4 and best_sub_off:
            self._parse_cmap_format4(best_sub_off)

    def _parse_cmap_format4(self, off: int) -> None:
        """Parse format 4 cmap (BMP)."""
        h = self._data
        seg_count_x2 = struct.unpack(">H", h[off + 6 : off + 8])[0]
        seg_count = seg_count_x2 // 2

        end_codes = []
        pos = off + 14
        for _ in range(seg_count):
            end_codes.append(struct.unpack(">H", h[pos : pos + 2])[0])
            pos += 2
        pos += 2  # reservedPad

        start_codes = []
        for _ in range(seg_count):
            start_codes.append(struct.unpack(">H", h[pos : pos + 2])[0])
            pos += 2

        id_deltas = []
        for _ in range(seg_count):
            id_deltas.append(struct.unpack(">h", h[pos : pos + 2])[0])
            pos += 2

        id_range_offsets_start = pos
        id_range_offsets = []
        for _ in range(seg_count):
            id_range_offsets.append(struct.unpack(">H", h[pos : pos + 2])[0])
            pos += 2

        for seg_idx in range(seg_count):
            start = start_codes[seg_idx]
            end = end_codes[seg_idx]
            delta = id_deltas[seg_idx]
            range_off = id_range_offsets[seg_idx]

            if range_off == 0:
                for cp in range(start, end + 1):
                    gid = (cp + delta) & 0xFFFF
                    self._glyph_map[cp] = gid
            else:
                for cp in range(start, end + 1):
                    roff = id_range_offsets_start + seg_idx * 2
                    glyph_addr = roff + range_off + (cp - start) * 2
                    gid = struct.unpack(">H", h[glyph_addr : glyph_addr + 2])[0]
                    if gid != 0:
                        gid = (gid + delta) & 0xFFFF
                    self._glyph_map[cp] = gid

    def _parse_cmap_format12(self, off: int) -> None:
        """Parse format 12 cmap (full Unicode)."""
        h = self._data
        # Format 12 header: format(2), reserved(2), length(4), language(4), numGroups(4)
        num_groups = struct.unpack(">I", h[off + 12 : off + 16])[0]
        pos = off + 16  # start of groups
        for _ in range(num_groups):
            start_char, end_char, start_glyph = struct.unpack(">III", h[pos : pos + 12])
            pos += 12
            for cp in range(start_char, end_char + 1):
                self._glyph_map[cp] = start_glyph + (cp - start_char)

    def _parse_hmtx(self, hmtx_off: int, num_hmetrics: int) -> None:
        """Parse hmtx table for advance widths."""
        h = self._data
        # hMetrics (advanceWidth + lsb)
        last_advance = 0
        for i in range(num_hmetrics):
            pos = hmtx_off + i * 4
            advance = struct.unpack(">H", h[pos : pos + 2])[0]
            self._glyph_widths[i] = advance
            last_advance = advance

        # Infer remaining glyphs (they share last advance width)
        # Estimate total glyphs from max glyph_id in cmap
        if self._glyph_map:
            max_glyph = max(self._glyph_map.values())
            for gid in range(num_hmetrics, max_glyph + 1):
                self._glyph_widths[gid] = last_advance

    def has_cjk(self, text: str) -> bool:
        """Check if text contains any character outside ASCII printable range."""
        for ch in text:
            cp = ord(ch)
            if cp > 127:
                return True
        return False

    def glyph(self, char: str) -> int:
        """Get glyph index for a Unicode character (returns 0 for .notdef)."""
        return self._glyph_map.get(ord(char), 0)

    def width(self, glyph_id: int) -> int:
        """Get advance width in font units for a glyph."""
        return self._glyph_widths.get(glyph_id, 500)

    @property
    def units_per_em(self) -> int:
        return self._units_per_em

    @property
    def ascent(self) -> int:
        return self._ascent

    @property
    def descent(self) -> int:
        return self._descent

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return self._bbox

    @property
    def font_data(self) -> bytes:
        return self._data


# ── Minimal PDF writer ────────────────────────────────────────────────

class SimplePDF:
    """Write a basic text PDF using raw PDF format. Supports:
    - Headings (#, ##, ###)
    - Bold (**text**), italic (*text*), code (`text`)
    - Unordered lists (- / *)
    - Paragraphs
    - Page breaks (---)
    - Chinese/CJK via embedded TrueType font (auto-detected)
    """

    # CJK font tag used in page Resources
    CJK_FONT_TAG = "/FC1"

    _FONT_PATH = _find_cjk_font()

    def __init__(self):
        self.objects: List[bytes] = []
        self.pages: List[int] = []
        self._font_registered = False
        self._cjk_registered = False
        self._cjk: Optional[TTFCJKFont] = None
        # Page resource refs
        self.font_normal = 0
        self.font_bold = 0
        self.font_italic = 0
        self.font_mono = 0
        self.font_cjk = 0  # embedded CJK font object ref
        self._tounicode_obj = 0  # ToUnicode CMap ref

    def _obj(self, data: bytes) -> int:
        self.objects.append(data)
        return len(self.objects)

    def _write_header(self) -> bytes:
        return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"

    def _build_font_obj(self) -> int:
        return self._obj(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        )

    def _build_font_bold_obj(self) -> int:
        return self._obj(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\n"
        )

    def _build_font_italic_obj(self) -> int:
        return self._obj(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >>\n"
        )

    def _build_font_mono_obj(self) -> int:
        return self._obj(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\n"
        )

    def _register_cjk_font(self) -> None:
        """Embed CJK TrueType font into the PDF."""
        if self._cjk_registered or not self._FONT_PATH:
            return

        self._cjk = TTFCJKFont(self._FONT_PATH)
        font_bytes = self._cjk.font_data

        # ── FontFile2 stream (the TTF itself) ──
        compressed = zlib.compress(font_bytes)
        length_obj = self._obj(f"{len(compressed)}\n".encode())
        fontfile_obj = self._obj(
            f"<< /Length {length_obj} 0 R /Filter /FlateDecode /Length1 {len(font_bytes)} >>\n"
            f"stream\n".encode()
            + compressed
            + b"\nendstream\n"
        )

        # ── FontDescriptor ──
        bbox = self._cjk.bbox
        desc = (
            f"<< /Type /FontDescriptor\n"
            f"   /FontName /CJKFont\n"
            f"   /Flags 4\n"
            f"   /FontBBox [{bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}]\n"
            f"   /ItalicAngle 0\n"
            f"   /Ascent {self._cjk.ascent}\n"
            f"   /Descent {self._cjk.descent}\n"
            f"   /CapHeight {self._cjk.ascent}\n"
            f"   /StemV 80\n"
            f"   /FontFile2 {fontfile_obj} 0 R\n"
            f">>\n"
        )
        font_desc_obj = self._obj(desc.encode())

        # ── ToUnicode CMap ──
        tounicode_stream = (
            b"/CIDInit /ProcSet findresource begin\n"
            b"12 dict begin\n"
            b"begincmap\n"
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            b"/CMapName /CJKToUnicode def\n"
            b"/CMapType 2 def\n"
            b"1 begincodespacerange\n"
            b"<0000> <FFFF>\n"
            b"endcodespacerange\n"
            # Simple: map each glyph's Unicode back
        )
        # Build bfrange: map glyph range to Unicode
        bfrange_parts = []
        # Collect glyph→unicode inverse mapping
        glyph_to_unicode: Dict[int, List[int]] = {}
        for cp, gid in self._cjk._glyph_map.items():
            if gid not in glyph_to_unicode:
                glyph_to_unicode[gid] = []
            glyph_to_unicode[gid].append(cp)

        # Build bfchar entries for each glyph
        bfchars = []
        for gid, cps in sorted(glyph_to_unicode.items()):
            cp = cps[0]  # take first mapping
            if cp <= 0xFFFF:
                bfchars.append(f"<{gid:04X}> <{cp:04X}>")
        if bfchars:
            tounicode_stream += f"{len(bfchars)} beginbfchar\n".encode()
            for entry in bfchars:
                tounicode_stream += (entry + "\n").encode()
            tounicode_stream += b"endbfchar\n"

        tounicode_stream += b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
        self._tounicode_obj = self._obj(
            b"<< /Length "
            + str(len(tounicode_stream)).encode()
            + b" >>\nstream\n"
            + tounicode_stream
            + b"\nendstream\n"
        )

        # ── Font object ──
        # Use all glyph widths for FirstChar/LastChar/Widths
        if self._cjk._glyph_map:
            min_gid = min(self._cjk._glyph_map.values())
            max_gid = max(self._cjk._glyph_map.values())
            widths = []
            for gid in range(min_gid, max_gid + 1):
                w = self._cjk.width(gid)
                # Scale from font units to PDF units (1000 = standard)
                w_scaled = int(w * 1000 / self._cjk.units_per_em)
                widths.append(str(w_scaled))
            widths_str = " ".join(widths)
        else:
            min_gid = 0
            max_gid = 0
            widths_str = "500"

        font_obj = (
            f"<< /Type /Font\n"
            f"   /Subtype /TrueType\n"
            f"   /BaseFont /CJKFont\n"
            f"   /FirstChar {min_gid}\n"
            f"   /LastChar {max_gid}\n"
            f"   /Widths [{widths_str}]\n"
            f"   /FontDescriptor {font_desc_obj} 0 R\n"
            f"   /ToUnicode {self._tounicode_obj} 0 R\n"
            f">>\n"
        )
        self.font_cjk = self._obj(font_obj.encode())
        self._cjk_registered = True

    def _needs_cjk(self, text: str) -> bool:
        """Check if text contains any non-ASCII characters."""
        for ch in text:
            if ord(ch) > 127:
                return True
        return False

    def add_page(self, text: str, title: str = "") -> None:
        """Add a page with markdown text."""
        if not self._font_registered:
            self.font_normal = self._build_font_obj()
            self.font_bold = self._build_font_bold_obj()
            self.font_italic = self._build_font_italic_obj()
            self.font_mono = self._build_font_mono_obj()
            self._font_registered = True

        # Auto-register CJK if needed
        title_and_text = (title or "") + text
        if not self._cjk_registered and self._needs_cjk(title_and_text):
            self._register_cjk_font()

        stream = self._render_markdown(text, title)
        compressed = zlib.compress(stream)

        stream_obj_num = len(self.objects) + 1
        length_obj = self._obj(f"{len(compressed)}\n".encode())

        stream_data = (
            f"<< /Length {stream_obj_num} 0 R /Filter /FlateDecode >>\n"
            f"stream\n"
        ).encode() + compressed + b"\nendstream\n"

        content_obj = self._obj(stream_data)

        # Page object — always include CJK font ref if registered (no-op if absent)
        font_resources = (
            f" /F1 {self.font_normal} 0 R"
            f" /F2 {self.font_bold} 0 R"
            f" /F3 {self.font_italic} 0 R"
            f" /F4 {self.font_mono} 0 R"
        )
        if self._cjk_registered and self.font_cjk:
            font_resources += f" {self.CJK_FONT_TAG} {self.font_cjk} 0 R"

        page = (
            f"<< /Type /Page /Parent 0 R\n"
            f"   /MediaBox [0 0 595 842]\n"
            f"   /Contents {content_obj} 0 R\n"
            f"   /Resources << /Font <<{font_resources} >> >>\n"
            f">>\n"
        ).encode()
        self.pages.append(self._obj(page))

    def _text_to_pdf_ops(
        self, txt: str, font_tag: str, size: float, x: float, y: float
    ) -> bytes:
        """Generate BT/ET text operators. Uses glyph-index hex for CJK, PDF string for ASCII."""
        ops = [b"BT\n"]
        ops.append(f"{font_tag} {size} Tf\n".encode())
        ops.append(f"1 0 0 1 {x} {y} Tm\n".encode())

        if font_tag == self.CJK_FONT_TAG and self._cjk:
            # Glyph-index hex string
            hex_parts = []
            for ch in txt:
                gid = self._cjk.glyph(ch)
                hex_parts.append(f"{gid:04X}")
            hex_str = "".join(hex_parts)
            ops.append(f"<{hex_str}> Tj\n".encode())
        else:
            # Standard PDF string
            escaped = txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({escaped}) Tj\n".encode())

        ops.append(b"ET\n")
        return b"".join(ops)

    def _pick_font_tag(self, seg_style: str, seg_text: str) -> str:
        """Pick font tag: prefer CJK if text has non-ASCII, else style-based."""
        if self._cjk and self._cjk.has_cjk(seg_text):
            return self.CJK_FONT_TAG
        return {"bold": "/F2", "italic": "/F3", "code": "/F4", "normal": "/F1"}.get(
            seg_style, "/F1"
        )

    def _text_width_estimate(self, txt: str, font_tag: str, size: float) -> float:
        """Estimate rendered text width."""
        if font_tag == self.CJK_FONT_TAG and self._cjk:
            total = 0.0
            for ch in txt:
                gid = self._cjk.glyph(ch)
                w = self._cjk.width(gid)
                total += w * size / self._cjk.units_per_em
            return total
        else:
            return len(txt) * size * 0.5

    def _render_markdown(self, text: str, title: str = "") -> bytes:
        """Render markdown text into PDF content stream operators."""
        lines = []
        margin_left = 50
        margin_top = 792
        margin_bottom = 50
        line_height = 14
        font_size_normal = 10
        font_size_h1 = 18
        font_size_h2 = 14
        font_size_h3 = 12
        font_size_code = 9

        y = margin_top - 40

        # Title
        if title:
            tag = self._pick_font_tag("bold", title)
            lines.append(self._text_to_pdf_ops(title, tag, font_size_h1, margin_left, y))
            y -= line_height + 8

        paragraphs = text.split("\n\n")
        for para in paragraphs:
            if not para.strip():
                continue
            if para.strip() == "---":
                continue

            for line in para.split("\n"):
                if y < margin_bottom:
                    break

                stripped = line.strip()
                if not stripped:
                    y -= line_height / 2
                    continue

                h1 = re.match(r"^# (.+)", stripped)
                h2 = re.match(r"^## (.+)", stripped)
                h3 = re.match(r"^### (.+)", stripped)

                if h1:
                    t = h1.group(1)
                    tag = self._pick_font_tag("bold", t)
                    lines.append(self._text_to_pdf_ops(t, tag, font_size_h1, margin_left, y))
                    y -= line_height + 4
                elif h2:
                    t = h2.group(1)
                    tag = self._pick_font_tag("bold", t)
                    lines.append(self._text_to_pdf_ops(t, tag, font_size_h2, margin_left, y))
                    y -= line_height + 3
                elif h3:
                    t = h3.group(1)
                    tag = self._pick_font_tag("bold", t)
                    lines.append(self._text_to_pdf_ops(t, tag, font_size_h3, margin_left, y))
                    y -= line_height + 2
                else:
                    segments = self._parse_inline(stripped)
                    x = margin_left
                    current_y = y
                    for seg_text, seg_style in segments:
                        tag = self._pick_font_tag(seg_style, seg_text)
                        size = font_size_code if seg_style == "code" else font_size_normal
                        lines.append(
                            self._text_to_pdf_ops(seg_text, tag, size, x, current_y)
                        )
                        x += self._text_width_estimate(seg_text, tag, size)
                    y -= line_height

        return b"\n".join(lines)

    def _parse_inline(self, text: str) -> List[tuple]:
        """Parse inline formatting: **bold**, *italic*, `code`."""
        segments = []
        i = 0
        while i < len(text):
            if text[i : i + 2] == "**":
                end = text.find("**", i + 2)
                if end > i:
                    segments.append((text[i + 2 : end], "bold"))
                    i = end + 2
                    continue
            if text[i] == "*" and text[i + 1 : i + 2] != "*":
                end = text.find("*", i + 1)
                if end > i:
                    segments.append((text[i + 1 : end], "italic"))
                    i = end + 1
                    continue
            if text[i] == "`":
                end = text.find("`", i + 1)
                if end > i:
                    segments.append((text[i + 1 : end], "code"))
                    i = end + 1
                    continue
            j = i
            while j < len(text) and text[j] not in "*`":
                j += 1
            segments.append((text[i:j], "normal"))
            i = j
        return segments

    def build(self) -> bytes:
        """Assemble the full PDF document."""
        kids = b" ".join(f"{p} 0 R".encode() for p in self.pages)
        pages_obj = self._obj(
            f"<< /Type /Pages /Kids [{kids.decode()}] /Count {len(self.pages)} >>\n".encode()
        )

        for i, obj in enumerate(self.objects):
            self.objects[i] = obj.replace(
                b"/Parent 0 R", f"/Parent {pages_obj} 0 R".encode()
            )

        catalog = self._obj(
            f"<< /Type /Catalog /Pages {pages_obj} 0 R >>\n".encode()
        )

        output = [self._write_header()]
        for i, obj in enumerate(self.objects, 1):
            output.append(f"{i} 0 obj\n".encode())
            output.append(obj)
            output.append(b"endobj\n")

        xref_offset = sum(len(o) for o in output)
        output.append(b"xref\n")
        output.append(f"0 {len(self.objects) + 1}\n".encode())
        output.append(b"0000000000 65535 f \n")
        byte_pos = len(self._write_header())
        for i in range(len(self.objects)):
            output.append(f"{byte_pos:010d} 00000 n \n".encode())
            obj_header = f"{i + 1} 0 obj\n".encode()
            obj_footer = b"endobj\n"
            byte_pos += len(obj_header) + len(self.objects[i]) + len(obj_footer)

        output.append(b"trailer\n")
        output.append(
            f"<< /Size {len(self.objects) + 1} /Root {catalog} 0 R >>\n".encode()
        )
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
    Full Chinese/CJK support via embedded TrueType font.

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

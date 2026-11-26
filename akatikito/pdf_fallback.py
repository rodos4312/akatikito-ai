"""Pure-Python fallback PDF vector renderer with embedded TrueType font & ToUnicode CMap."""
from __future__ import annotations

import io
import os
import struct
import textwrap
from pathlib import Path


def _find_unicode_ttf() -> Path | None:
    candidates: list[Path] = []
    windir = os.environ.get("WINDIR")
    if windir:
        wfonts = Path(windir) / "Fonts"
        for name in ("verdana.ttf", "arial.ttf", "tahoma.ttf", "segoeui.ttf"):
            candidates.append(wfonts / name)
    candidates.extend([
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ])
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _select_cmap_subtable(ttf_bytes: bytes, cmap_off: int) -> int | None:
    n_sub = struct.unpack_from(">H", ttf_bytes, cmap_off + 2)[0]
    sub_off = None
    best_prio = 99
    for i in range(n_sub):
        if cmap_off + 4 + (i + 1) * 8 > len(ttf_bytes):
            break
        plat, enc, rel = struct.unpack_from(">HHI", ttf_bytes, cmap_off + 4 + i * 8)
        cand = cmap_off + rel
        if cand + 14 > len(ttf_bytes):
            continue
        fmt = struct.unpack_from(">H", ttf_bytes, cand)[0]
        if (plat, enc) == (3, 1) and fmt == 4 and best_prio > 0:
            sub_off, best_prio = cand, 0
        elif (plat, enc) == (0, 3) and fmt == 4 and best_prio > 1:
            sub_off, best_prio = cand, 1
    return sub_off


def _build_char_to_gid(ttf_bytes: bytes, sub_off: int):
    _fmt, _length, _lang, seg_count_x2 = struct.unpack_from(">HHHH", ttf_bytes, sub_off)
    seg_count = seg_count_x2 // 2
    if sub_off + 16 + 8 * seg_count > len(ttf_bytes):
        return None
    end_codes = struct.unpack_from(f">{seg_count}H", ttf_bytes, sub_off + 14)
    start_codes = struct.unpack_from(f">{seg_count}H", ttf_bytes, sub_off + 16 + 2 * seg_count)
    id_deltas = struct.unpack_from(f">{seg_count}h", ttf_bytes, sub_off + 16 + 4 * seg_count)
    id_ro_base = sub_off + 16 + 6 * seg_count
    id_ros = struct.unpack_from(f">{seg_count}H", ttf_bytes, id_ro_base)

    def char_to_gid(ch: str) -> int:
        cp = ord(ch)
        if cp > 0xFFFF:
            return 0
        for idx in range(seg_count):
            s, e = start_codes[idx], end_codes[idx]
            if not (s <= cp <= e):
                continue
            if s == 0xFFFF and e == 0xFFFF:
                return 0
            ro = id_ros[idx]
            if ro == 0:
                return (cp + id_deltas[idx]) & 0xFFFF
            pos = id_ro_base + idx * 2 + ro + (cp - s) * 2
            if pos + 2 > len(ttf_bytes):
                return 0
            g = struct.unpack_from(">H", ttf_bytes, pos)[0]
            return (g + id_deltas[idx]) & 0xFFFF if g else 0
        return 0

    return char_to_gid


def _parse_ttf(ttf_bytes: bytes):
    if len(ttf_bytes) < 12:
        return None
    num_tables = struct.unpack_from(">H", ttf_bytes, 4)[0]
    if 12 + num_tables * 16 > len(ttf_bytes):
        return None
    tables = {}
    for i in range(num_tables):
        tag_b, _chk, off, ln = struct.unpack_from(">4sIII", ttf_bytes, 12 + i * 16)
        tables[tag_b.decode("latin1", errors="replace")] = (off, ln)
    if not {"head", "hhea", "hmtx", "cmap"} <= tables.keys():
        return None
    head_off, head_len = tables["head"]
    hhea_off, hhea_len = tables["hhea"]
    hmtx_off, hmtx_len = tables["hmtx"]
    cmap_off, cmap_len = tables["cmap"]
    if head_len < 44 or hhea_len < 36 or cmap_len < 4:
        return None
    units_per_em = struct.unpack_from(">H", ttf_bytes, head_off + 18)[0] or 1000
    bbox = struct.unpack_from(">4h", ttf_bytes, head_off + 36)
    ascent, descent = struct.unpack_from(">2h", ttf_bytes, hhea_off + 4)
    num_hmetrics = struct.unpack_from(">H", ttf_bytes, hhea_off + 34)[0]
    adv_widths = [struct.unpack_from(">H", ttf_bytes, hmtx_off + i * 4)[0] for i in range(min(num_hmetrics, hmtx_len // 4))]
    sub_off = _select_cmap_subtable(ttf_bytes, cmap_off)
    if sub_off is None:
        return None
    char_to_gid = _build_char_to_gid(ttf_bytes, sub_off)
    if char_to_gid is None:
        return None
    return {
        "units_per_em": units_per_em, "bbox": bbox, "ascent": ascent,
        "descent": descent, "adv_widths": adv_widths, "char_to_gid": char_to_gid,
    }


def _layout_pdf_pages(title, paragraphs, table) -> list[list[tuple[str, int, int, str]]]:
    lines_to_draw: list[tuple[str, int, str]] = []
    if title:
        for wrapped in textwrap.wrap(str(title), width=58) or [str(title)]:
            lines_to_draw.append((wrapped, 16, "title"))
        lines_to_draw.append(("", 8, "bar"))
    for p in (paragraphs or []):
        for wrapped in textwrap.wrap(str(p), width=82) or [""]:
            lines_to_draw.append((wrapped, 11, "body"))
        lines_to_draw.append(("", 7, "spacer"))
    if table and isinstance(table, list):
        for r_idx, row in enumerate(table):
            if isinstance(row, list):
                row_txt = " | ".join(str(c) for c in row)
                kind = "th" if r_idx == 0 else "td"
                for wrapped in textwrap.wrap(row_txt, width=82) or [""]:
                    lines_to_draw.append((wrapped, 10, kind))
    if not lines_to_draw:
        lines_to_draw.append((str(title or "Έγγραφο PDF"), 12, "body"))
    page_chunks: list[list[tuple[str, int, int, str]]] = [[]]
    y = 785
    for text_line, size, kind in lines_to_draw:
        if y < 55:
            page_chunks.append([])
            y = 785
        page_chunks[-1].append((text_line, size, y, kind))
        y -= size + 6
    return page_chunks


def _build_tounicode_cmap(used_gids: dict[int, int]) -> bytes:
    gid_pairs = sorted(used_gids.items())
    cmap_lines = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
        "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange",
    ]
    for idx in range(0, len(gid_pairs), 100):
        batch = gid_pairs[idx : idx + 100]
        cmap_lines.append(f"{len(batch)} beginbfchar")
        for gid, cp in batch:
            cmap_lines.append(f"<{gid:04X}> <{cp & 0xFFFF:04X}>")
        cmap_lines.append("endbfchar")
    if not gid_pairs:
        cmap_lines.extend(["1 beginbfchar", "<0000> <0020>", "endbfchar"])
    cmap_lines.extend(["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"])
    return "\n".join(cmap_lines).encode("ascii")


def _build_ttf_font_ref(writer, ttf_bytes: bytes, font_meta: dict, used_gids: dict[int, int]):
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    scale = 1000.0 / float(font_meta["units_per_em"] or 1000)
    adv_widths = font_meta["adv_widths"] or [600]
    w_entries = ArrayObject()
    for gid in sorted(used_gids):
        raw_w = adv_widths[min(gid, len(adv_widths) - 1)]
        w_entries.append(NumberObject(gid))
        w_entries.append(ArrayObject([NumberObject(int(round(raw_w * scale)))]))
    font_file2 = DecodedStreamObject()
    font_file2.set_data(ttf_bytes)
    font_file2 = font_file2.flate_encode()
    font_file2[NameObject("/Length1")] = NumberObject(len(ttf_bytes))
    ff2_ref = writer._add_object(font_file2)
    xmin, ymin, xmax, ymax = font_meta["bbox"]
    ascent_s = int(round(font_meta["ascent"] * scale))
    descent_s = int(round(font_meta["descent"] * scale))
    bbox_arr = ArrayObject([NumberObject(int(round(v * scale))) for v in (xmin, ymin, xmax, ymax)])
    font_desc = DictionaryObject({
        NameObject("/Type"): NameObject("/FontDescriptor"),
        NameObject("/FontName"): NameObject("/AkatikitoSans"),
        NameObject("/Flags"): NumberObject(32),
        NameObject("/FontBBox"): bbox_arr,
        NameObject("/ItalicAngle"): NumberObject(0),
        NameObject("/Ascent"): NumberObject(ascent_s),
        NameObject("/Descent"): NumberObject(descent_s),
        NameObject("/CapHeight"): NumberObject(max(500, ascent_s)),
        NameObject("/StemV"): NumberObject(80),
        NameObject("/FontFile2"): ff2_ref,
    })
    tounicode = DecodedStreamObject()
    tounicode.set_data(_build_tounicode_cmap(used_gids))
    cidfont = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/CIDFontType2"),
        NameObject("/BaseFont"): NameObject("/AkatikitoSans"),
        NameObject("/CIDSystemInfo"): DictionaryObject({
            NameObject("/Registry"): NameObject("/Adobe"),
            NameObject("/Ordering"): NameObject("/Identity"),
            NameObject("/Supplement"): NumberObject(0),
        }),
        NameObject("/FontDescriptor"): writer._add_object(font_desc),
        NameObject("/CIDToGIDMap"): NameObject("/Identity"),
        NameObject("/DW"): NumberObject(600),
        NameObject("/W"): w_entries,
    })
    type0 = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type0"),
        NameObject("/BaseFont"): NameObject("/AkatikitoSans"),
        NameObject("/Encoding"): NameObject("/Identity-H"),
        NameObject("/DescendantFonts"): ArrayObject([writer._add_object(cidfont)]),
        NameObject("/ToUnicode"): writer._add_object(tounicode),
    })
    return writer._add_object(type0)


def _render_ttf_pages(writer, page_chunks, ttf_bytes: bytes, font_meta: dict) -> None:
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    char_to_gid = font_meta["char_to_gid"]
    fallback_gid = char_to_gid("?") or char_to_gid(" ") or 0
    used_gids: dict[int, int] = {}

    def map_text(text: str) -> str:
        hex_parts = []
        for ch in text:
            cp = ord(ch)
            gid = char_to_gid(ch)
            if gid == 0 and ch != "\0":
                gid = char_to_gid(" ") if ch.isspace() else fallback_gid
                cp_out = ord(" ") if ch.isspace() else (cp if cp <= 0xFFFF else ord("?"))
            else:
                cp_out = cp
            used_gids.setdefault(gid, cp_out)
            hex_parts.append(f"{gid:04X}")
        return "".join(hex_parts)

    pre_hex = [[(map_text(tl) if tl else "", sz, py, kind) for tl, sz, py, kind in chunk] for chunk in page_chunks]
    font_ref = _build_ttf_font_ref(writer, ttf_bytes, font_meta, used_gids)
    for chunk in pre_hex:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        ops = []
        for hex_str, size, py, kind in chunk:
            if kind == "bar":
                ops.append(f"q 0.23 0.51 0.96 RG 1.2 w 50 {py + 4} m 545 {py + 4} l S Q")
            elif hex_str:
                color = "0.118 0.227 0.541 rg" if kind in ("title", "th") else "0.10 0.13 0.17 rg"
                ops.append(f"BT {color} /F1 {size} Tf 50 {py} Td <{hex_str}> Tj ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)


def _render_helvetica_pages(writer, page_chunks) -> None:
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    base_font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    font_ref = writer._add_object(base_font)
    for chunk in page_chunks:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        ops = []
        for text_line, size, py, kind in chunk:
            if kind == "bar":
                ops.append(f"q 0.23 0.51 0.96 RG 1.2 w 50 {py + 4} m 545 {py + 4} l S Q")
            elif text_line:
                safe = (
                    text_line.encode("cp1252", errors="replace").decode("cp1252")
                    .replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                )
                ops.append(f"BT /F1 {size} Tf 50 {py} Td ({safe}) Tj ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("cp1252", errors="replace"))
        page[NameObject("/Contents")] = writer._add_object(stream)


def _render_fallback_pdf_bytes(title, paragraphs, table) -> bytes:
    """Pure-Python valid PDF fallback embedding a system TrueType font with visible vector glyphs and ToUnicode CMap."""
    from pypdf import PdfWriter

    page_chunks = _layout_pdf_pages(title, paragraphs, table)
    writer = PdfWriter()
    ttf_path = _find_unicode_ttf()
    ttf_bytes, font_meta = None, None
    if ttf_path is not None:
        try:
            ttf_bytes = ttf_path.read_bytes()
            font_meta = _parse_ttf(ttf_bytes)
        except OSError:
            ttf_bytes, font_meta = None, None
    if ttf_bytes is not None and font_meta is not None:
        _render_ttf_pages(writer, page_chunks, ttf_bytes, font_meta)
    else:
        _render_helvetica_pages(writer, page_chunks)
    writer.add_metadata({
        "/Title": str(title or "Έγγραφο PDF"),
        "/Subject": "\n".join(str(p) for p in (paragraphs or [])),
    })
    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue()

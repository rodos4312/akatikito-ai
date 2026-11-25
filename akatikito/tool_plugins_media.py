"""Media, PDF, Image, and ZIP built-in tool handlers extracted from tool_plugins.py."""
from __future__ import annotations

from pathlib import Path


def _guard_output_write(ws, target: Path, overwrite: bool) -> None:
    """Prevent accidental overwrite of existing files or live source files unless explicitly approved."""
    from .tools import _protect_live_source

    _protect_live_source(target)
    guard = getattr(ws, "write_guard", None)
    if callable(guard):
        guard(target, "", "")
    if target.exists():
        if not overwrite:
            raise FileExistsError(
                f"Το αρχείο '{ws.relative(target)}' υπάρχει ήδη. Διάλεξε νέο όνομα ή όρισε overwrite=true."
            )
        if target.is_file() and hasattr(ws, "backup"):
            ws.backup(target)


def tool_pdf_toolkit(
    ws,
    action: str = "info",
    paths: list[str] | None = None,
    output_path: str = "",
    pages: str = "",
    overwrite: bool = False,
) -> str:
    """Merge, split, or inspect PDF files safely inside the project workspace."""
    from pypdf import PdfReader, PdfWriter

    act = str(action or "info").strip().lower()
    src_list = [str(p).strip() for p in (paths or []) if str(p).strip()]
    if not src_list:
        raise ValueError("Δώσε τουλάχιστον μία διαδρομή PDF στο paths.")

    if act == "info":
        target = ws.resolve(src_list[0])
        if not target.is_file():
            raise ValueError(f"Δεν βρέθηκε το PDF: {src_list[0]}")
        reader = PdfReader(str(target))
        meta = reader.metadata or {}
        title = str(meta.get("/Title") or "—")
        return f"PDF {ws.relative(target)}: {len(reader.pages)} σελίδες · Τίτλος: {title} · Μέγεθος: {target.stat().st_size} bytes"

    if act == "merge":
        if len(src_list) < 2:
            raise ValueError("Για merge χρειάζονται τουλάχιστον 2 PDF αρχεία.")
        if not output_path or not output_path.strip():
            raise ValueError("Δώσε output_path για το ενωμένο PDF.")
        out = ws.resolve(output_path)
        _guard_output_write(ws, out, overwrite)
        writer = PdfWriter()
        total_pages = 0
        for item in src_list[:50]:
            p = ws.resolve(item)
            if not p.is_file():
                raise ValueError(f"Δεν βρέθηκε το PDF: {item}")
            reader = PdfReader(str(p))
            for pg in reader.pages:
                writer.add_page(pg)
                total_pages += 1
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as fh:
            writer.write(fh)
        return f"ΕΓΙΝΕ: ενώθηκαν {len(src_list)} PDF ({total_pages} σελίδες) στο {ws.relative(out)}"

    if act == "split":
        if not output_path or not output_path.strip():
            raise ValueError("Δώσε output_path για το νέο PDF.")
        target = ws.resolve(src_list[0])
        if not target.is_file():
            raise ValueError(f"Δεν βρέθηκε το PDF: {src_list[0]}")
        reader = PdfReader(str(target))
        n = len(reader.pages)
        selected: list[int] = []
        raw_pages = str(pages or f"1-{n}").strip()
        for part in raw_pages.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                if "-" in part:
                    a, b = part.split("-", 1)
                    if not a.strip() or not b.strip():
                        raise ValueError("missing bound")
                    start_i = int(a.strip())
                    end_i = int(b.strip())
                    if start_i < 1 or end_i < start_i:
                        raise ValueError("invalid bounds")
                    selected.extend(range(max(1, start_i), min(n, end_i) + 1))
                else:
                    idx = int(part)
                    if 1 <= idx <= n:
                        selected.append(idx)
            except ValueError as exc:
                raise ValueError(f"Μη έγκυρο εύρος σελίδων '{part}' (παράδειγμα: '1-3,5').") from exc
        if not selected:
            raise ValueError(f"Μη έγκυρο εύρος σελίδων (1–{n}).")
        writer = PdfWriter()
        for idx in selected:
            writer.add_page(reader.pages[idx - 1])
        out = ws.resolve(output_path)
        _guard_output_write(ws, out, overwrite)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as fh:
            writer.write(fh)
        return f"ΕΓΙΝΕ: εξήχθησαν {len(selected)} σελίδες από {ws.relative(target)} στο {ws.relative(out)}"

    raise ValueError("Μη έγκυρη ενέργεια pdf_toolkit (χρησιμοποίησε info, merge ή split).")


def tool_image_tools(
    ws,
    source_path: str,
    output_path: str = "",
    max_width: int = 0,
    max_height: int = 0,
    quality: int = 85,
    overwrite: bool = False,
) -> str:
    """Convert and/or resize project images (PNG/JPG/WebP) safely using Pillow."""
    from PIL import Image

    src = ws.resolve(source_path)
    if not src.is_file():
        raise ValueError(f"Δεν βρέθηκε η εικόνα: {source_path}")
    out = ws.resolve(output_path) if (output_path and output_path.strip()) else src.with_name(f"{src.stem}_edited{src.suffix}")
    _guard_output_write(ws, out, overwrite)
    q = max(10, min(100, int(quality or 85)))
    with Image.open(src) as img:
        orig_size = img.size
        if max_width > 0 or max_height > 0:
            mw = max_width if max_width > 0 else img.width
            mh = max_height if max_height > 0 else img.height
            img.thumbnail((mw, mh))
        suffix = out.suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            raise ValueError(f"Μη υποστηριζόμενος τύπος εικόνας '{suffix}'. Χρησιμοποίησε .png, .jpg ή .webp.")
        out.parent.mkdir(parents=True, exist_ok=True)
        if suffix in (".jpg", ".jpeg"):
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            img.save(out, format="JPEG", quality=q, optimize=True)
        elif suffix == ".webp":
            img.save(out, format="WEBP", quality=q)
        else:
            img.save(out, format="PNG", optimize=True)
        new_size = img.size
    return f"ΕΓΙΝΕ: {ws.relative(src)} ({orig_size[0]}x{orig_size[1]}) → {ws.relative(out)} ({new_size[0]}x{new_size[1]}, {out.stat().st_size} bytes)"


def tool_zip_manager(
    ws,
    action: str = "list",
    archive_path: str = "",
    paths: list[str] | None = None,
    destination: str = "",
    overwrite: bool = False,
) -> str:
    """Safely list, create, or extract ZIP archives inside the project (with Zip-Slip guard)."""
    import zipfile

    act = str(action or "list").strip().lower()
    if not archive_path or not archive_path.strip():
        raise ValueError("Δώσε διαδρομή αρχείου .zip στο archive_path.")
    arc = ws.resolve(archive_path)

    if act == "list":
        if not arc.is_file():
            raise ValueError(f"Δεν βρέθηκε το αρχείο ZIP: {archive_path}")
        with zipfile.ZipFile(arc, "r") as zf:
            infos = zf.infolist()
            lines = [f"ZIP {ws.relative(arc)} ({len(infos)} στοιχεία):"]
            for info in infos[:100]:
                lines.append(f"- {info.filename} ({info.file_size} bytes)")
            if len(infos) > 100:
                lines.append(f"... (+{len(infos) - 100} ακόμα)")
        return "\n".join(lines)

    if act == "create":
        items = [str(p).strip() for p in (paths or []) if str(p).strip()]
        if not items:
            raise ValueError("Δώσε paths για συμπίεση στο .zip.")
        _guard_output_write(ws, arc, overwrite)
        arc.parent.mkdir(parents=True, exist_ok=True)
        arc_resolved = arc.resolve()
        skip_parts = {".git", ".agent_backups", ".venv", "venv", "__pycache__", "node_modules"}
        count = 0
        with zipfile.ZipFile(arc, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in items[:500]:
                if count >= 2000:
                    break
                p = ws.resolve(item)
                if p.is_file():
                    if p.resolve() == arc_resolved or p.name == ".env" or any(sp in p.parts for sp in skip_parts):
                        continue
                    zf.write(p, arcname=ws.relative(p).replace("\\", "/"))
                    count += 1
                elif p.is_dir():
                    for child in p.rglob("*"):
                        if count >= 2000:
                            break
                        if not child.is_file():
                            continue
                        if child.resolve() == arc_resolved or child.name == ".env" or any(sp in child.parts for sp in skip_parts):
                            continue
                        zf.write(child, arcname=ws.relative(child).replace("\\", "/"))
                        count += 1
        return f"ΕΓΙΝΕ: δημιουργήθηκε το {ws.relative(arc)} ({count} αρχεία, {arc.stat().st_size} bytes)"

    if act == "extract":
        if not arc.is_file():
            raise ValueError(f"Δεν βρέθηκε το αρχείο ZIP: {archive_path}")
        dest_root = ws.resolve(destination or ".")
        dest_root.mkdir(parents=True, exist_ok=True)
        total_bytes = 0
        extracted = 0
        with zipfile.ZipFile(arc, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.file_size > 32 * 1024 * 1024 or total_bytes + info.file_size > 64 * 1024 * 1024:
                    raise ValueError("Το ZIP ξεπερνά το όριο ασφαλείας αποσυμπίεσης (64 MiB).")
                # Strict Zip-Slip guard using Path.relative_to (prevents out -> out2 prefix bypass!)
                target = ws.resolve(str((dest_root / info.filename).resolve()))
                try:
                    target.relative_to(dest_root.resolve())
                except ValueError as exc:
                    raise PermissionError(f"Μη ασφαλής διαδρομή μέσα στο ZIP: {info.filename}") from exc
                _guard_output_write(ws, target, overwrite)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src_f:
                    data = src_f.read(32 * 1024 * 1024 + 1)
                    if len(data) > 32 * 1024 * 1024 or total_bytes + len(data) > 64 * 1024 * 1024:
                        raise ValueError("Το ZIP ξεπερνά το όριο ασφαλείας αποσυμπίεσης (64 MiB).")
                    target.write_bytes(data)
                    total_bytes += len(data)
                    extracted += 1
        return f"ΕΓΙΝΕ: αποσυμπιέστηκαν {extracted} αρχεία ({total_bytes} bytes) στο {ws.relative(dest_root)}"

    raise ValueError("Μη έγκυρη ενέργεια zip_manager (list, create ή extract).")

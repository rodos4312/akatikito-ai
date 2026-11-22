"""Bounded, read-only previews of regular text files inside the active workspace."""
from __future__ import annotations
import hashlib
import stat
from .workspace import Workspace

MAX_FILE_BYTES = 4 * 1024 * 1024
PAGE_CHARS = 20000
SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.agent', '.agent_backups', '.pytest_cache'}


def list_tree(root, path='.', offset=0, limit=100, version='', max_entries=None):
    import json
    import os
    import time
    from .local_store import checked

    ws = Workspace(root)
    base = ws.resolve(path or '.')
    checked(base)
    if not base.is_dir():
        raise ValueError('Ο φάκελος δεν βρέθηκε.')
    if max_entries is not None:
        limit = max(1, min(500, int(max_entries)))
    else:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('Μη έγκυρη σελιδοποίηση φακέλου.')
    started = time.monotonic()
    rows = []
    partial = False
    scanned = 0
    reparse = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    with os.scandir(base) as entries:
        for entry in entries:
            scanned += 1
            if scanned > 10000 or time.monotonic() - started > 2:
                partial = True
                break
            try:
                info = entry.stat(follow_symlinks=False)
                if (
                    entry.name in SKIP_DIRS
                    or stat.S_ISLNK(info.st_mode)
                    or (reparse and getattr(info, 'st_file_attributes', 0) & reparse)
                ):
                    continue
                is_dir = stat.S_ISDIR(info.st_mode)
                if not is_dir and not stat.S_ISREG(info.st_mode):
                    continue
                rows.append({
                    'name': entry.name,
                    'path': ws.relative(base / entry.name).replace('\\', '/'),
                    'type': 'dir' if is_dir else 'file',
                    'size': 0 if is_dir else info.st_size,
                })
            except OSError:
                continue
    rows.sort(key=lambda x: (x['type'] != 'dir', x['name'].casefold(), x['name']))
    revision = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if version and revision != version:
        raise ValueError('Ο φάκελος άλλαξε. Ανανέωσε την προβολή.')
    rel_base = ws.relative(base).replace('\\', '/')
    page = rows[offset : offset + limit]
    next_offset = offset + limit if offset + limit < len(rows) else None
    return {
        'path': '.' if rel_base == '.' else rel_base,
        'entries': page,
        'version': revision,
        'offset': offset,
        'next': next_offset,
        'total': len(rows),
        'partial': partial,
        'truncated': partial or next_offset is not None,
    }


def preview(root, path, offset=0, version=''):
    if not isinstance(path, str) or not path.strip():
        raise ValueError('Διάλεξε ένα αρχείο του project.')
    if type(offset) is not int or offset < 0:
        raise ValueError('Μη έγκυρη θέση προεπισκόπησης.')
    ws = Workspace(root)
    target = ws.resolve(path)
    relative = ws.relative(target).replace('\\', '/')
    if ':' in relative or not stat.S_ISREG(target.stat().st_mode):
        raise ValueError('Η προεπισκόπηση υποστηρίζει μόνο κανονικά αρχεία του project.')
    with target.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Το αρχείο ξεπερνά το όριο προεπισκόπησης των 4 MiB.')
    digest = hashlib.sha256(raw).hexdigest()
    if version and version != digest:
        raise ValueError('Το αρχείο άλλαξε. Πάτησε Ανανέωση για να διαβάσεις τη νέα έκδοση.')
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        encoding = 'utf-32'
    elif raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    else:
        encoding = 'utf-8-sig'
    try:
        text = raw.decode(encoding)
    except UnicodeError as exc:
        raise ValueError('Δεν υποστηρίζεται η κωδικοποίηση. Χρειάζεται αρχείο κειμένου UTF-8 ή UTF-16/32 με BOM.') from exc
    if any(ord(c) < 32 and c not in '\t\r\n' for c in text):
        raise ValueError('Το αρχείο είναι δυαδικό και δεν εμφανίζεται ως κείμενο.')
    if offset > len(text):
        raise ValueError('Η θέση προεπισκόπησης είναι έξω από το αρχείο.')
    end = min(offset + PAGE_CHARS, len(text))
    return {'path':relative, 'text':text[offset:end], 'encoding':encoding,
            'size':len(raw), 'offset':offset, 'next':end if end < len(text) else None,
            'total_chars':len(text), 'line_start':text.count('\n', 0, offset)+1,
            'line_end':text.count('\n', 0, end)+1, 'version':digest}


IMAGE_MIMES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
}
MAX_CSV_ROWS = 200
MAX_CSV_COLS = 40


def rich_preview(root, path, offset=0, version=''):
    """Rich preview supporting images (base64 data URI), CSV/TSV tables, Markdown, and plain text."""
    import base64
    import csv
    import io

    if not isinstance(path, str) or not path.strip():
        raise ValueError('Διάλεξε ένα αρχείο του project.')
    ws = Workspace(root)
    target = ws.resolve(path)
    relative = ws.relative(target).replace('\\', '/')
    if ':' in relative or not stat.S_ISREG(target.stat().st_mode):
        raise ValueError('Η προεπισκόπηση υποστηρίζει μόνο κανονικά αρχεία του project.')
    suffix = target.suffix.lower()

    if suffix in IMAGE_MIMES:
        with target.open('rb') as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError('Η εικόνα ξεπερνά το όριο προεπισκόπησης των 4 MiB.')
        digest = hashlib.sha256(raw).hexdigest()
        mime = IMAGE_MIMES[suffix]
        b64 = base64.b64encode(raw).decode('ascii')
        return {
            'path': relative,
            'kind': 'image',
            'mime': mime,
            'data_uri': f'data:{mime};base64,{b64}',
            'size': len(raw),
            'version': digest,
            'text': '',
            'offset': 0,
            'next': None,
            'total_chars': 0,
            'line_start': 1,
            'line_end': 1,
        }

    base = preview(root, path, offset=offset, version=version)
    if suffix in {'.csv', '.tsv'} and offset == 0:
        delim = '\t' if suffix == '.tsv' else ','
        reader = csv.reader(io.StringIO(base['text']), delimiter=delim)
        rows = []
        truncated = False
        for i, row in enumerate(reader):
            if i >= MAX_CSV_ROWS + 1:
                truncated = True
                break
            rows.append([cell[:200] for cell in row[:MAX_CSV_COLS]])
        headers = rows[0] if rows else []
        body = rows[1:] if len(rows) > 1 else []
        base.update({
            'kind': 'csv',
            'headers': headers,
            'rows': body,
            'csv_truncated': truncated or base.get('next') is not None,
        })
        return base

    if suffix in {'.md', '.markdown'}:
        base['kind'] = 'markdown'
        return base

    base['kind'] = 'text'
    return base

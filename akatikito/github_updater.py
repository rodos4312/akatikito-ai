"""Cryptographically verified GitHub Releases updater for Akatikito AI.

Security guarantees:
- Checks only the official GitHub Releases API over HTTPS.
- Downloads release assets only from trusted GitHub release hosts.
- Verifies downloaded ZIP against SHA256SUMS.txt AND verifies every extracted file against MANIFEST.json.
- Rejects symlinks, path traversal, or any attempt to touch user data/, .venv/, or node_modules/.
- Creates a full backup before applying and rolls back on any write error.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from .diagnostics import APP_VERSION

GITHUB_REPO = "rodos4312/akatikito-ai"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
TRUSTED_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
PROTECTED_TOP = {"data", ".venv", "venv", ".agent", ".agent_backups", ".git", "node_modules", "dist"}
PROTECTED_FILES = {"settings.json", "mcp.json", "channels.json", "scheduled.json", "profile.json", "skill_proposals.json"}
MAX_ZIP_BYTES = 128 * 1024 * 1024
MAX_SUMS_BYTES = 64 * 1024


def parse_version(tag: str) -> tuple[int, ...]:
    """Parse 'v2.1.0' or '2.1.0-beta' into comparable integer tuple (2, 1, 0)."""
    cleaned = str(tag or "").strip().lstrip("vV")
    nums = re.findall(r"\d+", cleaned.split("-")[0])
    return tuple(int(n) for n in nums[:4]) if nums else (0, 0, 0)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    return parse_version(latest) > parse_version(current)


def _validate_release_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in TRUSTED_HOSTS:
        raise ValueError(f"Μη αξιόπιστη διεύθυνση ενημέρωσης GitHub: {url}")
    if parsed.username or parsed.password:
        raise ValueError("Δεν επιτρέπονται credentials στο URL ενημέρωσης.")
    return parsed.geturl()


def _download_bytes(url: str, max_bytes: int, accept: str = "application/octet-stream", timeout: int = 25) -> bytes:
    """Download from trusted GitHub HTTPS URL following redirects only to trusted hosts."""
    current = _validate_release_url(url)

    class _SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _validate_release_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(_SafeRedirect())
    req = urllib.request.Request(
        current,
        headers={"User-Agent": f"Akatikito-Updater/{APP_VERSION}", "Accept": accept},
    )
    with opener.open(req, timeout=timeout) as resp:
        _validate_release_url(resp.geturl())
        buf = io.BytesIO()
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Το αρχείο ενημέρωσης ξεπερνά το επιτρεπτό όριο μεγέθους.")
            buf.write(chunk)
        return buf.getvalue()


def check_github_update(current_version: str = APP_VERSION, fetcher=None) -> dict:
    """Check GitHub Releases API for a newer version of Akatikito AI."""
    fetch = fetcher or _download_bytes
    raw = fetch(RELEASES_API, 512 * 1024, "application/vnd.github+json", 12)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise ValueError("Μη έγκυρη απάντηση από το GitHub Releases API.")
    tag = str(data["tag_name"]).strip()
    latest_ver = tag.lstrip("vV")
    assets = data.get("assets") or []
    zip_asset = next(
        (a for a in assets if isinstance(a, dict) and str(a.get("name", "")).endswith(".zip")),
        None,
    )
    sha_asset = next(
        (a for a in assets if isinstance(a, dict) and str(a.get("name", "")).upper() == "SHA256SUMS.TXT"),
        None,
    )
    zip_url = str(zip_asset.get("browser_download_url", "")) if zip_asset else ""
    sha_url = str(sha_asset.get("browser_download_url", "")) if sha_asset else ""
    if zip_url:
        _validate_release_url(zip_url)
    if sha_url:
        _validate_release_url(sha_url)
    return {
        "repo": GITHUB_REPO,
        "current_version": current_version,
        "latest_version": latest_ver,
        "tag": tag,
        "update_available": is_newer_version(latest_ver, current_version),
        "can_auto_install": bool(zip_url and sha_url),
        "release_url": str(data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"),
        "release_name": str(data.get("name") or tag),
        "release_notes": str(data.get("body") or "")[:2000],
        "published_at": str(data.get("published_at") or ""),
        "zip_name": str(zip_asset.get("name", "")) if zip_asset else "",
        "zip_url": zip_url,
        "sha_url": sha_url,
    }


def _parse_sha256sums(text: str, zip_name: str) -> str:
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[a-fA-F0-9]{64}", parts[0]):
            fname = parts[1].lstrip("*")
            if not zip_name or fname == zip_name:
                return parts[0].lower()
    raise ValueError("Δεν βρέθηκε έγκυρο SHA-256 στο SHA256SUMS.txt για το πακέτο ενημέρωσης.")


def _safe_rel_path(member: str) -> Path:
    norm = member.replace("\\", "/").strip("/")
    if not norm or norm.startswith("/") or ":" in norm:
        raise ValueError(f"Μη έγκυρη διαδρομή στο πακέτο ενημέρωσης: {member}")
    parts = norm.split("/")
    if parts[0] == "Akatikito":
        parts = parts[1:]
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"Απαγορευμένη διαδρομή στο πακέτο ενημέρωσης: {member}")
    if parts[0].lower() in PROTECTED_TOP or parts[-1].lower() in PROTECTED_FILES:
        raise PermissionError(f"Το πακέτο ενημέρωσης δεν επιτρέπεται να αγγίξει το {norm}.")
    return Path(*parts)


def apply_github_update(app_root: Path, zip_url: str, sha_url: str, zip_name: str = "", fetcher=None) -> dict:
    """Download, cryptographically verify, back up, and atomically apply a GitHub Release ZIP."""
    root = Path(app_root).resolve()
    fetch = fetcher or _download_bytes
    _validate_release_url(zip_url)
    _validate_release_url(sha_url)

    sums_raw = fetch(sha_url, MAX_SUMS_BYTES, "text/plain, */*", 15)
    expected_zip_sha = _parse_sha256sums(sums_raw.decode("utf-8", errors="strict"), zip_name)

    zip_bytes = fetch(zip_url, MAX_ZIP_BYTES, "application/octet-stream", 60)
    actual_zip_sha = hashlib.sha256(zip_bytes).hexdigest().lower()
    if actual_zip_sha != expected_zip_sha:
        raise ValueError("Αποτυχία ελέγχου ακεραιότητας SHA-256! Η ενημέρωση ακυρώθηκε για λόγους ασφαλείας.")

    staged: list[tuple[Path, bytes]] = []
    manifest_version = ""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        if len(zf.infolist()) > 10000 or sum(i.file_size for i in zf.infolist()) > 256 * 1024 * 1024:
            raise ValueError("Το πακέτο ενημέρωσης ξεπερνά τα όρια ασφαλούς αποσυμπίεσης.")
        manifest_entry = next((i for i in zf.infolist() if i.filename.replace("\\", "/").endswith("MANIFEST.json")), None)
        if manifest_entry is None:
            raise ValueError("Λείπει το MANIFEST.json από το πακέτο ενημέρωσης.")
        manifest = json.loads(zf.read(manifest_entry).decode("utf-8"))
        manifest_version = str(manifest.get("version") or "")
        expected_files = {str(r["path"]).replace("\\", "/"): str(r["sha256"]).lower() for r in (manifest.get("files") or [])}
        if not expected_files:
            raise ValueError("Κενό MANIFEST.json στο πακέτο ενημέρωσης.")

        seen_rel: set[str] = set()
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Reject Unix symlinks in ZIP external_attr
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise PermissionError("Δεν επιτρέπονται symlinks στο πακέτο ενημέρωσης.")
            if info.filename == manifest_entry.filename:
                continue
            rel = _safe_rel_path(info.filename)
            rel_posix = rel.as_posix()
            if rel_posix not in expected_files:
                raise ValueError(f"Μη καταγεγραμμένο αρχείο στο πακέτο ενημέρωσης: {rel_posix}")
            raw = zf.read(info)
            if hashlib.sha256(raw).hexdigest().lower() != expected_files[rel_posix]:
                raise ValueError(f"Αλλοιωμένο αρχείο στο πακέτο ενημέρωσης: {rel_posix}")
            staged.append((rel, raw))
            seen_rel.add(rel_posix)

        missing = set(expected_files) - seen_rel
        if missing:
            raise ValueError(f"Λείπουν αρχεία από το πακέτο ενημέρωσης: {', '.join(sorted(missing)[:5])}")

    # Backup existing files before mutating anything
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = root / ".agent_backups" / f"github-update-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    applied: list[tuple[Path, bytes | None]] = []
    try:
        for rel, new_bytes in staged:
            target = (root / rel).resolve()
            if not target.is_relative_to(root):
                raise PermissionError(f"Εκτός φακέλου εφαρμογής: {rel}")
            prev_bytes = target.read_bytes() if target.is_file() else None
            if prev_bytes is not None:
                b_target = backup_dir / rel
                b_target.parent.mkdir(parents=True, exist_ok=True)
                b_target.write_bytes(prev_bytes)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name("." + target.name + ".upd.tmp")
            try:
                tmp.write_bytes(new_bytes)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
            applied.append((target, prev_bytes))
    except Exception as exc:
        # Roll back every modified file cleanly
        for target, prev_bytes in reversed(applied):
            try:
                if prev_bytes is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(prev_bytes)
            except OSError:
                pass
        raise RuntimeError(f"Η ενημέρωση απέτυχε και αναιρέθηκε με ασφάλεια: {exc}") from exc

    return {
        "ok": True,
        "version": manifest_version,
        "updated_files": len(applied),
        "backup_dir": str(backup_dir),
        "sha256": actual_zip_sha,
        "restart_required": True,
    }


def restore_github_update_backup(app_root: Path, backup_dir: Path) -> dict:
    """Restore backed-up application files from a previous update without ever touching user data/."""
    root = Path(app_root).resolve()
    bdir = Path(backup_dir).resolve()
    if not bdir.is_dir():
        raise FileNotFoundError(f"Δεν βρέθηκε ο φάκελος αντιγράφου ασφαλείας: {bdir}")
    restored = 0
    for src in sorted(bdir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(bdir)
        if not rel.parts or rel.parts[0].lower() in PROTECTED_TOP or rel.name.lower() in PROTECTED_FILES:
            continue
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"Μη επιτρεπτή διαδρομή επαναφοράς: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name("." + target.name + ".rb.tmp")
        try:
            tmp.write_bytes(src.read_bytes())
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        restored += 1
    return {"ok": True, "restored_files": restored, "backup_dir": str(bdir)}

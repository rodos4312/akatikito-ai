"""Supply-chain policy checks: Actions SHA pinning, bounded deps, 7-day release age & OSV (Q7)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
MIN_RELEASE_AGE_DAYS = 7

_USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")
_SHA_REF_RE = re.compile(r"@[0-9a-f]{40}$")
_EXACT_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;#]+)")
_BOUNDED_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:==|>=[^,<]+,<)")
_AGE_OK_RE = re.compile(r"#\s*age-ok:\s*(.{3,})$")


def _clean_req_lines(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, spec_without_comment, raw_line) for non-empty requirement entries."""
    rows: list[tuple[int, str, str]] = []
    if not path.is_file():
        return rows
    for idx, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        spec = stripped.split("#", 1)[0].strip()
        if spec:
            rows.append((idx, spec, raw))
    return rows


def check_workflow_sha_pins(root: Path = ROOT) -> list[str]:
    """Ensure every GitHub Actions `uses:` reference is pinned to a full 40-char commit SHA."""
    wf_dir = root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    errors: list[str] = []
    for wf in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        rel = wf.relative_to(root).as_posix()
        for idx, line in enumerate(wf.read_text(encoding="utf-8-sig").splitlines(), 1):
            m = _USES_RE.match(line)
            if not m:
                continue
            ref = m.group(1)
            if ref.startswith("./"):
                continue
            if not _SHA_REF_RE.search(ref):
                errors.append(
                    f"[SC-SHA] {rel}:{idx} uses floating action '{ref}'. "
                    "Fix: pin to a full 40-char commit SHA (e.g. actions/checkout@<40-hex>  # v4)."
                )
    return errors


def check_requirement_bounds(root: Path = ROOT) -> list[str]:
    """Verify exact == pins in requirements-dev/features and upper bounds in requirements.txt."""
    errors: list[str] = []
    for fname in ("requirements-dev.txt", "requirements-features.txt"):
        for idx, spec, _ in _clean_req_lines(root / fname):
            if not _EXACT_PIN_RE.match(spec):
                errors.append(
                    f"[SC-PIN] {fname}:{idx} '{spec}' is not exact-pinned with '=='. "
                    "Fix: pin an exact tested version (pkg==X.Y.Z)."
                )
    for idx, spec, _ in _clean_req_lines(root / "requirements.txt"):
        if not _BOUNDED_RE.match(spec):
            errors.append(
                f"[SC-PIN] requirements.txt:{idx} '{spec}' lacks an upper major version cap. "
                "Fix: bound the major version (e.g. pkg>=1.0,<2) or pin with '=='."
            )
    return errors


def _default_pypi_fetch(pkg: str, ver: str) -> datetime | None:
    import httpx

    resp = httpx.get(f"https://pypi.org/pypi/{pkg}/{ver}/json", timeout=15.0)
    if resp.status_code != 200:
        return None
    urls = resp.json().get("urls") or []
    times = []
    for item in urls:
        ts = item.get("upload_time_iso_8601")
        if ts:
            times.append(datetime.fromisoformat(str(ts).replace("Z", "+00:00")))
    return min(times) if times else None


def check_release_age(
    root: Path = ROOT,
    *,
    min_days: int = MIN_RELEASE_AGE_DAYS,
    now: datetime | None = None,
    fetcher: Callable[[str, str], datetime | None] | None = None,
) -> list[str]:
    """Flag exact-pinned releases newer than min_days unless overridden with '# age-ok: <reason>'."""
    ref_now = now or datetime.now(timezone.utc)
    fetch = fetcher or _default_pypi_fetch
    errors: list[str] = []
    for fname in ("requirements-dev.txt", "requirements-features.txt"):
        for idx, spec, raw in _clean_req_lines(root / fname):
            m = _EXACT_PIN_RE.match(spec)
            if not m or _AGE_OK_RE.search(raw):
                continue
            pkg, ver = m.group(1), m.group(2)
            uploaded = fetch(pkg, ver)
            if uploaded is None:
                continue
            age_days = (ref_now - uploaded).total_seconds() / 86400.0
            if age_days < min_days:
                errors.append(
                    f"[SC-AGE] {fname}:{idx} {pkg}=={ver} is only {age_days:.1f} days old (min {min_days}d). "
                    "Fix: wait for the cooldown window or justify with '# age-ok: <reason>'."
                )
    return errors


def check_osv(root: Path = ROOT) -> list[str]:
    """Query OSV.dev batch API for known vulnerabilities across exact-pinned PyPI packages."""
    import httpx

    queries: list[dict] = []
    labels: list[str] = []
    for fname in ("requirements-dev.txt", "requirements-features.txt"):
        for _, spec, _ in _clean_req_lines(root / fname):
            m = _EXACT_PIN_RE.match(spec)
            if m:
                pkg, ver = m.group(1), m.group(2)
                queries.append({"package": {"name": pkg, "ecosystem": "PyPI"}, "version": ver})
                labels.append(f"{pkg}=={ver}")
    if not queries:
        return []
    resp = httpx.post("https://api.osv.dev/v1/querybatch", json={"queries": queries}, timeout=20.0)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    errors: list[str] = []
    for label, entry in zip(labels, results):
        vulns = entry.get("vulns") or []
        if vulns:
            ids = ", ".join(str(v.get("id") or "?") for v in vulns[:5])
            errors.append(
                f"[SC-OSV] {label} has known OSV advisories: {ids}. "
                "Fix: upgrade to a patched version and update the pinned requirement."
            )
    return errors


def run_offline_checks(root: Path = ROOT) -> tuple[bool, list[str]]:
    errors = check_workflow_sha_pins(root) + check_requirement_bounds(root)
    return (len(errors) == 0), errors


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:  # quality: allow S110 — optional reconfigure on non-TTY pipe
            pass
    parser = argparse.ArgumentParser(description="Supply-chain policy checks (QUALITY-PLAN-2 Q7)")
    parser.add_argument("--release-age", action="store_true", help="Check PyPI 7-day release age (requires network)")
    parser.add_argument("--osv", action="store_true", help="Query OSV.dev for known advisories (requires network)")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args(argv)

    ok, errors = run_offline_checks()
    if args.release_age:
        errors.extend(check_release_age())
    if args.osv:
        errors.extend(check_osv())
    ok = len(errors) == 0
    if args.json:
        print(json.dumps({"ok": ok, "errors": errors}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    if not ok:
        print("SUPPLY CHAIN CHECK FAILED:")
        for err in errors:
            print("  -", err)
        return 1
    print("OK: supply-chain checks passed (SHA-pinned Actions, bounded/pinned requirements).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

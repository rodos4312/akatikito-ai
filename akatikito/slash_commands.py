"""Developer & workspace slash commands (/diff, /test, /review, plus /pin, /unpin, /pins).

Zero-token local commands (/diff, /test, /pin, /unpin, /pins) run without any model call
and strictly reuse Akatikito's existing git/command security guards.
Prompt-expanding commands (/review) turn a short slash trigger into a structured review brief.
"""
from __future__ import annotations

import re

from .context_pin import handle_pin_slash_command
from .tools import dispatch

_DIFF_RE = re.compile(r"^\s*/diff\b(.*)$", re.I | re.S)
_TEST_RE = re.compile(r"^\s*/test\b(.*)$", re.I | re.S)
_REVIEW_RE = re.compile(r"^\s*/review\b(.*)$", re.I | re.S)


def _handle_diff_command(agent, arg: str) -> str:
    rel = str(arg or "").strip().strip("\"'")
    out = dispatch(agent.ws, "git_status_diff", {"path": rel} if rel else {})
    header = f"🌿 **Git Diff**{f' (`{rel}`)' if rel else ''} *(0 tokens)*\n\n"
    return header + out


def _handle_test_command(agent, arg: str) -> str:
    extra = str(arg or "").strip()
    base_cmd = str(getattr(agent.config, "verify_command", "") or "").strip()
    if not base_cmd and not extra:
        return "⚠️ Δεν έχει οριστεί εντολή ελέγχου (`verify_command`) για αυτό το project. Δώσε π.χ. `/test python -m pytest -q`."
    command = f"{base_cmd} {extra}".strip() if (base_cmd and extra and not extra.startswith(("python", "pytest", "npm", "node", "cargo", "go "))) else (extra or base_cmd)
    out = dispatch(agent.ws, "run_command", {"command": command})
    ok = out.startswith("returncode=0")
    badge = "✅ **Tests πέρασαν**" if ok else "❌ **Tests απέτυχαν**"
    return f"{badge} (`{command}`) *(0 tokens)*\n\n{out}"


def handle_local_slash_command(agent, text: str) -> str | None:
    """Execute 0-token local slash commands (/pin, /unpin, /pins, /diff, /test)."""
    pin_reply = handle_pin_slash_command(agent, text)
    if pin_reply is not None:
        return pin_reply
    m_diff = _DIFF_RE.match(str(text or ""))
    if m_diff:
        return _handle_diff_command(agent, m_diff.group(1))
    m_test = _TEST_RE.match(str(text or ""))
    if m_test:
        return _handle_test_command(agent, m_test.group(1))
    return None


def expand_prompt_slash_command(text: str) -> tuple[str, bool]:
    """Expand prompt-driven slash commands (e.g. /review) into structured briefs.

    Returns (expanded_text, matched).
    """
    m_rev = _REVIEW_RE.match(str(text or ""))
    if not m_rev:
        return text, False
    focus = str(m_rev.group(1) or "").strip()
    scope = f" με έμφαση σε: {focus}" if focus else " στις τρέχουσες αλλαγές του project"
    expanded = (
        f"[ΕΝΤΟΛΗ CODE REVIEW /review] Κάνε αυστηρό, πρακτικό code review{scope}.\n"
        "1) Κάλεσε git_status_diff για να δεις ακριβώς τι άλλαξε και διάβασε τα αλλαγμένα αρχεία όπου χρειάζεται.\n"
        "2) Εντόπισε ΜΟΝΟ πραγματικά προβλήματα: bugs, edge cases, κενά ασφάλειας, race conditions, ελλείψεις σε tests.\n"
        "3) Μην κάνεις αλλαγές σε αρχεία — δώσε καθαρή αναφορά ανά αρχείο/γραμμή με σοβαρότητα και προτεινόμενη διόρθωση."
    )
    return expanded, True

"""Static security scanner for agent-proposed and imported skills (Category 4).

Inspired by MEU's ``tools/skills_guard.py``: scans skill markdown before saving or
proposing for exfiltration, prompt injection, destructive shell commands, agent
config tampering, and invisible-character obfuscation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillFinding:
    pattern_id: str
    severity: str       # "critical" | "high" | "medium"
    category: str       # "exfiltration" | "prompt_injection" | "destructive" | "config_persistence" | "obfuscation"
    line: int
    match: str
    description: str


@dataclass
class SkillScanResult:
    skill_name: str
    verdict: str        # "safe" | "caution" | "dangerous"
    findings: list[SkillFinding] = field(default_factory=list)
    summary: str = ""


_CONFIG_FILES = r"(?:settings\.json|AGENTS\.md|RULES\.md|CLAUDE\.md|\.akatikito[/\\][^\s\"']+)"

_PATTERNS: tuple[tuple[re.Pattern[str], str, str, str, str], ...] = (
    # --- Exfiltration ---
    (
        re.compile(r"(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)[^\n]*\|\s*(?:sh|bash|zsh|pwsh|powershell|python|node|iex)\b", re.I),
        "pipe_download_exec", "critical", "exfiltration",
        "Κατέβασμα και άμεση εκτέλεση script από το δίκτυο (curl/wget | shell).",
    ),
    (
        re.compile(r"(?:curl|wget|iwr|irm)[^\n]*(?:api[_-]?key|settings\.json|\.env\b|id_rsa|dpapi|secret|token)", re.I),
        "secret_exfil_http", "critical", "exfiltration",
        "Πιθανή αποστολή μυστικών ή API keys σε εξωτερικό URL.",
    ),
    (
        re.compile(r"base64\s+(?:-d|--decode)[^\n]*\|\s*(?:sh|bash|pwsh|powershell|python|node|iex)\b", re.I),
        "base64_pipe_exec", "critical", "exfiltration",
        "Αποκωδικοποίηση base64 με άμεση εκτέλεση σε shell.",
    ),
    # --- Prompt injection / guardrail bypass ---
    (
        re.compile(r"(?:ignore|disregard|override|forget)\s+(?:all\s+)?(?:previous|prior|above|system)\s+(?:instructions|rules|prompts|policies)", re.I),
        "prompt_override", "high", "prompt_injection",
        "Απόπειρα παράκαμψης οδηγιών συστήματος (prompt injection).",
    ),
    (
        re.compile(r"(?:bypass|disable|skip)\s+(?:owner\s+)?(?:approval|security|guardrails?|command\s+policy|restrictions)", re.I),
        "bypass_guardrails", "high", "prompt_injection",
        "Οδηγία για παράκαμψη μηχανισμών έγκρισης ή ασφάλειας.",
    ),
    # --- Destructive shell commands ---
    (
        re.compile(r"(?:^|[\s`\"';&|])(?:rm\s+-[a-z]*r[a-z]*f|rm\s+-rf\s+/|del\s+/[sqf]+|rd\s+/s\s+/q|remove-item\s+[^\n]*-recurse[^\n]*-force|format\s+[a-z]:|shutdown\s+/[sr])", re.I),
        "destructive_shell", "critical", "destructive",
        "Επικίνδυνη εντολή μαζικής διαγραφής ή τερματισμού συστήματος.",
    ),
    # --- Agent config persistence ---
    (
        re.compile(rf"(?:>>\s*|[\w\"'`)\]]\s*>\s*|\btee\b[^\n]*|\bsed\b[^\n]*-i[^\n]*|\b(?:cp|mv)\s+\S+\s+[^\n]{{0,30}})[~\w./\\-]*{_CONFIG_FILES}", re.I),
        "config_shell_write", "high", "config_persistence",
        "Απόπειρα τροποποίησης αρχείων ρυθμίσεων ή κανόνων του agent.",
    ),
    # --- Invisible / BiDi obfuscation ---
    (
        re.compile(r"[\u200b-\u200d\u202a-\u202e\u2066-\u2069]"),
        "invisible_unicode", "medium", "obfuscation",
        "Κρυφοί χαρακτήρες Unicode (zero-width ή BiDi override).",
    ),
)


def scan_skill_text(skill_name: str, text: str) -> SkillScanResult:
    """Run static security checks over a skill's markdown text."""
    findings: list[SkillFinding] = []
    seen: set[tuple[str, int]] = set()
    for lineno, line in enumerate(str(text or "").splitlines(), 1):
        for regex, pid, sev, cat, desc in _PATTERNS:
            m = regex.search(line)
            if m and (pid, lineno) not in seen:
                seen.add((pid, lineno))
                findings.append(SkillFinding(
                    pattern_id=pid,
                    severity=sev,
                    category=cat,
                    line=lineno,
                    match=m.group(0)[:100],
                    description=desc,
                ))
    if any(f.severity in ("critical", "high") for f in findings):
        verdict = "dangerous"
    elif findings:
        verdict = "caution"
    else:
        verdict = "safe"
    summary = (
        f"Εντοπίστηκαν {len(findings)} ευρήματα ασφάλειας ({verdict})."
        if findings
        else "Καθαρό (0 ευρήματα ασφάλειας)."
    )
    return SkillScanResult(skill_name=str(skill_name or ""), verdict=verdict, findings=findings, summary=summary)


def format_block_message(result: SkillScanResult) -> str:
    """Format a concise Greek rejection message explaining why a dangerous skill was blocked."""
    top = [f for f in result.findings if f.severity in ("critical", "high")][:3]
    details = "; ".join(f"[{f.pattern_id} L{f.line}] {f.description}" for f in top)
    return f"ΑΠΟΡΡΙΨΗ ΑΣΦΑΛΕΙΑΣ SKILL ({result.skill_name}): {details}"

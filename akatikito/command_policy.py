"""Approval classification for complete commands, not a shell sandbox."""
from __future__ import annotations

import re
import shlex
from pathlib import Path, PureWindowsPath

DANGEROUS_PROGRAMS = {
    "del", "erase", "rd", "rmdir", "rm", "ri", "remove-item", "format",
    "format-volume", "clear-disk", "diskpart", "mkfs", "dd", "reg", "regedit",
    "shutdown", "restart-computer", "stop-computer", "taskkill", "stop-process",
    "bcdedit", "cipher", "takeown", "icacls", "cacls", "setx", "runas", "sudo",
    "schtasks", "sc", "net", "netsh", "wmic", "vssadmin", "chmod", "chown",
    "set-executionpolicy", "mklink", "iex", "invoke-expression",
}
SHELLS = {"cmd", "powershell", "pwsh", "bash", "sh", "wsl"}
DESTRUCTIVE_GIT = {"clean", "reset", "push", "rebase", "restore", "prune",
                   "filter-branch", "filter-repo", "update-ref"}


def _program(token):
    name = token.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _outside(argument, root):
    value = argument.strip("\"'")
    if "=" in value and value.startswith("-"):
        value = value.split("=", 1)[1].strip("\"'")
    if ".." in value.replace("\\", "/").split("/"):
        return True
    if root is None:
        return False
    path = Path(value)
    win = PureWindowsPath(value)
    if win.drive and not win.is_absolute():
        return True
    if not (path.is_absolute() or win.is_absolute()):
        return False
    try:
        return not path.resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return True


_DANGEROUS_GIT_FLAGS: dict[str, set[str]] = {
    "checkout": {"--", "-f", "--force", "-b"},
    "switch": {"-c", "-f", "--force", "--discard-changes"},
    "branch": {"-d", "-f", "-m", "--delete", "--force", "--move"},
    "worktree": {"remove"},
    "reflog": {"expire", "delete"},
}


def _git_is_dangerous_verb(verb: str, flags: list[str]) -> bool:
    if verb in DESTRUCTIVE_GIT:
        return True
    flagged = _DANGEROUS_GIT_FLAGS.get(verb)
    if flagged and any(a in flagged for a in flags):
        return True
    return verb == "gc" and any(a.startswith("--prune") for a in flags)


def _git_risk(arguments):
    # -C changes directory, whereas -c can execute configured helpers/aliases.
    if any(a == "-c" or (a.startswith("-c") and len(a) > 2) or a in ("--config-env", "--exec-path")
           or a.startswith(("--config-env=", "--exec-path=")) for a in arguments):
        return "dangerous"
    rest = list(arguments)
    unknown = False
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        if option in ("-C", "--git-dir", "--work-tree", "--namespace") and rest:
            rest.pop(0)
        elif option.startswith(("--git-dir=", "--work-tree=", "--namespace=")) or option in ("--no-pager", "--no-optional-locks"):
            continue
        else:
            unknown = True
    if not rest:
        return "normal"
    verb, flags = rest[0].casefold(), [a.casefold() for a in rest[1:]]
    if _git_is_dangerous_verb(verb, flags):
        return "dangerous"
    if not unknown and verb in ("status", "log", "ls-files", "show", "diff") and not any(
            a.startswith(("--output", "--exec", "--ext-diff", "--textconv")) for a in flags):
        return "safe"
    return "normal"


def _split_command_groups(canonical: str) -> list[list[str]] | None:
    try:
        lexer = shlex.shlex(canonical, posix=False, punctuation_chars="&|;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = [p.strip("\"'") for p in lexer]
    except ValueError:
        return None
    groups, group = [], []
    for token in tokens:
        if token and set(token) <= set("&|;<>"):
            if group:
                groups.append(group)
            group = []
        else:
            group.append(token)
    if group:
        groups.append(group)
    return groups


def _group_is_dangerous(words: list[str], root) -> bool:
    program, args = _program(words[0]), [p.casefold() for p in words[1:]]
    if program in DANGEROUS_PROGRAMS or (program in SHELLS and args):
        return True
    if any(_outside(p, root) for p in words[1:]):
        return True
    if program == "git" and _git_risk(words[1:]) == "dangerous":
        return True
    if program in ("pip", "pip3", "npm", "pnpm", "yarn") and any(a in ("uninstall", "remove", "rm", "publish") for a in args):
        return True
    if program in ("python", "python3", "py") and args[:2] == ["-m", "pip"] and args[2:3] in (["uninstall"], ["remove"]):
        return True
    return program == "robocopy" and "/mir" in args


def _single_command_risk(words: list[str]) -> str:
    literal = words[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    program, args = _program(words[0]), [p.casefold() for p in words[1:]]
    if literal.endswith((".cmd", ".bat", ".ps1")):
        return "normal"
    if program == "git":
        return _git_risk(words[1:])
    if program in ("python", "python3", "py"):
        if args[:1] == ["-b"]:
            args = args[1:]
        if args[:2] in (["-m", "pytest"], ["-m", "unittest"]) or args[:3] == ["-m", "pip", "list"]:
            return "safe"
    if program in ("pip", "pip3") and args and args[0] in ("list", "show", "check"):
        return "safe"
    if program in ("dir", "ls", "pwd") and not any(a.startswith(("-exec", "--exec")) for a in args):
        return "safe"
    return "normal"


def command_risk(command, root=None):
    """Destructive commands never inherit autonomous or permanent permission."""
    text = str(command or "").strip()
    if not text or len(text) > 8192:
        return "dangerous"
    canonical = text.replace("^", "")
    if re.search(r"(?:^|[\s;&|])(?:del|erase|rd|rmdir|rm|ri|remove-item|format|reg|shutdown)(?:\s|/|$)", canonical, re.I):
        return "dangerous"
    if re.search(r"(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod).*\|.*(?:sh|bash|powershell|pwsh|iex|python|node)", canonical, re.I):
        return "dangerous"
    groups = _split_command_groups(canonical)
    if groups is None:
        return "normal"
    if any(_group_is_dangerous(words, root) for words in groups):
        return "dangerous"
    if len(groups) != 1 or any(c in text for c in "&|;<>\r\n$%^" + chr(96)):
        return "normal"
    return _single_command_risk(groups[0])

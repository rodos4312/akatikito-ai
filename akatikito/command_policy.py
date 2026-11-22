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


def _git_risk(arguments):
    # -C changes directory, whereas -c can execute configured helpers/aliases.
    if any(a == "-c" or a.startswith("-c") and len(a)>2 or a in ("--config-env", "--exec-path") or
           a.startswith(("--config-env=", "--exec-path=")) for a in arguments):
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
    if verb in DESTRUCTIVE_GIT:
        return "dangerous"
    if (verb == "checkout" and any(a in ("--", "-f", "--force", "-b") for a in flags)
            or verb == "switch" and any(a in ("-c", "-f", "--force", "--discard-changes") for a in flags)
            or verb == "branch" and any(a in ("-d", "-f", "-m", "--delete", "--force", "--move") for a in flags)
            or verb == "worktree" and "remove" in flags
            or verb == "reflog" and any(a in ("expire", "delete") for a in flags)
            or verb == "gc" and any(a.startswith("--prune") for a in flags)):
        return "dangerous"
    if not unknown and verb in ("status", "log", "ls-files", "show", "diff") and not any(
            a.startswith(("--output", "--exec", "--ext-diff", "--textconv")) for a in flags):
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
    try:
        lexer = shlex.shlex(canonical, posix=False, punctuation_chars="&|;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = [p.strip("\"'") for p in lexer]
    except ValueError:
        return "normal"
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
    for words in groups:
        program, args = _program(words[0]), [p.casefold() for p in words[1:]]
        if program in DANGEROUS_PROGRAMS or program in SHELLS and args:
            return "dangerous"
        if any(_outside(p, root) for p in words[1:]):
            return "dangerous"
        if program == "git" and _git_risk(words[1:]) == "dangerous":
            return "dangerous"
        if program in ("pip", "pip3", "npm", "pnpm", "yarn") and any(
                a in ("uninstall", "remove", "rm", "publish") for a in args):
            return "dangerous"
        if program in ("python","python3","py") and args[:2]==["-m","pip"] and args[2:3] in (["uninstall"],["remove"]):
            return "dangerous"
        if program == "robocopy" and "/mir" in args:
            return "dangerous"
    if len(groups) != 1 or any(c in text for c in "&|;<>\r\n$%^" + chr(96)):
        return "normal"
    words = groups[0]
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

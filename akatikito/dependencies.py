"""Resolve declared local imports; never guess from an unrelated basename."""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
import posixpath
import sys
import _imp

JS_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")


def edges(entries):
    entries = list(entries)
    paths = {name for name, _, _ in entries}
    modules = defaultdict(list)
    for name in paths:
        path = PurePosixPath(name)
        if path.suffix not in (".py", ".pyw"):
            continue
        parts = list(path.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            modules[".".join(parts)].append(name)

    def resolve(module, absolute=False):
        if not module:
            return None
        head = module.split(".", 1)[0]
        if absolute and (head in sys.builtin_module_names or _imp.is_frozen(head)):
            return None
        found = modules.get(module, ())
        return found[0] if len(found) == 1 else None

    result = set()
    for name, _, refs in entries:
        package = list(PurePosixPath(name).parent.parts)
        for ref in refs:
            targets = []
            if ref.startswith("py-import:"):
                targets.append(resolve(ref[len("py-import:"):], absolute=True))
            elif ref.startswith("py-from:"):
                try:
                    _, level, module, members = ref.split(":", 3)
                    level = int(level)
                except (ValueError, TypeError):
                    continue
                if level:
                    if level > len(package):
                        continue
                    base = package[:len(package)-level+1]
                    prefix = ".".join(base + ([module] if module else []))
                else:
                    prefix = module
                target = resolve(prefix, absolute=not level)
                targets.append(target)
                # Imported attributes of a normal .py module are not submodules.
                namespace = target is None and any(m.startswith(prefix+".") for m in modules)
                if target and PurePosixPath(target).stem == "__init__" or namespace:
                    for member in members.split(","):
                        if member and member != "*":
                            targets.append(resolve(prefix+"."+member, absolute=not level))
            elif ref.startswith("js:"):
                literal = ref[3:]
                if not literal.startswith("."):
                    continue  # package aliases/tsconfig paths need configuration
                base = posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent), literal))
                if base == ".." or base.startswith("../"):
                    continue
                candidates = [base]
                if not PurePosixPath(base).suffix:
                    candidates += [base+s for s in JS_SUFFIXES]
                    candidates += [base+"/index"+s for s in JS_SUFFIXES]
                found = [p for p in candidates if p in paths]
                if len(found) == 1:
                    targets.append(found[0])
            for target in targets:
                if target and target != name:
                    result.add((name, target))
    return result


def dependants(entries, shown=3):
    users = defaultdict(set)
    for source, target in edges(entries):
        users[target].add(source)
    return {target: "← " + ", ".join(sorted(who)[:shown]) +
            (f" +{len(who)-shown}" if len(who) > shown else "")
            for target, who in users.items()}

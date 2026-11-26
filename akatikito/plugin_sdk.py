"""Developer SDK for custom Akatikito plugins & skills (PRO-PLAN D5).

Provides:
- `scaffold_plugin`: creates a ready-to-test Channel API v1 plugin skeleton.
- `sign_plugin` / `verify_plugin_signature`: HMAC-SHA256 integrity signing over exact manifest files.
- `discover_dev_plugins`: loads unsigned plugins ONLY from an explicit dev folder AND ONLY when
  Advanced `plugin_sdk` is enabled, tagging unsigned plugins with a visible red badge.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from . import advanced
from .channel_plugins import ID, PluginError, fingerprint, manifest
from .local_store import save_json

SIGNATURE_FILE = "akatikito-signature.json"


def generate_signing_key() -> str:
    """Return a fresh 256-bit hex signing secret."""
    return secrets.token_hex(32)


def _read_plugin_parts(plugin_dir: Path) -> tuple[dict, dict[str, str]]:
    root = Path(plugin_dir).expanduser().resolve()
    manifest_path = root / "akatikito-plugin.json"
    if not manifest_path.is_file():
        raise PluginError("Λείπει το akatikito-plugin.json στον φάκελο του πρόσθετου.")
    raw_manifest = manifest_path.read_text(encoding="utf-8")
    info = manifest(raw_manifest)
    parts: dict[str, str] = {"akatikito-plugin.json": raw_manifest}
    for rel in info["files"]:
        target = (root / rel).resolve()
        if not target.is_file() or not target.is_relative_to(root):
            raise PluginError(f"Λείπει ή δεν είναι ασφαλές το αρχείο πρόσθετου: {rel}")
        parts[rel] = target.read_text(encoding="utf-8")
    return info, parts


def scaffold_plugin(parent_dir: str | Path, plugin_id: str, *, name: str = "") -> dict:
    """Generate a working Channel API v1 plugin skeleton with manifest, adapter, and test."""
    pid = str(plugin_id or "").strip().lower()
    if not ID.fullmatch(pid):
        raise PluginError("Μη έγκυρο plugin ID (πεζά γράμματα, ψηφία και '-', 2-40 χαρακτήρες).")
    parent = Path(parent_dir).expanduser().resolve()
    target = parent / pid
    if target.exists():
        raise FileExistsError(f"Ο φάκελος υπάρχει ήδη: {target}")
    target.mkdir(parents=True, exist_ok=False)

    title = (name or pid.replace("-", " ").title()).strip()
    manifest_obj = {
        "id": pid,
        "name": title,
        "version": "0.1.0",
        "kind": "channel",
        "api_version": 1,
        "entry": "adapter.py",
        "files": ["adapter.py", "README.md"],
        "permissions": ["outbound HTTPS only to configured service"],
    }
    (target / "akatikito-plugin.json").write_text(
        json.dumps(manifest_obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    adapter_code = (
        f'"""Channel API v1 adapter skeleton for {title}."""\n'
        "from __future__ import annotations\n\n"
        "from akatikito.channel_sdk import Inbound\n\n\n"
        "class Adapter:\n"
        "    def __init__(self, config: dict) -> None:\n"
        "        self.config = dict(config or {})\n"
        "        self.bot = ''\n\n"
        "    def connect(self) -> str:\n"
        f"        self.bot = '{pid}-bot'\n"
        "        return self.bot\n\n"
        "    def poll(self, cursor: str, stopped) -> tuple[str, list[Inbound]]:\n"
        "        return str(cursor or '0'), []\n\n"
        "    def send(self, event: Inbound, text: str) -> None:\n"
        "        _ = (event, text)\n\n"
        "    def close(self) -> None:\n"
        "        return None\n\n\n"
        "def create(config: dict) -> Adapter:\n"
        "    return Adapter(config)\n"
    )
    (target / "adapter.py").write_text(adapter_code, encoding="utf-8")
    (target / "README.md").write_text(f"# {title}\n\nCustom Akatikito Channel API v1 plugin (`{pid}`).\n", encoding="utf-8")
    test_code = (
        f'"""Contract test for {pid} plugin skeleton."""\n'
        "import importlib.util\n"
        "from pathlib import Path\n\n"
        "def test_adapter_contract():\n"
        "    root = Path(__file__).resolve().parent\n"
        "    spec = importlib.util.spec_from_file_location('dev_plugin', root / 'adapter.py')\n"
        "    mod = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(mod)\n"
        "    ad = mod.create({})\n"
        f"    assert ad.connect() == '{pid}-bot'\n"
        "    assert ad.poll('0', lambda: False) == ('0', [])\n"
        "    ad.close()\n"
    )
    (target / "test_plugin.py").write_text(test_code, encoding="utf-8")
    return {"id": pid, "path": str(target), "files": ["akatikito-plugin.json", "adapter.py", "README.md", "test_plugin.py"]}


def sign_plugin(plugin_dir: str | Path, secret_key_hex: str) -> dict:
    """Sign all manifest-declared plugin files with HMAC-SHA256 and write akatikito-signature.json."""
    key_str = str(secret_key_hex or "").strip()
    if len(key_str) < 16:
        raise ValueError("Χρειάζεται κλειδί υπογραφής τουλάχιστον 16 χαρακτήρων.")
    info, parts = _read_plugin_parts(Path(plugin_dir))
    digest = fingerprint(parts)
    key_id = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:12]
    sig = hmac.new(key_str.encode("utf-8"), digest.encode("utf-8"), hashlib.sha256).hexdigest()
    payload = {
        "algorithm": "hmac-sha256",
        "plugin_id": info["id"],
        "version": info["version"],
        "fingerprint": digest,
        "key_id": key_id,
        "signature": sig,
        "signed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_json(Path(plugin_dir).resolve() / SIGNATURE_FILE, payload)
    return payload


def verify_plugin_signature(plugin_dir: str | Path, secret_key_hex: str) -> tuple[bool, str]:
    """Verify plugin files against `akatikito-signature.json` using constant-time HMAC comparison."""
    root = Path(plugin_dir).expanduser().resolve()
    sig_path = root / SIGNATURE_FILE
    if not sig_path.is_file():
        return False, "μη υπογεγραμμένο"
    try:
        sig_doc = json.loads(sig_path.read_text(encoding="utf-8"))
        _, parts = _read_plugin_parts(root)
    except (OSError, ValueError, PluginError) as exc:
        return False, f"μη έγκυρη υπογραφή: {exc}"
    digest = fingerprint(parts)
    if sig_doc.get("fingerprint") != digest:
        return False, "τα αρχεία άλλαξαν μετά την υπογραφή"
    expected = hmac.new(str(secret_key_hex or "").encode("utf-8"), digest.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(sig_doc.get("signature") or ""), expected):
        return False, "η υπογραφή δεν ταιριάζει με το κλειδί"
    return True, "έγκυρη υπογραφή"


def discover_dev_plugins(dev_dir: str | Path, settings, *, trusted_key: str = "") -> list[dict]:
    """List plugins in a developer folder ONLY when Advanced `plugin_sdk` is enabled."""
    if not advanced.enabled(settings, "plugin_sdk"):
        raise PermissionError("Η λειτουργία «Δικά μου plugins και skills (SDK)» είναι κλειστή στο Advanced.")
    root = Path(dev_dir).expanduser().resolve()
    if not root.is_dir():
        return []
    found: list[dict] = []
    for manifest_file in sorted(root.glob("*/akatikito-plugin.json")):
        folder = manifest_file.parent
        try:
            info, parts = _read_plugin_parts(folder)
        except (OSError, PluginError):
            continue
        signed_ok = False
        reason = "μη υπογεγραμμένο"
        if trusted_key:
            signed_ok, reason = verify_plugin_signature(folder, trusted_key)
        found.append(
            {
                "id": info["id"],
                "name": info["name"],
                "version": info["version"],
                "path": str(folder),
                "fingerprint": fingerprint(parts),
                "unsigned": not signed_ok,
                "badge": "υπογεγραμμένο" if signed_ok else "μη υπογεγραμμένο",
                "status_detail": reason,
            }
        )
    return found

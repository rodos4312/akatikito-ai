"""OS-level secret encryption using Windows DPAPI (CryptProtectData / CryptUnprotectData).

Secrets are encrypted per Windows user account with application entropy and stored
with a 'dpapi:v1:' prefix. Legacy 'v1:' obfuscated values remain readable for seamless
transparent migration on next save.
"""
from __future__ import annotations

import base64
import ctypes
import os
import re

DPAPI_PREFIX = "dpapi:v1:"
_ENTROPY = b"Akatikito::DPAPI::Vault::v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


if os.name == "nt":
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _make_blob(data: bytes) -> tuple["_DATA_BLOB", ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def is_dpapi_available() -> bool:
    """Return True if Windows DPAPI (crypt32.CryptProtectData) is operational."""
    if os.name != "nt":
        return False
    try:
        out = dpapi_protect(b"probe")
        return dpapi_unprotect(out) == b"probe"
    except Exception:
        return False


def dpapi_protect(plaintext: bytes) -> bytes:
    """Encrypt bytes using Windows DPAPI bound to the current Windows user."""
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    in_blob, _in_buf = _make_blob(plaintext)
    ent_blob, _ent_buf = _make_blob(_ENTROPY)
    out_blob = _DATA_BLOB()

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "Akatikito Secret",
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(ciphertext: bytes) -> bytes:
    """Decrypt bytes previously encrypted via dpapi_protect under the same Windows user."""
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    in_blob, _in_buf = _make_blob(ciphertext)
    ent_blob, _ent_buf = _make_blob(_ENTROPY)
    out_blob = _DATA_BLOB()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def protect_text(plain: str) -> str | None:
    """Encrypt string to 'dpapi:v1:<b64>' if Windows DPAPI is available, else None."""
    if not plain:
        return ""
    if str(plain).startswith(DPAPI_PREFIX):
        return str(plain)
    if os.name != "nt":
        return None
    try:
        raw = dpapi_protect(plain.encode("utf-8"))
        return DPAPI_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")
    except Exception:
        return None


def unprotect_text(encoded: str) -> str:
    """Decrypt 'dpapi:v1:<b64>' string back to plaintext, returning '' on corruption."""
    if not encoded or not str(encoded).startswith(DPAPI_PREFIX):
        return ""
    try:
        raw = base64.urlsafe_b64decode(str(encoded)[len(DPAPI_PREFIX) :].encode("ascii"))
        return dpapi_unprotect(raw).decode("utf-8")
    except Exception:
        return ""


_GENERIC_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bntn_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bsecret_[A-Za-z0-9]{16,}\b"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{12,}"),
)


def redact_secrets(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """Scrub known secret tokens and common API-key patterns from diagnostic text."""
    out = str(text or "")
    for sec in extra_secrets:
        if sec and len(sec) >= 4 and sec in out:
            out = out.replace(sec, "***")
    for pat in _GENERIC_SECRET_PATTERNS:
        out = pat.sub(lambda m: (m.group(1) + "***") if m.lastindex else "***", out)
    return out

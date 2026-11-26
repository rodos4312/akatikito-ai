"""Tests for Q3 hidden-error rules registry, inline justifications, and per-file ratchet."""
from __future__ import annotations

from scripts.quality_rules import RULES, check_rules, has_justified_allow, write_baseline


def test_every_rule_has_actionable_fix_and_allow_requires_reason():
    assert len(RULES) >= 8
    for spec in RULES.values():
        assert spec.id and spec.title and len(spec.fix) >= 15
    assert has_justified_allow("except Exception:  # quality: allow BLE001 — UI boundary", "BLE001") is True
    assert has_justified_allow("except Exception:  # quality: allow BLE001", "BLE001") is False
    assert has_justified_allow("except Exception:  # quality: allow S110 — wrong rule", "BLE001") is False


def test_rules_catch_broad_except_silent_pass_timeout_ladder_path_and_test_sleep(tmp_path):
    (tmp_path / "akatikito").mkdir()
    (tmp_path / "tests").mkdir()
    mod = tmp_path / "akatikito" / "sample.py"
    tst = tmp_path / "tests" / "test_sample.py"
    base = tmp_path / "quality_baseline.json"

    mod.write_text("def clean():\n    return 1\n", encoding="utf-8")
    tst.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    write_baseline(tmp_path, base, include_ruff=False)
    ok, errs = check_rules(tmp_path, base, include_ruff=False, use_git_base=False)
    assert ok and not errs

    # Inject violations for BLE001, S110, AK-TIMEOUT, AK-IF-LADDER, AK-HARDCODED-PATH, AK-TEST-SLEEP
    mod.write_text(
        "import subprocess\n"
        "def bad(x):\n"
        "    try:\n"
        "        subprocess.run(['echo', 'hi'])\n"
        "    except Exception:\n"
        "        pass\n"
        "    if x == 'a': return 1\n"
        "    elif x == 'b': return 2\n"
        "    elif x == 'c': return 3\n"
        "    elif x == 'd': return 4\n"
        "    elif x == 'e': return 5\n"
        "    elif x == 'f': return 6\n"
        "    p = '/tmp/leak.txt'\n"
        "    return p\n",
        encoding="utf-8",
    )
    tst.write_text("import time\ndef test_slow():\n    time.sleep(0.1)\n", encoding="utf-8")

    ok2, errs2 = check_rules(tmp_path, base, include_ruff=False, use_git_base=False)
    assert not ok2
    joined = "\n".join(errs2)
    for expected in ("BLE001", "S110", "AK-TIMEOUT", "AK-IF-LADDER", "AK-HARDCODED-PATH", "AK-TEST-SLEEP"):
        assert expected in joined and "Fix:" in joined

    # Justified inline allows clear BLE001 and S110 cleanly
    mod.write_text(
        "def justified():\n"
        "    try:\n"
        "        return 1\n"
        "    except Exception:  # quality: allow BLE001 — top-level boundary\n"
        "        pass  # ignored\n",
        encoding="utf-8",
    )
    tst.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    ok3, errs3 = check_rules(tmp_path, base, include_ruff=False, use_git_base=False)
    assert not ok3 and all("[S110]" in e for e in errs3)

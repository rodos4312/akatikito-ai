"""Exercise the actual frontend logic locally, with no browser/provider."""
from pathlib import Path
import shutil
import subprocess


def test_learning_preview_approval_and_pagination():
    script=Path(__file__).with_suffix(".cjs")
    node=shutil.which("node")
    assert node,"Node is required to validate the frontend"
    result=subprocess.run([node,str(script)],capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert result.returncode==0,result.stdout+result.stderr

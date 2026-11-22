"""Run the file-sidebar UI contract as part of the standard verifier."""
import os, shutil, subprocess
from pathlib import Path

def test_files_sidebar_frontend():
    node=shutil.which('node')
    assert node, 'Node is required for the file-sidebar tests'
    result=subprocess.run([node,str(Path(__file__).with_suffix('.cjs'))],capture_output=True,text=True,
                          encoding='utf-8',timeout=30,env=os.environ.copy())
    assert result.returncode==0, result.stdout+result.stderr

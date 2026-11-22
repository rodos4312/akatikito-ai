import subprocess,sys
from pathlib import Path

def test_extended_ui_without_browser_or_model():
    result=subprocess.run(['node',str(Path(__file__).with_suffix('.cjs'))],capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr

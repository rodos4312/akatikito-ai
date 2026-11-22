import subprocess
from pathlib import Path


def test_voice_browser_handlers_without_real_microphone():
    result=subprocess.run(['node',str(Path(__file__).with_suffix('.cjs'))],capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stdout+result.stderr

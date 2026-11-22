import subprocess,shutil
from pathlib import Path
def test_gateway_menus_and_install_ui():
    result=subprocess.run([shutil.which("node"),str(Path(__file__).with_suffix(".cjs"))],capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert result.returncode==0,result.stdout+result.stderr

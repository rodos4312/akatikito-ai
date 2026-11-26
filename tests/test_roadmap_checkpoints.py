"""Snapshots do not touch user Git; undo is exact, bounded and conflict checked."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.checkpoints import Checkpoints
from akatikito.tools import write_file
from akatikito.workspace import Workspace
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel,tool_call


def test_snapshot_diff_and_undo_preserve_original_bytes_and_user_git(tmp_path):
    subprocess.run(['git','init',str(tmp_path)],capture_output=True,check=True)
    original_git={str(p.relative_to(tmp_path)):p.read_bytes() for p in (tmp_path/'.git').rglob('*') if p.is_file()}
    source=tmp_path/'a.py';original=b'\xef\xbb\xbfvalue=1\r\n';source.write_bytes(original)
    store=Checkpoints(tmp_path);key=store.begin()
    source.write_bytes(b'value=2\n');(tmp_path/'new.txt').write_text('new')
    store.finish(key)
    changes=store.diff(key)
    assert {x['path'] for x in changes}=={'a.py','new.txt'}
    assert any('+value=2' in x['diff'] for x in changes)
    assert store.undo(key)==['a.py','new.txt']
    assert source.read_bytes()==original and not (tmp_path/'new.txt').exists()
    assert original_git=={str(p.relative_to(tmp_path)):p.read_bytes() for p in (tmp_path/'.git').rglob('*') if p.is_file()}


def test_conflict_preflight_leaves_every_file_unchanged(tmp_path):
    a=tmp_path/'a.txt';b=tmp_path/'b.txt';a.write_text('old-a');b.write_text('old-b')
    store=Checkpoints(tmp_path);key=store.begin();a.write_text('new-a');b.write_text('new-b');store.finish(key)
    b.write_text('owner edit')
    with pytest.raises(ValueError,match='Σύγκρουση'):store.undo(key)
    assert a.read_text()=='new-a' and b.read_text()=='owner edit'


def test_deleted_files_and_changed_paths_can_be_restored(tmp_path):
    p=tmp_path/'old.bin';p.write_bytes(b'\x00original')
    store=Checkpoints(tmp_path);key=store.begin();p.unlink();store.finish(key)
    assert store.undo(key)==['old.bin'] and p.read_bytes()==b'\x00original'
    with pytest.raises(ValueError):store.load('../outside')


def test_checkpoint_budget_fails_visibly_instead_of_partial_snapshot(tmp_path,monkeypatch):
    import akatikito.checkpoints as module
    (tmp_path/'big').write_bytes(b'12345');monkeypatch.setattr(module,'MAX_FILE',4)
    with pytest.raises(ValueError,match='όριο'):Checkpoints(tmp_path).begin()


def test_agent_message_checkpoint_survives_a_new_store(tmp_path):
    model=ScriptedModel([{'tool_calls':[tool_call('write_file',path='a.txt',content='done')]},{'content':'done'}])
    agent=Akatikito(str(tmp_path),Config(checkpoints=True,auto_mode=False),model)
    assert agent.chat('Υλοποίησε το αρχείο')=='done'
    store=Checkpoints(tmp_path)
    assert store.diff(agent.last_checkpoint)[0]['path']=='a.txt'
    assert store.undo(agent.last_checkpoint)==['a.txt']


def test_file_tools_cannot_modify_user_git_or_snapshot_metadata(tmp_path):
    ws=Workspace(tmp_path)
    assert write_file(ws,'.git/config','bad').startswith('ΣΦΑΛΜΑ')
    assert write_file(ws,'.agent/checkpoints/fake.json','bad').startswith('ΣΦΑΛΜΑ')
    assert not (tmp_path/'.git/config').exists()


@pytest.mark.windows
def test_undo_rejects_junction_changed_since_snapshot(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'a.txt').write_text('old')
    store=Checkpoints(tmp_path);key=store.begin();(source/'a.txt').write_text('new');store.finish(key)
    original=tmp_path/'original';source.rename(original)
    made=subprocess.run(['cmd','/c','mklink','/J',str(source),str(original)],capture_output=True)
    assert made.returncode==0,made.stderr
    with pytest.raises(ValueError,match='σύνδεσμος'):store.undo(key)
    assert (original/'a.txt').read_text()=='new'


def test_api_undo_updates_agent_observation_and_does_not_call_model(tmp_path,monkeypatch):
    from akatikito import settings
    from akatikito.app_api import AppState
    from akatikito.sessions import SessionStore
    monkeypatch.setattr(settings,'DEFAULT_PATH',tmp_path/'settings.json')
    state=AppState();model=ScriptedModel([{'tool_calls':[tool_call('write_file',path='new.txt',content='new')]},{'content':'done'}])
    state.agent=Akatikito(str(tmp_path),Config(verify_command=' ',checkpoints=True),model);state.store=SessionStore(tmp_path)
    state.agent.chat('Γράψε new.txt');key=state.agent.last_checkpoint
    result=state.checkpoint_action(key,True)
    assert result['ok'] and not (tmp_path/'new.txt').exists() and model.calls==2
    assert 'ΑΝΑΙΡΕΣΗ' in state.agent.messages[-1]['content'] and state.agent.task.status=='active'


def test_undo_directory_conflict_and_rollback_leave_no_restore_files(tmp_path,monkeypatch):
    a=tmp_path/'a.txt';b=tmp_path/'b.txt';a.write_text('old-a');b.write_text('old-b')
    store=Checkpoints(tmp_path);key=store.begin();a.write_text('new-a');b.write_text('new-b');store.finish(key)
    import akatikito.checkpoints as module
    original=module.os.replace;calls=[]
    def fail_second(source,target):
        calls.append(target)
        if len(calls)==2:raise OSError('synthetic disk failure')
        return original(source,target)
    monkeypatch.setattr(module.os,'replace',fail_second)
    with pytest.raises(OSError):store.undo(key)
    assert a.read_text()=='new-a' and b.read_text()=='new-b' and not list(tmp_path.glob('.*.restore-*'))

    monkeypatch.setattr(module.os,'replace',original)
    b.unlink();b.mkdir()
    with pytest.raises(ValueError,match='Σύγκρουση'):store.undo(key)
    assert a.read_text()=='new-a' and b.is_dir()

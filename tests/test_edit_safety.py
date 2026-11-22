"""Regression coverage for the malformed patch that corrupted tools.py."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito.tools import edit_file,write_file,dispatch
from akatikito.workspace import Workspace
from akatikito.editing import EditError,commit
from akatikito import editing,workspace


def target(tmp_path,name='app.py',data=b'value = 1\n'):
    path=tmp_path/name;path.write_bytes(data)
    return Workspace(tmp_path),path


def test_real_extra_separator_shape_is_rejected_without_writing(tmp_path):
    ws,path=target(tmp_path,data=b'TOOL_SPECS = [\n    {"name":"list_files"}\n]\n')
    before=path.read_bytes();stamp=path.stat().st_mtime_ns
    patch='<<<<<<< SEARCH\nTOOL_SPECS = [\n=======\ndef new_tool():\n    return 1\n\nTOOL_SPECS = [\n=======\n>>>>>>> REPLACE'
    result=edit_file(ws,'app.py',patch)
    assert result.startswith('ΣΦΑΛΜΑ') and '=======' in result
    assert path.read_bytes()==before and path.stat().st_mtime_ns==stamp
    assert not (tmp_path/'.agent_backups').exists()


@pytest.mark.parametrize('tail',['\n<<<<<<< SEARCH\nmissing','\nUNCONSUMED TEXT'])
def test_valid_first_block_and_bad_remainder_are_not_partially_applied(tmp_path,tail):
    ws,path=target(tmp_path);before=path.read_bytes()
    patch='<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE'+tail
    assert edit_file(ws,'app.py',patch).startswith('ΣΦΑΛΜΑ')
    assert path.read_bytes()==before


def test_invalid_python_never_replaces_valid_source(tmp_path):
    ws,path=target(tmp_path);before=path.read_bytes()
    result=edit_file(ws,'app.py',edits=[{'search':'1','replace':'('}])
    assert result.startswith('ΣΦΑΛΜΑ') and 'ΠΡΙΝ' in result
    assert path.read_bytes()==before and not (tmp_path/'.agent_backups').exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_compiler_context_errors_are_caught_before_commit(tmp_path):
    ws,path=target(tmp_path,data=b'pass\n')
    result=edit_file(ws,'app.py',edits=[{'search':'pass','replace':'return 1'}])
    assert result.startswith('ΣΦΑΛΜΑ') and path.read_bytes()==b'pass\n'


def test_invalid_new_python_is_not_created(tmp_path):
    ws=Workspace(tmp_path)
    assert write_file(ws,'new.py','def incomplete(').startswith('ΣΦΑΛΜΑ')
    assert not (tmp_path/'new.py').exists()


def test_structured_edit_can_remove_literal_conflict_marker_and_repair_syntax(tmp_path):
    ws,path=target(tmp_path,data=b'TOOL_SPECS = [\n=======\n]\n')
    result=edit_file(ws,'app.py',edits=[{'search':'=======\n','replace':''}])
    assert result.startswith('ΕΓΙΝΕ') and path.read_bytes()==b'TOOL_SPECS = [\n]\n'


def test_literal_markers_in_text_are_unambiguous_with_structured_edits(tmp_path):
    ws,path=target(tmp_path,'notes.txt',b'alpha\n=======\nomega\n')
    result=edit_file(ws,'notes.txt',edits=[{'search':'=======\nomega','replace':'literal\n>>>>>>> REPLACE'}])
    assert result.startswith('ΕΓΙΝΕ') and path.read_bytes()==b'alpha\nliteral\n>>>>>>> REPLACE\n'


def test_exact_line_hint_disambiguates_repeated_text(tmp_path):
    ws,path=target(tmp_path,data=b'x = 1\ny = 0\nx = 1\n')
    result=edit_file(ws,'app.py',edits=[{'search':'x = 1','replace':'x = 2','start_line':3}])
    assert result.startswith('ΕΓΙΝΕ') and path.read_bytes()==b'x = 1\ny = 0\nx = 2\n'


def test_wrong_line_hint_never_picks_nearest_match(tmp_path):
    ws,path=target(tmp_path,data=b'# header\nvalue = 1\n');before=path.read_bytes()
    result=edit_file(ws,'app.py',edits=[{'search':'value = 1','replace':'value = 2','start_line':1}])
    assert result.startswith('ΣΦΑΛΜΑ') and path.read_bytes()==before


def test_multiblock_hints_use_original_lines_after_insertions(tmp_path):
    ws,path=target(tmp_path,data=b'a = 1\nb = 2\nc = 3\n')
    result=edit_file(ws,'app.py',edits=[{'search':'a = 1','replace':'a = 10\nextra = 5','start_line':1},{'search':'c = 3','replace':'c = 30','start_line':3}])
    assert result.startswith('ΕΓΙΝΕ')
    assert path.read_bytes()==b'a = 10\nextra = 5\nb = 2\nc = 30\n'


def test_overlapping_changes_and_overlapping_matches_are_rejected(tmp_path):
    ws,path=target(tmp_path,'data.txt',b'ababa\n');before=path.read_bytes()
    result=edit_file(ws,'data.txt',edits=[{'search':'aba','replace':'x'}])
    assert result.startswith('ΣΦΑΛΜΑ') and '2 φορές' in result and path.read_bytes()==before
    result=edit_file(ws,'data.txt',edits=[{'search':'ababa','replace':'x'},{'search':'baba','replace':'y'}])
    assert result.startswith('ΣΦΑΛΜΑ') and path.read_bytes()==before


def test_bom_crlf_and_unchanged_whitespace_are_preserved_exactly(tmp_path):
    original=b'\xef\xbb\xbf# keep   \r\nvalue = 1\r\n# last\r\n'
    ws,path=target(tmp_path,data=original)
    result=edit_file(ws,'app.py',edits=[{'search':'value = 1','replace':'value = 2\nother = 3'}])
    assert result.startswith('ΕΓΙΝΕ')
    assert path.read_bytes()==b'\xef\xbb\xbf# keep   \r\nvalue = 2\r\nother = 3\r\n# last\r\n'


def test_non_utf8_is_not_silently_rewritten(tmp_path):
    ws,path=target(tmp_path,'data.txt',b'\xff\xfeinvalid');before=path.read_bytes()
    assert edit_file(ws,'data.txt',edits=[{'search':'invalid','replace':'changed'}]).startswith('ΣΦΑΛΜΑ')
    assert path.read_bytes()==before


def test_failed_atomic_replace_keeps_original_and_cleans_temp(tmp_path,monkeypatch):
    ws,path=target(tmp_path);before=path.read_bytes()
    def fail(*args):raise OSError('simulated replace failure')
    monkeypatch.setattr(editing.os,'replace',fail)
    result=dispatch(ws,'edit_file',{'path':'app.py','edits':[{'search':'1','replace':'2'}]})
    assert result.startswith('ΣΦΑΛΜΑ') and path.read_bytes()==before
    assert not list(tmp_path.glob('*.tmp'))


def test_file_changed_since_read_is_not_overwritten(tmp_path):
    ws,path=target(tmp_path,data=b'newer = 3\n')
    with pytest.raises(EditError,match='άλλαξε'):
        commit(ws,path,b'old = 1\n',b'replacement = 2\n')
    assert path.read_bytes()==b'newer = 3\n'


def test_same_second_backups_are_distinct_and_retain_each_version(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace.time,'strftime',lambda fmt:'20261121-010000')
    ws,path=target(tmp_path)
    assert edit_file(ws,'app.py',edits=[{'search':'1','replace':'2'}]).startswith('ΕΓΙΝΕ')
    assert edit_file(ws,'app.py',edits=[{'search':'2','replace':'3'}]).startswith('ΕΓΙΝΕ')
    rows=ws.list_backups();assert len(rows)==2 and rows[0]['backup']!=rows[1]['backup']
    assert {(tmp_path/'.agent_backups'/r['backup']).read_bytes() for r in rows}=={b'value = 1\n',b'value = 2\n'}

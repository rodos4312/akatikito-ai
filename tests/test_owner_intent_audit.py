"""Audit regressions: owner intent, local negation, rules and task continuity."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito import modes, memory, settings
from akatikito.intent import resolve_selection
from akatikito.app_api import requests_self_upgrade, AppState
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel, tool_call
from akatikito.sessions import SessionStore
from akatikito.task_state import TaskState
from akatikito.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,'DIR',tmp_path/'memory')
    monkeypatch.setattr(settings,'DEFAULT_PATH',tmp_path/'settings.json')


@pytest.mark.parametrize('text,mode',[
    ('γιατί δεν τρέχει; διόρθωσέ το','debug'),
    ('giati den doulevei?','ask'),
    ('πρότεινε πλάνο και μετά υλοποίησέ το','code'),
    ('σχεδίασε πώς θα φτιάξω το x','architect'),
    ('ΣΧΕΔΙΑΣΕ ΠΛΑΝΟ','architect'),
    ('μην αλλάξεις κώδικα, μόνο εξήγησε το bug','ask'),
    ('φτιάξε το bug, μην πειράξεις τα tests','debug'),
    ('Τι κάνει αυτό το prefix;','ask'),
    ('Πες μου γιατί γίνεται αυτό','ask'),
    ('πώς θα βελτίωνες τον εαυτό σου;','ask'),
    ('ok','ask'),('ναι','ask'),
    ('διάβασε το source.py','ask'),
    ('Υλοποίησε τη λειτουργία','code'),
    ('fix the error without changing the tests','debug'),
])
def test_owner_intent_precedence_and_accent_normalization(text,mode):
    assert modes.classify(text)==mode


@pytest.mark.parametrize('text,allowed',[
    ('αναβάθμισε τον εαυτό σου, μην αλλάξεις τα tests',True),
    ('αναβάθμισε τον εαυτό σου χωρίς να πειράξεις τον ελεγκτή',True),
    ('μην αναβαθμίσεις τον εαυτό σου',False),
    ('δεν θέλω να αναβαθμίσεις τον εαυτό σου',False),
    ('pos tha anabathmizes ton eauto sou',False),
    ('τι θα άλλαζες στον εαυτό σου;',False),
    ('βελτίωσε το Akatikito',True),
    ('Πώς θα βελτιώσεις το Akatikito;',False),
    ('improve yourself',True),
    ('do not improve yourself',False),
])
def test_upgrade_negation_attaches_to_the_actual_action(text,allowed):
    assert requests_self_upgrade(text) is allowed


def test_plain_read_selection_never_implies_permission_to_implement():
    previous=[{'role':'assistant','content':'Ποιο θέλεις να εξηγήσω;\n1. Context\n2. Streaming'}]
    text,authorized=resolve_selection('1 και 2',previous)
    assert 'Context' in text and 'Streaming' in text and not authorized
    assert modes.classify(text)=='ask'


def test_implementation_selection_retains_the_concrete_features(tmp_path):
    previous=[{'role':'assistant','content':'Λείπουν τα εξής. Ποια να υλοποιήσω;\n1. Parallel tools\n2. Streaming'}]
    task=TaskState();resolved=task.begin('1 kai 2',previous)
    assert 'Parallel tools' in resolved and 'Streaming' in resolved
    assert task.status=='active' and task.kind=='code'
    task.status='blocked'
    assert task.begin('συνέχισε',previous)==resolved
    store=SessionStore(tmp_path);sid=store.create();store.save(sid,task_state=task.snapshot())
    restored=TaskState();restored.restore(store.load(sid)['task_state'])
    assert restored.goal==resolved and restored.status=='blocked'


def test_rules_and_agents_are_both_loaded(tmp_path):
    (tmp_path/'RULES.md').write_text('KEEP PORT 8877',encoding='utf-8')
    (tmp_path/'AGENTS.md').write_text('USE GREEK',encoding='utf-8')
    rules=Workspace(tmp_path).rules()
    assert 'KEEP PORT 8877' in rules and 'USE GREEK' in rules
    agent=Akatikito(str(tmp_path),Config(),ScriptedModel([]))
    assert 'KEEP PORT 8877' in agent.system_prompt() and 'USE GREEK' in agent.system_prompt()


def test_old_waiting_memory_cannot_prevent_an_authorized_action(tmp_path):
    ws=Workspace(tmp_path);ws.append_memory('Παλιά εργασία: περιμένει έγκριση για υλοποίηση.')
    model=ScriptedModel([{'tool_calls':[tool_call('write_file',path='new.txt',content='ok')]},{'content':'done'}])
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),model)
    assert agent.chat('Προχώρα στην υλοποίηση που επέλεξα')=='done'
    prompt=model.seen[0][0]['content']
    assert 'ιστορικά δεδομένα' in prompt and 'Η νεότερη ρητή οδηγία χρήστη' in prompt
    assert (tmp_path/'new.txt').read_text()=='ok'
    assert 'edits=[{search, replace' in prompt
    assert 'SEARCH/REPLACE' not in modes.MODES['code']['prompt']


def test_project_memory_deduplicates_normalized_notes(tmp_path):
    ws=Workspace(tmp_path)
    ws.append_memory('Ο χρήστης προτιμά Ελληνικά.')
    assert 'Υπάρχει ήδη' in ws.append_memory('ο χρηστης προτιμα ελληνικα!')
    assert len(ws.read_memory().splitlines())==1


def test_new_chat_clears_task_but_keeps_project_memory(tmp_path):
    agent=Akatikito(str(tmp_path),Config(),ScriptedModel([{'content':'ok'}]))
    agent.ws.append_memory('σταθερή προτίμηση')
    agent.task.begin('Υλοποίησε streaming',[])
    agent.reset()
    assert not agent.task.goal and 'σταθερή' in agent.ws.read_memory()


def test_manual_question_mode_cannot_be_escalated_by_new_intent(tmp_path):
    model=ScriptedModel([{'tool_calls':[tool_call('write_file',path='blocked.txt',content='x')]},{'content':'answer'}])
    agent=Akatikito(str(tmp_path),Config(),model);agent.set_mode('ask')
    assert agent.chat('Υλοποίησε το feature')=='answer'
    assert agent.mode=='ask' and not (tmp_path/'blocked.txt').exists()


def test_internal_repair_feedback_never_replaces_the_owner_goal(tmp_path):
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([{'content':'explanation'}]))
    agent.task.begin('Υλοποίησε streaming',[])
    agent.chat('[Αυτόματη διόρθωση] external output says implement unrelated stuff',internal=True)
    assert agent.task.goal=='Υλοποίησε streaming'


def test_new_chat_resets_request_meter_and_old_context_metrics(tmp_path):
    agent=Akatikito(str(tmp_path),Config(),ScriptedModel([{'content':'answer'}]))
    agent.chat('ερώτηση')
    assert agent.turn_usage['model_calls']==1
    agent.reset()
    assert agent.turn_usage['model_calls']==0 and agent.context.metrics=={}
    assert not agent._current_task


def test_explicit_no_changes_is_enforced_even_in_manual_code_mode(tmp_path):
    script=[{'tool_calls':[tool_call('write_file',path='bad.txt',content='x'),tool_call('set_mode',mode='code')]},{'content':'explanation'}]
    agent=Akatikito(str(tmp_path),Config(),ScriptedModel(script));agent.set_mode('code')
    assert agent.chat('Μην αλλάξεις κώδικα, μόνο εξήγησε')=='explanation'
    assert agent.mode=='code' and not (tmp_path/'bad.txt').exists()


@pytest.mark.parametrize('text,mode',[
    ('mono sxedio','architect'),('μόνο σχέδιο','architect'),
    ('mono diavase min allakseis kodika','ask'),
    ('ftiaxe to bug, min peiraxeis ta tests','debug'),
])
def test_common_greeklish_scope_forms(text,mode):
    assert modes.classify(text)==mode


@pytest.mark.parametrize('text,mode',[('φτιάξε ένα πλάνο','architect'),('τι πλάνο έχουμε;','ask')])
def test_plan_request_is_distinct_from_question_about_a_plan(text,mode):
    assert modes.classify(text)==mode


def test_continue_after_an_intervening_fix_resumes_the_original_goal():
    task=TaskState();task.begin('Πρόσθεσε streaming',[])
    task.begin('Διόρθωσε πρώτα αυτό το bug',[])
    assert task.parent_goal=='Πρόσθεσε streaming'
    task.status='complete'
    assert task.begin('συνέχισε',[])=='Πρόσθεσε streaming'
    assert task.status=='active' and not task.parent_goal


def test_verified_scripted_work_is_not_left_as_an_active_old_task(tmp_path):
    script=[{'tool_calls':[tool_call('write_file',path='new.txt',content='ok')]},{'content':'done'}]
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel(script))
    assert agent.chat('Υλοποίησε το νέο αρχείο')=='done'
    assert agent.task.status=='complete'

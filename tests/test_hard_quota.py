"""Do not repeat paid generation requests after a permanent quota rejection."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx,pytest
from openai import RateLimitError
from akatikito.model import OpenAIModel,ModelError
from akatikito.config import Config


def error(message):
    response=httpx.Response(429,request=httpx.Request('POST','https://unit.invalid/chat/completions'))
    return RateLimitError(message,response=response,body={'message':message})


def test_weekly_hard_quota_makes_only_one_attempt():
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        raise error('Weekly included input token limit exceeded. Wait for weekly reset.')
    model=OpenAIModel.__new__(OpenAIModel)
    model.config=Config(model='test',api_key='synthetic',model_retry_delay=0,model_retry_attempts=3)
    model._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(ModelError,match='Weekly included input token limit exceeded'):
        model.chat([{'role':'user','content':'synthetic request'}])
    assert len(calls)==1


def test_temporary_rate_limit_remains_retryable():
    assert OpenAIModel._is_retryable(error('Too many requests; retry later.'))


def test_machine_readable_quota_rejection_is_not_retried():
    assert not OpenAIModel._is_retryable(error('insufficient_quota'))

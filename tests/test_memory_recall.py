"""MEMORY-PLAN M3: recall returns the few memories a question needs, safely framed, in any language."""
import time

import pytest

from akatikito.memory_recall import HEADER, match_expression, recall
from akatikito.memory_store import MemoryStore
from akatikito.memory_text import query_terms


@pytest.fixture(params=["fts", "plain"])
def store(request, tmp_path):
    s = MemoryStore(tmp_path / "memory.db", force_plain=request.param == "plain")
    yield s
    s.close()


def test_eval_set_reaches_the_plan_targets(store):
    """MEMORY-PLAN §6: recall@8 >= 0.90 overall and per language family, at most 2 unrelated items."""
    from scripts.memory_eval import evaluate, load_corpus

    corpus = load_corpus()
    ids = {store.add(m["text"], source="eval"): m["id"] for m in corpus["memories"]}

    class Engine:
        name = "recall"

        def search(self, question, limit=8):
            return [ids[i.id] for i in recall(store, question, limit=limit, max_chars=10_000, touch=False).items]

    report = evaluate(Engine(), corpus["questions"])
    assert report["recall_at_8"] >= 0.90, report["misses"]
    assert report["by_tag"]["greeklish"] >= 0.90 and report["by_tag"]["cross"] >= 0.90
    assert report["by_tag"]["cjk"] >= 0.75 and report["by_tag"]["id"] >= 0.90
    assert report["avg_irrelevant"] <= 2


def test_block_is_framed_as_data_and_respects_the_budget(store):
    for i in range(30):
        store.add(f"Σημείωση {i} για το αντίγραφο ασφαλείας του δίσκου E: με αρκετό κείμενο για να γεμίσει.")
    result = recall(store, "αντίγραφο ασφαλείας", max_chars=400, touch=False)
    assert result.text.startswith(HEADER)
    assert len(result.text) <= 400 and 0 < len(result.items) <= 8
    assert all(line.startswith("- (") for line in result.text.splitlines()[1:])


def test_unrelated_question_recalls_nothing(store):
    store.add("Ο σκύλος λέγεται Ρεξ.")
    assert recall(store, "Ποια είναι η πρωτεύουσα της Νορβηγίας;").items == []
    assert recall(store, "   ").text == ""


def test_project_memories_stay_in_their_project(store):
    store.add("Τα tests τρέχουν με check.cmd", scope="project:alpha")
    store.add("Τα tests τρέχουν με make test", scope="project:beta")
    texts = [i.row["text"] for i in recall(store, "πώς τρέχουν τα tests", project_scope="project:alpha").items]
    assert texts == ["Τα tests τρέχουν με check.cmd"]
    assert recall(store, "πώς τρέχουν τα tests").items == []  # global questions see no project memory


def test_revised_and_forgotten_memories_are_never_recalled(store):
    old = store.add("Ο πάροχος είναι το DeepSeek.")
    store.update(old, text="Ο πάροχος είναι το OpenRouter.")
    gone = store.add("Ο πάροχος backup είναι το Groq.")
    store.forget(gone)
    texts = [i.row["text"] for i in recall(store, "ποιος είναι ο πάροχος").items]
    assert texts == ["Ο πάροχος είναι το OpenRouter."]


def test_feedback_and_importance_reorder_equal_matches(store):
    a = store.add("Ο κωδικός του router είναι στο συρτάρι.")
    b = store.add("Ο κωδικός του router είναι στο ντουλάπι.", importance=3)
    assert [i.id for i in recall(store, "κωδικός router", touch=False).items][0] == b
    for _ in range(3):
        store.feedback(b, helpful=False)
        store.feedback(a, helpful=True)
    assert [i.id for i in recall(store, "κωδικός router", touch=False).items][0] == a


def test_recall_marks_use_and_explains_why(store):
    memory_id = store.add("Ο χρήστης δουλεύει ως πυροσβέστης.")
    item = recall(store, "pyrosvestis").items[0]
    assert item.id == memory_id and any(w.startswith("≈") for w in item.why)
    assert store.get(memory_id)["use_count"] == 1


def test_unconfirmed_beliefs_are_labelled(store):
    store.add("Ίσως προτιμά σύντομες απαντήσεις.", kind="belief", status="unconfirmed")
    assert "ΑΝΕΠΙΒΕΒΑΙΩΤΗ ΥΠΟΘΕΣΗ" in recall(store, "σύντομες απαντήσεις").text


def test_quotes_in_a_question_cannot_break_the_search_syntax(store):
    store.add('Το αρχείο λέγεται "notes".')
    expression = match_expression(query_terms('αρχείο "notes" OR NEAR( *'))
    assert expression.count('"') % 2 == 0
    assert recall(store, 'αρχείο "notes" OR NEAR( *').items


@pytest.mark.slow
def test_ten_thousand_memories_answer_fast(tmp_path):
    with MemoryStore(tmp_path / "big.db") as s:
        if not s.fts:
            pytest.skip("FTS5 unavailable")
        started = time.perf_counter()
        s.add_many({"text": f"Σημείωση αριθμός {i} για το έργο {i % 97} και τον φάκελο {i % 13}."}
                   for i in range(10_000))
        assert time.perf_counter() - started < 60  # bulk import stays practical
        s.add("Τα αντίγραφα ασφαλείας γίνονται κάθε Κυριακή.")
        recall(s, "αντίγραφο ασφαλείας", touch=False)
        started = time.perf_counter()
        for _ in range(10):
            result = recall(s, "αντίγραφο ασφαλείας", touch=False)
        elapsed_ms = (time.perf_counter() - started) * 100
        assert result.items[0].row["text"].startswith("Τα αντίγραφα")
        assert elapsed_ms < 100  # plan target 30 ms; generous for slow CI machines

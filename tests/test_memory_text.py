"""MEMORY-PLAN M2: search keys survive accents, endings, Greeklish, CJK and identifiers."""
import pytest

from akatikito.memory_text import cjk_pairs, detect_lang, fold, identifiers, index_terms, query_terms, skeleton


@pytest.mark.parametrize("a,b", [("Ακτή", "ακτη"), ("Mañana", "manana"), ("удалённо", "удаленно"),
                                 ("Steuererklärung", "steuererklarung")])
def test_fold_ignores_case_and_accents(a, b):
    assert fold(a) == fold(b)


@pytest.mark.parametrize("greek,latin", [
    ("πυροσβέστης", "pyrosvestis"), ("πυροσβέστης", "pirosvestis"), ("δουλεύει", "doulevo"),
    ("δουλεύει", "douleuo"), ("δουλεύει", "doulevw"), ("βάρδιες", "vardia"), ("τιμολόγια", "timologio"),
    ("αποθηκεύονται", "apothikevontai"), ("αυτοκίνητο", "aftokinito"), ("αυτοκίνητο", "avtokinito"),
    ("γλώσσα", "glwssa"), ("σκοτεινό", "skoteino"),
])
def test_greek_and_greeklish_share_a_skeleton(greek, latin):
    assert skeleton(greek) and skeleton(greek) == skeleton(latin)


@pytest.mark.parametrize("a,b", [("σκύλος", "γάτα"), ("pnpm", "npm"), ("βάρδια", "βιβλίο")])
def test_different_words_keep_different_skeletons(a, b):
    assert skeleton(a) != skeleton(b)


@pytest.mark.parametrize("text,lang", [
    ("Ο σκύλος λέγεται Ρεξ", "el"), ("The user prefers pnpm over npm", "en"), ("Die Katze heißt Mia", "de"),
    ("El usuario prefiere las reuniones", "es"), ("Он не любит совещания", "ru"), ("他对海鲜过敏", "zh"),
    ("ユーザーは毎朝コーヒーを飲みます", "ja"), ("to aftokinito einai toyota", "greeklish"),
])
def test_language_detection_picks_the_right_stemmer(text, lang):
    assert detect_lang(text) == lang


def test_cjk_is_split_into_overlapping_pairs():
    assert cjk_pairs("海鲜过敏") == ["海鲜", "鲜过", "过敏"] and cjk_pairs("海") == ["海"]
    keys, _ = index_terms("他对海鲜过敏。")
    assert "海鲜" in keys.split() and "过敏" in keys.split()


@pytest.mark.parametrize("text,token", [("deepseek-v4-flash", "deepseekv4flash"), ("D:\\meu-work", "dmeuwork"),
                                        ("192.168.1.1", "19216811"), ("check.cmd", "checkcmd"),
                                        ("Version 2.4.0", "240")])
def test_identifiers_are_kept_whole(text, token):
    assert token in identifiers(text)
    assert token in query_terms(text).exact


def test_index_holds_words_stems_and_skeletons():
    keys, sk = index_terms("Ο χρήστης δουλεύει ως πυροσβέστης.", topic="δουλειά")
    keys = keys.split()
    assert "δουλευει" in keys and "δουλευ" in keys and "πυροσβεστ" in keys
    assert "pirosvest" in sk.split() and "dulev" in sk.split()


def test_query_drops_stop_words_but_never_everything():
    terms = query_terms("Τι δουλειά κάνει ο χρήστης;")
    every = terms.exact + terms.prefix
    assert "τι" not in every and "ο" not in every and "χρηστ" in every
    assert not query_terms("the and of").empty()  # only stop words: keep them rather than search nothing


def test_query_stems_latin_words_in_several_languages():
    terms = query_terms("Fahrräder")
    assert "fahrrad" in terms.prefix  # German stem, although one word cannot reveal its language

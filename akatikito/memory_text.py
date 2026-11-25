"""Language handling for the central memory (MEMORY-PLAN M2). Local, no model, no network.

Turns a memory or a question into search keys that survive the ways people actually write:
accents and case (`Σκύλος` / `σκυλος`), word endings (`δουλεύω` / `δουλεύει`, `deployments` /
`deploy`), Greek typed in Latin letters (`pyrosvestis` / `πυροσβέστης`), scripts without spaces
(Chinese and Japanese, as overlapping character pairs) and identifiers (`deepseek-v4-flash`,
`D:\\meu-work`, `192.168.1.1`), which are also kept whole.

Two key sets are produced: ``keys`` (folded words, stems, character pairs, whole identifiers)
and ``sk`` (a sound "skeleton" shared by Greek and Greeklish spellings of the same word).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

try:  # pure-Python stemmers for ~30 languages; without them only exact and prefix matching remain
    import snowballstemmer
except ImportError:  # pragma: no cover - exercised on stripped-down installs only
    snowballstemmer = None

SNOWBALL = {"el": "greek", "en": "english", "de": "german", "es": "spanish", "fr": "french", "it": "italian",
            "pt": "portuguese", "nl": "dutch", "ru": "russian", "tr": "turkish", "sv": "swedish", "pl": "polish"}
LATIN_STEMMERS = ("en", "de", "es", "fr", "it", "pt", "nl")

_STOP = {
    "el": "ο η το οι τα του της των τον την τη στο στη στον στην στα στις σε με για απο να θα και ειναι "
          "ποιο ποια ποιος ποιοι τι πως που ποτε ενα μια ενας η αν οτι μου σου μας σας εχει εχω αυτο αυτα "
          "δεν μην μη ειναι πριν κατα ως",
    "greeklish": "o i to oi ta tou tis ton tin ti sto sti ston stin sta se me gia apo na tha kai einai poio "
                 "poia poios ti pws pos pou pote ena mia enas an oti mou sou mas den min",
    "en": "the a an is are was were of to in on at for and or with what which when where who how do does "
          "did can i he his she her it its should be by from this that my we our us your you there have has",
    "de": "der die das und ist im am zu zum zur mit den dem des ein eine einen er sie es was wie wann "
          "welche welcher welches welchen bei auf nur jeden jedes jede von fur nicht",
    "es": "el la los las de del en y a que se su sus un una por con es que cuando donde le no lo al como",
    "fr": "le la les de des du et est un une en au aux que qui pour dans sur pas ne il elle",
    "it": "il lo la gli le di del della e un una in che per con non sono",
    "pt": "o a os as de do da e um uma em que para com nao por",
    "nl": "de het een en van is in op te met voor niet dat die",
    "ru": "в во на и не он она его ее по из как какой какая когда где сколько ли что с со к у от до это",
}
STOPWORDS = {lang: frozenset(words.split()) for lang, words in _STOP.items()}
_ALL_STOP = frozenset().union(*STOPWORDS.values())

_IDENTIFIER = re.compile(r"[A-Za-z]:\\[^\s,;]+|(?:[\w]+[-_.:/\\]){1,}[\w]+|\w*\d\w*")
_WORD = re.compile(r"[^\W\d_]{2,}|\d{2,}")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\u31f0-\u31ff\u30fc]+")


def fold(text: str) -> str:
    """Case- and accent-insensitive form (``Ακτή`` → ``ακτη``, ``Mañana`` → ``manana``, ``ё`` → ``е``)."""
    text = unicodedata.normalize("NFKD", str(text).casefold())
    return unicodedata.normalize("NFC", "".join(c for c in text if not unicodedata.combining(c)))


def script_of(text: str) -> str:
    counts = {"greek": 0, "cyrillic": 0, "latin": 0, "han": 0, "kana": 0, "hangul": 0}
    for ch in text:
        code = ord(ch)
        if 0x370 <= code <= 0x3FF or 0x1F00 <= code <= 0x1FFF:
            counts["greek"] += 1
        elif 0x400 <= code <= 0x4FF:
            counts["cyrillic"] += 1
        elif 0x3040 <= code <= 0x30FF:
            counts["kana"] += 1
        elif 0xAC00 <= code <= 0xD7AF:
            counts["hangul"] += 1
        elif 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            counts["han"] += 1
        elif ch.isalpha() and code < 0x250:
            counts["latin"] += 1
    best = max(counts, key=counts.get)
    return best if counts[best] else "none"


def detect_lang(text: str) -> str:
    """Best guess, good enough to pick a stemmer: el, ru, ja, zh, ko, greeklish or a Latin language."""
    script = script_of(text)
    direct = {"greek": "el", "cyrillic": "ru", "kana": "ja", "hangul": "ko", "none": ""}
    if script in direct:
        return direct[script]
    if script == "han":
        return "ja" if re.search(r"[\u3040-\u30ff]", text) else "zh"
    words = re.findall(r"[^\W\d_]+", fold(text))
    # Greeklish last: on a tie a real language's stemmer is the safer choice.
    scores = {lang: sum(w in STOPWORDS[lang] for w in words) for lang in (*LATIN_STEMMERS, "greeklish")}
    best = max(scores, key=scores.get)
    return best if scores[best] else "en"


@lru_cache(maxsize=None)
def _stemmer(lang: str):
    name = SNOWBALL.get(lang)
    return snowballstemmer.stemmer(name) if snowballstemmer and name else None


def stem(word: str, lang: str) -> str:
    stemmer = _stemmer(lang)
    return stemmer.stemWord(word) if stemmer else word


# ── Greek ⇄ Greeklish sound skeleton ───────────────────────────────────────────────────────────
_GREEK = [("ου", "u"), ("αι", "e"), ("ει", "i"), ("οι", "i"), ("υι", "i"), ("ευ", "ev"), ("αυ", "av"), ("μπ", "b"),
          ("ντ", "d"), ("γκ", "g"), ("γγ", "g"), ("τσ", "ts"), ("τζ", "tz"), ("α", "a"), ("β", "v"), ("γ", "g"),
          ("δ", "d"), ("ε", "e"), ("ζ", "z"), ("η", "i"), ("θ", "th"), ("ι", "i"), ("κ", "k"), ("λ", "l"), ("μ", "m"),
          ("ν", "n"), ("ξ", "ks"), ("ο", "o"), ("π", "p"), ("ρ", "r"), ("σ", "s"), ("ς", "s"), ("τ", "t"), ("υ", "i"),
          ("φ", "f"), ("χ", "h"), ("ψ", "ps"), ("ω", "o")]
_LATIN = [("th", "th"), ("8", "th"), ("ch", "h"), ("ph", "f"), ("ps", "ps"), ("ks", "ks"), ("ou", "u"), ("ei", "i"),
          ("oi", "i"), ("ui", "i"), ("ee", "i"), ("ai", "e"), ("eu", "ev"), ("au", "av"), ("mp", "b"), ("nt", "d"),
          ("gk", "g"), ("gg", "g"), ("x", "h"), ("y", "i"), ("w", "o"), ("c", "k"), ("q", "k"), ("3", "ks")]


def _translit(word: str, table) -> str:
    out, i = [], 0
    while i < len(word):
        for src, dst in table:
            if word.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(word[i])
            i += 1
    return "".join(out)


def skeleton(word: str) -> str:
    """Spelling-independent key: ``πυροσβέστης`` and ``pyrosvestis`` both give ``pirosvest``."""
    word = fold(word)
    if len(word) < 3 or not word.isalpha():
        return ""
    table = _GREEK if script_of(word) == "greek" else _LATIN
    if table is _LATIN and not word.isascii():
        return ""
    # αυ/ευ are typed both ways (aftokinito / avtokinito), so af/ef fold into av/ev on both sides.
    out = re.sub(r"(.)\1+", r"\1", _translit(word, table)).replace("af", "av").replace("ef", "ev")
    if len(out) > 4 and out[-1] in "sn":  # one final -s / -n (case endings), then the ending vowels
        out = out[:-1]
    while len(out) > 3 and out[-1] in "aeiou":
        out = out[:-1]
    return out if len(out) >= 3 else ""


# ── terms ──────────────────────────────────────────────────────────────────────────────────────
def cjk_pairs(run: str) -> list[str]:
    """Overlapping character pairs (``海鲜过敏`` → 海鲜 鲜过 过敏); a single character stays itself."""
    return [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]


def identifiers(text: str) -> list[str]:
    """Whole identifiers (paths, versions, hosts, model names) as one compact token each."""
    found = []
    for match in _IDENTIFIER.finditer(text):
        token = re.sub(r"[\W_]+", "", fold(match.group()))
        if len(token) >= 2 and not token.isalpha():
            found.append(token)
        elif re.search(r"[-_.:/\\]", match.group()) and len(token) >= 4:
            found.append(token)
    return found


@dataclass
class Terms:
    """Search terms of a question: exact keys, prefix keys and skeleton prefixes."""

    exact: list[str] = field(default_factory=list)
    prefix: list[str] = field(default_factory=list)
    skeletons: list[str] = field(default_factory=list)
    lang: str = ""

    def empty(self) -> bool:
        return not (self.exact or self.prefix or self.skeletons)


def _words(text: str) -> tuple[list[str], list[str]]:
    """(folded words outside CJK runs, CJK runs)."""
    folded = fold(text)
    runs = _CJK.findall(folded)
    rest = _CJK.sub(" ", folded)
    return [w for w in _WORD.findall(rest)], runs


def index_terms(text: str, topic: str = "", keywords: str = "") -> tuple[str, str]:
    """``(keys, sk)`` strings stored in the search index for one memory."""
    source = " ".join(part for part in (topic, text, keywords) if part)
    lang = detect_lang(source)
    words, runs = _words(source)
    keys, sk = [], []
    for word in words:
        keys.append(word)
        word_lang = "el" if script_of(word) == "greek" else "ru" if script_of(word) == "cyrillic" else lang
        stemmed = stem(word, word_lang if word_lang in SNOWBALL else "en")
        if stemmed != word:
            keys.append(stemmed)
        if (skel := skeleton(word)) and word not in _ALL_STOP:
            sk.append(skel)
    for run in runs:
        keys.extend(cjk_pairs(run))
    keys.extend(identifiers(source))
    return " ".join(dict.fromkeys(keys)), " ".join(dict.fromkeys(sk))


def query_terms(text: str) -> Terms:
    """Terms for a question: stop words dropped, every plausible stem and the Greeklish skeleton kept."""
    lang = detect_lang(text)
    words, runs = _words(text)
    terms = Terms(lang=lang)
    content = [w for w in words if w not in _ALL_STOP] or words
    for word in content:
        script = script_of(word)
        langs = ("el",) if script == "greek" else ("ru",) if script == "cyrillic" else LATIN_STEMMERS
        if word.isdigit() or len(word) < 4:
            terms.exact.append(word)
        else:
            terms.prefix.append(word)
        for candidate in langs:
            stemmed = stem(word, candidate)
            if stemmed != word and len(stemmed) >= 3:
                (terms.prefix if len(stemmed) >= 4 else terms.exact).append(stemmed)
        if (skel := skeleton(word)) and len(skel) >= 4:
            terms.skeletons.append(skel)
    for run in runs:
        terms.exact.extend(cjk_pairs(run))
    terms.exact.extend(identifiers(text))
    terms.exact = list(dict.fromkeys(terms.exact))
    terms.prefix = [p for p in dict.fromkeys(terms.prefix) if p not in terms.exact]
    terms.skeletons = list(dict.fromkeys(terms.skeletons))
    return terms

"""Deterministic owner intent, with local negation and consistent Greek normalization."""
from __future__ import annotations
import re
import unicodedata


def normal(text):
    return ''.join(c for c in unicodedata.normalize('NFD', str(text or '').casefold())
                   if not unicodedata.combining(c)).replace('ς', 'σ')


_ACTION = re.compile(r"\b(?:υπενθυμισ\w*|θυμισ\w*|remind|μεταφρασ\w*|translat\w*|συνοψισ\w*|δημιουργησ\w*|φτιαξ(?:ε\w*|εισ\w*|το\w*|τε\w*)|διορθωσ\w*|ενημερωσ\w*|υλοποιησ\w*|προσθεσ\w*|αναβαθμισ\w*|βελτιωσ\w*|αλλαξ\w*|γραψ\w*|εφαρμοσ\w*|τρεξε\w*|συνεχισ\w*|προχωρ\w*|ftia[kx]\w*|diorthos\w*|ylop\w*|prosth\w*|anabathm\w*|veltios\w*|allax\w*|graps\w*|efarmos\w*|trexe\w*|synex\w*|synech\w*|proxora\w*|implement|modify|update|add|fix|build|create|repair|improve|upgrade)\b")
_NEGATION = re.compile(r"(?:\b(?:μην|μη|δεν|οχι|den|min|oxi|not)|don't|do not)\s+(?:\w+\s+){0,3}$")
_QUESTION = re.compile(r"^\s*(?:γιατι|τι\b|πωσ|ποιο|giati|ti\b|pos\b|pws\b|why\b|what\b|how\b)")
_PLAN = re.compile(r"σχεδι|πλανο|αρχιτεκτον|προτειν|sxedi|plano|architecture|\bplan\b|suggest")
_DEBUG = re.compile(r"σφαλμ|λαθοσ|\bbug\b|\berror\b|κρασαρ|δεν (?:δουλευ|τρεχ)|den (?:doule|trex)")


def action_spans(text):
    value = normal(text)
    result = []
    for match in _ACTION.finditer(value):
        before = value[:match.start()]
        clause = re.split(r'[;.!?\n,]', before)[-1]
        denied = bool(_NEGATION.search(clause))
        hypothetical = bool(_QUESTION.search(clause) and re.search(r'\b(?:θα|tha|would|could)\b', clause))
        result.append((match, not denied and not hypothetical))
    return result


def positive_actions(text):
    return [match.group() for match, allowed in action_spans(text) if allowed]


def implementation_requested(text):
    actions = positive_actions(text)
    return any(not re.fullmatch(r"(?:τρεξε\w*|trexe\w*)", action) for action in actions)


def read_only(text):
    value = normal(text)
    return bool(re.search(r'(?:μην|μη|min|do not|don.t)\s+(?:\w+\s+){0,2}(?:αλλαξ\w*|πειραξ\w*|γραψ\w*|change|edit|write)\s+(?:(?:τα|τον|τα|any|the|κανενα)\s+)?(?:τιποτα|κατι|αρχει\w*|κωδικ\w*|anything|files|code)\b', value)
                or re.search(r'(?:min|mi|den)\s+(?:\w+\s+){0,2}(?:allax\w*|peirax\w*|graps\w*)\s+(?:ta\s+)?(?:tipota|kati|arxei\w*|kodik\w*)', value)
                or re.search(r'\b(?:χωρισ|xoris|without|no)\s+(?:αλλαγεσ|allages|changes|edits)(?=\s*(?:$|[,.;!]))', value)
                or re.search(r'\b(?:μονο|only|mono)\s+(?:διαβασ\w*|diavas\w*|read|εξηγ\w*|ερωτησ\w*|erotis\w*|eksig\w*)', value))


def classify(text):
    value = normal(text).strip()
    actions = positive_actions(text)
    if read_only(text):
        return 'ask'
    if re.search(r'υπενθυμ|θυμισε|remind|reminder|έγγραφ|εγγραφ|document|spreadsheet|csv|excel|pdf|επιστολ|letter|μεταφρα|translat',value) and not re.search(r'κωδικ|code|bug|agent|akatikito|υλοποι|implement',value):
        return 'assistant' if actions else 'ask'
    if actions:
        if _PLAN.search(value) and all(re.match(r'φτιαξ|γραψ|create', a) for a in actions) and not re.search(r'κωδικ|code|υλοποι|implement', value):
            return 'architect'
        return 'debug' if _DEBUG.search(value) or any(re.search(r'διορθ|diorth|fix|repair', a) for a in actions) else 'code'
    if _QUESTION.search(value):
        return 'ask'
    if _PLAN.search(value):
        return 'architect'
    if re.search(r'^(?:διαβασε|δες|εξετασε|read\b|inspect\b|analyse\b|analyze\b)', value):
        return 'ask'
    if re.search(r'εξηγη|πεσ μου|pes mou|eksig|explain', value):
        return 'ask'
    if value.strip(' !.;,') in ('ευχαριστω','thanks','kalispera','καλησπερα','καλημερα','ok','οκ','ναι','nai','yes'):
        return 'ask'
    if re.search(r'συντομ|περιεκτ|απλ[εη].*ερωτ|aples? erot|periektik|suntom|syntom|λιγοτερ.*token', value):
        return 'ask'
    if _DEBUG.search(value):
        return 'debug'
    return 'code'


def self_upgrade(text, own_project=False):
    value = normal(text)
    if read_only(text):
        return False
    candidates = [(match, allowed) for match, allowed in action_spans(text)
                  if re.search(r'αναβαθμ|βελτιω|anabathm|veltio|upgrade|improve', match.group())]
    explicit_name = bool(re.search(r'\bakatikito\b|ακατικιτο', value))
    reference = explicit_name or bool(re.search(r'εαυτ|eauto|eafto|yourself', value))
    if candidates and reference:
        return candidates[-1][1]
    return bool((positive_actions(text) and (explicit_name or own_project) and classify(text) in ('code','debug'))
                or (own_project and classify(text) == 'architect' and _PLAN.search(value)))


def resolve_selection(text, messages):
    value = normal(text).strip()
    if not re.fullmatch(r'[\d\s,;+&]+(?:(?:και|kai|and)[\d\s,;+&]+)*', value):
        return str(text), False
    numbers = list(dict.fromkeys(re.findall(r'\d+', value)))
    if not numbers or len(numbers) > 8:
        return str(text), False
    previous = next((str(m.get('content') or '') for m in reversed(messages)
                     if m.get('role') == 'assistant' and not m.get('tool_calls')), '')
    options = dict(re.findall(r'^\s*(\d+)[.)]\s+(.+)$', previous, re.M))
    if not all(n in options for n in numbers):
        return str(text), False
    implementation = bool(re.search(r'υλοποι|προχωρ|φτιαξ|λειπουν|implement|build|add ', normal(previous)))
    prefix = 'Υλοποίησε τις επιλεγμένες προτάσεις:' if implementation else 'Εξέτασε τα επιλεγμένα στοιχεία:'
    return prefix + '\n' + '\n'.join(n + '. ' + options[n] for n in numbers), implementation

"""Φρουρός μνήμης: απορρίπτει σημειώσεις που μοιάζουν με prompt injection.

Μια δηλητηριασμένη σημείωση ΜΕΝΕΙ και διαβάζεται σε κάθε επόμενη συνεδρία, άρα
ελέγχεται πριν γραφτεί. Δεσμεύεται σε σαφή επιθετική συμπεριφορά (παράκαμψη
οδηγιών, αλλαγή ταυτότητας, απόκρυψη από τον χρήστη, διαρροή μυστικών, αόρατοι
χαρακτήρες), ΟΧΙ σε επιτακτική διατύπωση: «πρέπει πάντα να τρέχεις pytest»
είναι θεμιτή μνήμη. Τα regex έχουν φραγμένο ενδιάμεσο κείμενο ({0,N} λέξεις)
ώστε να μην υπάρχει καταστροφικό backtracking σε μεγάλη είσοδο.
"""

from __future__ import annotations

import re
import unicodedata
import ipaddress
from urllib.parse import urlsplit

# Οι σημειώσεις είναι «ένα σύντομο γεγονός»· πάνω από αυτό το όριο δεν σαρώνουμε
# μισό κείμενο (θα έκρυβε το επίθεμα), το αρνούμαστε.
MAX_SCAN = 20_000

_INVISIBLE = re.compile(
    "[­᠎​‌‎‏‪-‮⁠-⁤⁦-⁩﻿"
    "\U000e0000-\U000e007f]"
    # Το ZWJ ενώνει emoji· ανάμεσα σε γράμματα όμως δεν έχει θεμιτή χρήση.
    r"|(?<=\w)‍|‍(?=\w)")

_HTML = re.compile(
    r"<!--|<\s*script\b"
    r"|<\s*(?:div|span|p|details|img|a|iframe|style)\b[^>]{0,200}?"
    r"(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0|\bhidden\b)")

_W = r"(?:\w+\s+)"  # μία λέξη φραγμένου ενδιάμεσου κειμένου

# Λέξεις αρνητικής διατύπωσης πριν από το εύρημα: «μην αγνοείς τους κανόνες» δεν
# είναι επίθεση. Ισχύει μόνο για τις κατηγορίες όπου το ρήμα μπορεί να αρνηθεί.
_NEG = re.compile(r"(?:μην|δεν|never|not|don'?t|dont|cannot|can'?t)\s+(?:\w+\s+){0,2}$")

_OVERRIDE = [re.compile(p) for p in (
    r"\b(?:ignore|disregard|forget|override|bypass)\s+" + _W + r"{0,3}"
    r"(?:previous|prior|above|earlier|all|any|your|my|system|these|those)\s+" + _W + r"{0,3}"
    r"(?:instructions?|rules?|prompts?|guidelines?|directives?|polic(?:y|ies))\b",
    r"\b(?:new|updated)\s+system\s+prompt\s*:",
    r"<\|(?:im_start|im_end|system)\|>|\[\s*system\s*\]|<\s*/?\s*system\s*>",
    r"(?:αγνοησε|αγνοηστε|ξεχασε|παραβλεψε|αψηφησε|παρακαμψε)\s+" + _W + r"{0,3}"
    r"(?:προηγουμεν\w+|παραπανω|ολεσ|ολουσ|ολα|ολων|σου|σας|συστηματοσ|αρχικ\w+)\s+" + _W + r"{0,3}"
    r"(?:οδηγι\w+|κανον\w+|εντολ\w+|περιορισμ\w+)",
    r"(?:αγνοησε|αγνοηστε|ξεχασε|παραβλεψε|αψηφησε|παρακαμψε)\s+" + _W + r"{0,2}"
    r"(?:οδηγι\w+|κανον\w+|εντολ\w+)\s+(?:σου|σας|του\s+συστηματοσ)\b",
)]

_ROLE = [re.compile(p) for p in (
    r"\byou\s+are\s+now\s+(?:a|an|the|my|no\s+longer|free|unrestricted)\b",
    r"\bfrom\s+now\s+on,?\s+you\s+(?:are|will\s+be|shall\s+be)\b",
    r"\bpretend\s+(?:to\s+be|you\s+are|that\s+you)\b",
    r"\bact\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+)?(?:unrestricted|jailbroken|dan)\b",
    r"απο\s+τωρα\s+(?:εισαι|εισαστε|θα\s+εισαι|θα\s+ενεργεις|ειμαι)\b",
    r"(?:απο\s+εδω\s+και\s+(?:περα|στο\s+εξης)|στο\s+εξης|εφεξης),?\s+(?:εισαι|εισαστε|θα\s+εισαι|θα\s+ενεργεις)\b",
    r"\bεισαι\s+τωρα\s+(?:ενας|μια|ενα|ο|η|το)\b",
    r"\b(?:προσποιησου|παριστανε)\s+(?:οτι|πως|τον|την|το)\b",
)]

_CONCEAL = [re.compile(p) for p in (
    # "mention"/"reveal" mirror the Greek αποκαλύψεις; "show"/"say" stay out on purpose:
    # "never show the user raw stack traces" is an ordinary UX note.
    r"\b(?:do\s+not|don'?t|never)\s+" + _W + r"{0,3}(?:tell|inform|notify|alert|mention|reveal)\s+" + _W + r"{0,2}"
    r"(?:the\s+)?(?:user|owner|human|operator)\b",
    r"\bwithout\s+(?:telling|informing|notifying)\s+(?:the\s+)?(?:user|owner|human)\b",
    r"\b(?:hide|conceal)\s+(?:this|it|that)\s+from\s+(?:the\s+)?(?:user|owner|human)\b",
    r"\bμην\s+" + _W + r"{0,3}(?:πει[σς]|πειτε|ενημερωσει[σς]|ειδοποιησει[σς]|αποκαλυψει[σς])\s+" + _W + r"{0,2}"
    r"(?:στον\s+|στην\s+|του\s+|τον\s+)?(?:χρηστ\w+|ιδιοκτητ\w+|ανθρωπ\w+)",
    r"\bχωρι[σς]\s+να\s+(?:το\s+)?(?:πει[σς]|ενημερωσει[σς]|ειδοποιησει[σς])\s+" + _W + r"{0,2}(?:χρηστ\w+|ιδιοκτητ\w+)",
    r"\bκρυψε\s+(?:το|αυτο)\s+απο\s+(?:τον\s+)?(?:χρηστ\w+|ιδιοκτητ\w+)",
)]

# Μυστικό: όνομα μεταβλητής ή λέξη-κλειδί.
_SECRET = (r"(?:\$\{?\w{0,20}(?:key|token|secret|passw\w*)\w{0,20}\}?|\$env:\w+|%\w{0,20}(?:key|token|secret)\w{0,20}%"
           r"|\b(?:api[_ -]?keys?|secrets?|tokens?|passwords?|credentials?|id_rsa)\b|\.env\b"
           r"|\benv(?:ironment)?\s+var\w*)")
_NETCMD = r"\b(?:curl|wget|invoke-webrequest|invoke-restmethod|iwr|irm)\b"
_EXFIL_PAIR = [re.compile(p) for p in (
    _NETCMD + r"[^\n]{0,200}?" + _SECRET,
    _SECRET + r"[^\n]{0,200}?" + _NETCMD,
)]
_EXFIL_VERBAL = [re.compile(p) for p in (
    r"\b(?:send|post|upload|exfiltrate|email|forward|leak)\s+" + _W + r"{0,4}"
    r"(?:api\s+keys?|secrets?|tokens?|passwords?|credentials?|env(?:ironment)?\s+variables?|\.env)\b[^\n]{0,80}?https?://",
    r"\b(?:στειλε|ανεβασε|διαρρευσε|κοινοποιησε)\s+" + _W + r"{0,4}"
    r"(?:κλειδι\w*|μυστικ\w+|κωδικ\w+|tokens?|διαπιστευτηρι\w+|μεταβλητ\w+)\b[^\n]{0,80}?https?://",
)]
# Διεύθυνση που φέρει μυστικό στο ίδιο το URL.
_URL_SECRET = re.compile(r"https?://[^\s]{0,200}?(?:\$\{?\w{0,20}(?:key|token|secret|passw\w*)|%\w{0,20}(?:key|token|secret)\w{0,20}%"
                         r"|\{\{\s*\w{0,20}(?:key|token|secret))")
_URL = re.compile(r'''https?://[^\s<>"']{1,2048}''')


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def _remote_url(text: str) -> bool:
    # Parse IPv6 and userinfo correctly, without DNS requests or network access.
    for value in _URL.findall(text):
        try:
            host=(urlsplit(value).hostname or '').rstrip('.')
            if host=='localhost' or host.endswith('.localhost') or host=='0.0.0.0':
                continue
            try:
                address=ipaddress.ip_address(host)
                mapped=getattr(address,'ipv4_mapped',None)
                if address.is_loopback or mapped and mapped.is_loopback:
                    continue
            except ValueError:
                pass
        except ValueError:
            pass
        return True
    return False


def _hit(patterns, text, negatable=False):
    for rx in patterns:
        for m in rx.finditer(text):
            if negatable and _NEG.search(text[max(0, m.start() - 30):m.start()]):
                continue
            return True
    return False


def scan(text) -> str | None:
    """None για ασφαλές κείμενο, αλλιώς σύντομη αιτιολογία με την κατηγορία."""
    text = str(text)
    if len(text) > MAX_SCAN:
        return f"υπερβολικό μήκος (>{MAX_SCAN} χαρακτήρες· δεν σαρώνεται μισό κείμενο)"
    body = text[1:] if text.startswith("﻿") else text  # BOM στην αρχή αρχείου είναι αθώο
    if _INVISIBLE.search(body):
        return "αόρατους χαρακτήρες ή χαρακτήρες αλλαγής κατεύθυνσης (zero-width/bidi)"
    if _HTML.search(body.casefold()):
        return "κρυφό περιεχόμενο HTML (σχόλιο ή κρυμμένο στοιχείο)"
    low = _norm(text)
    if _hit(_OVERRIDE, low, negatable=True):
        return "εντολή παράκαμψης των οδηγιών"
    if _hit(_ROLE, low, negatable=True):
        return "αλλαγή ρόλου ή ταυτότητας του agent"
    if _hit(_CONCEAL, low):
        return "απόκρυψη πληροφορίας από τον χρήστη"
    if ((_hit(_EXFIL_PAIR, low, negatable=True) and _remote_url(low)) or _hit(_EXFIL_VERBAL, low, negatable=True)
            or (_URL_SECRET.search(low) and _remote_url(low))):
        return "διαρροή μυστικών ή μεταβλητών περιβάλλοντος"
    return None


def refusal(reason: str) -> str:
    return f"ΑΠΟΡΡΙΨΗ: ΑΠΟΡΡΙΦΘΗΚΕ η σημείωση επειδή περιέχει {reason}· δεν αποθηκεύτηκε."

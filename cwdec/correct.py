# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Safe post-correction for CW text.

Design rules (deliberately conservative):

1. Only tokens whose *target is in a closed vocabulary* may be corrected.
   Callsigns are an open set, so they are **never** rewritten — only flagged.
2. Distance is measured in *Morse code space*, not letter space, because CW
   errors are single-element slips (V "...-" misread as 5 ".....").
3. Tokens containing digits are never corrected (a wrong digit ruins a callsign
   or a signal report).
4. A correction must be unambiguous: exactly one candidate at the minimal
   distance, otherwise nothing is changed.
5. Every change is recorded so the UI can show it and the user can undo it.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- vocabulary
PROWORDS = {
    "CQ", "DE", "K", "KN", "TU", "TNX", "TKS", "GL", "GM", "GE", "GA", "GN",
    "R", "RR", "RST", "5NN", "599", "579", "559", "569", "589", "73", "88",
    "QRZ", "QRM", "QRN", "QSB", "QTH", "QSL", "QRL", "QRT", "QRX", "QRS", "QRQ",
    "QSY", "QSP", "QTC", "PSE", "RPT", "AGN", "ES", "UR", "HR", "NAME", "QRA",
    "OP", "ANT", "RIG", "WX", "TEMP", "FB", "OM", "YL", "XYL", "DR", "SK", "AR",
    "BK", "CFM", "HW", "CPY", "ABT", "PWR", "WPM", "TEST", "CONTEST", "PORT",
    "NR", "SIG", "RPRT", "CUL", "HPE", "CU", "GB", "GA", "MNI", "TNX",
    "FER", "GUD", "NW", "OW", "ABT", "HI", "HIS", "HR", "WID", "VY", "PWR",
}

# 常见 ITU 前缀（只需用于"像不像呼号"的判断，不追求完整）
PREFIXES = set("""
K W N A VE VK ZL JA JE JF JG JH JI JJ JK JL JM JN JO JP JQ JR JS
DL DK DA DB DC DD DF DG DH DJ DO DQ DR
G M F I S PA ON OZ LA SM OH YO LZ YU S5 9A HA OK OM SP UA UR UN UT
EA CT CS CU EI GJ GM GW GD GI
PY LU CE CX HK YV OA HC
BY BD BG BH BI BJ BK BL BM BN BO BP BQ BR BS BT BU BV BW BX
EX UN UZ EY EZ
4X 4Z 5B 9K A4 A6 A7 A9 AP
KH KL KP VK8 VK9 ZS 9J 9V HS XV XU YB 9M DU
VP2 VP5 VP8 VP9 8P 9Y
""".split())

# 常见英文词：这些词即使摩斯距离很近也绝不动（避免把正常行文改坏）
ENGLISH_WORDS = set("""
BUT FIND HIM LIKE SEE USE HAD HAS DID DOES NOTE MORE MOST VERY WELL BACK DOWN OVER
TIME LIFE WORK HAND PART PLACE WEEK CASE POINT GROUP NUMBER WAY
WATER ROOM MOTHER AREA MONEY STORY FACT MONTH RIGHT STUDY BOOK
EYE JOB WORD BUSINESS ISSUE SIDE KIND HEAD HOUSE SERVICE FRIEND
FATHER POWER HOUR GAME LINE END MEMBER LAW CAR CITY COMMUNITY NAME
PRESIDENT TEAM MINUTE IDEA KID BODY INFORMATION BACK PARENT FACE
OTHERS LEVEL OFFICE DOOR HEALTH PERSON ART WAR HISTORY PARTY RESULT
CHANGE MORNING REASON RESEARCH GIRL GUY MOMENT AIR TEACHER FORCE
EDUCATION FOOT BOY AGE POLICY PROCESS MUSIC MARKET SENSE NATION
PLAN COLLEGE INTEREST DEATH EXPERIENCE EFFECT CLASS CONTROL CARE
FIELD DEVELOPMENT ROLE EFFORT RATE HEART DRUG SHOW LEAD LIGHT VOICE
WIFE POLICE MIND PRICE REPORT DECISION SON VIEW RELATIONSHIP TOWN
ROAD ARM DIFFERENCE VALUE BUILDING ACTION MODEL SEASON SOCIETY
TAX DIRECTOR POSITION PLAYER RECORD PAPER SPACE GROUND FORM EVENT
THE AND IS MY FOR HE THAT ANYONE FREE TO OF NOT YOU WITH THIS ARE WAS
WERE BEEN BE HIS HER THEY THEM THEN THAN THERE HERE WHERE WHEN WHAT WHY HOW
ALL ANY SOME MORE MOST MUCH MANY SUCH ONLY ALSO INTO FROM UPON OVER UNDER
LORD SHEPHERD SHALL WANT WANT GIVE THANKS UNTO BECAUSE MERCY ENDURETH FOREVER
WELCOME WIKIPEDIA ENCYCLOPEDIA EDIT CAN WILL WOULD COULD SHOULD MAY MIGHT
GOOD GREAT NEW OLD FIRST LAST LONG SHORT HIGH LOW RIGHT LEFT
DAY NIGHT TIME YEAR MONTH WEEK HOUR MINUTE SECOND
MAN WOMAN CHILD PEOPLE WORLD LIFE WORK HOME HOUSE ROAD CITY
IS IT ITS AS AT BY IN ON OR IF SO NO YES ONE TWO THREE
""".split())

CALL_RE = re.compile(r"^[A-Z]{1,3}\d[A-Z]{1,4}(/[A-Z0-9]{1,4})?$")
DIGIT_RE = re.compile(r"\d")

# ---------------------------------------------------------------- morse space
MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
}


def to_morse(token: str) -> str | None:
    out = []
    for ch in token:
        code = MORSE.get(ch)
        if code is None:
            return None
        out.append(code)
    return "".join(out)


def morse_distance(a: str, b: str) -> int:
    """Levenshtein distance over Morse element strings ('.' and '-')."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


_PROWORD_MORSE = {w: to_morse(w) for w in PROWORDS}
_PROWORD_MORSE = {w: m for w, m in _PROWORD_MORSE.items() if m}


# ---------------------------------------------------------------- classify
def classify(token: str) -> str:
    """'proword' | 'call' | 'call_like' | 'digits' | 'other'."""
    t = token.upper()
    if t in PROWORDS:
        return "proword"
    if CALL_RE.match(t):
        core = t.split("/")[0]
        letters = re.match(r"^([A-Z]{1,3})\d", core)
        prefix = letters.group(1) if letters else ""
        return "call" if prefix in PREFIXES else "call_like"
    if t.isdigit():
        return "digits"
    return "other"


# ---------------------------------------------------------------- correction
def correct_token(token: str, max_distance: int = 1):
    """Return (corrected_or_None, reason). Only prowords/plain words are touched."""
    t = token.upper()
    if len(t) < 3 or len(t) > 5:
        return None, "length"              # 只处理 3–5 字符（常用词基本都是这个长度）
    if t in ENGLISH_WORDS:
        return None, "english_word"
    kind = classify(t)
    # 呼号/纯数字一律不动（保护呼号、信号报告）
    if kind in ("call", "call_like", "digits"):
        return None, "callsign_or_digits"
    # 非呼号的词里允许含数字（例如 "GI5E" 其实是 "GIVE" 的误判）

    if t in PROWORDS:
        return None, "already_exact"

    src = to_morse(t)
    if not src:
        return None, "not_encodable"

    best = []
    best_d = 99
    for word, code in _PROWORD_MORSE.items():
        if word == t:
            continue
        if len(word) < 3:                  # 候选词太短（ES/HW/FB）容易撞上正常单词
            continue
        if DIGIT_RE.search(word) != DIGIT_RE.search(t):
            continue                       # 不能凭空引入/去掉数字
        if abs(len(code) - len(src)) != 1:
            continue                       # 只接受"多一个或少一个码元"
        d = morse_distance(src, code)
        if d < best_d:
            best_d, best = d, [word]
        elif d == best_d:
            best.append(word)

    if best_d == 0:
        return None, "already_exact"
    if best_d > max_distance:
        return None, "far"
    if len(best) != 1:
        return None, "ambiguous"
    return best[0], f"morse_d{best_d}"


def correct_text(text: str, enabled: bool = True):
    """Return (new_text, changes) where changes is a list of dicts."""
    if not enabled or not text:
        return text, []
    out = []
    changes = []
    for raw in text.split(" "):
        if not raw:
            out.append(raw)
            continue
        fixed, reason = correct_token(raw)
        if fixed and fixed != raw:
            changes.append({"from": raw, "to": fixed, "reason": reason})
            out.append(fixed)
        else:
            out.append(raw)
    return " ".join(out), changes


def _is_call(part: str) -> bool:
    return bool(CALL_RE.match(part))


def segment_token(tok: str, max_parts: int = 8) -> list[str] | None:
    """Split a merged token into known words/callsigns (spacing repair only).

    "CQCQDESQ5VCO" -> ["CQ", "CQ", "DE", "SQ5VCO"]
    Only accepted when every part is a proword or a callsign-shaped string, and
    the segmentation is unique.  Letters are never changed.
    """
    t = tok.upper()
    if len(t) < 4 or len(t) > 24:
        return None
    allow_calls = len(t) >= 6
    sols: list[list[str]] = []

    def walk(pos: int, acc: list[str]):
        if len(sols) > 8:
            return
        if pos == len(t):
            if len(acc) >= 2:
                sols.append(list(acc))
            return
        if len(acc) >= max_parts:
            return
        for end in range(pos + 1, min(len(t), pos + 9) + 1):
            part = t[pos:end]
            ok = part in PROWORDS or (allow_calls and len(part) >= 3 and _is_call(part))
            if ok:
                acc.append(part)
                walk(end, acc)
                acc.pop()

    walk(0, [])
    if len(sols) == 1:
        return sols[0]
    if sols:
        # 多种切法时：取"段数最少"的那种（尽量保住长呼号），且必须唯一
        best_n = min(len(x) for x in sols)
        cands = [x for x in sols if len(x) == best_n]
        if len(cands) == 1:
            return cands[0]

    return None


def repair_spacing(text: str) -> tuple[str, list[dict]]:
    """Insert spaces inside over-long merged tokens (no letter changes)."""
    out, changes = [], []
    for tok in text.split(" "):
        if not tok:
            out.append(tok)
            continue
        parts = segment_token(tok)
        if parts:
            out.extend(parts)
            changes.append({"from": tok, "to": " ".join(parts), "reason": "spacing"})
        else:
            out.append(tok)
    return " ".join(out), changes

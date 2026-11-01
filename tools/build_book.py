#!/usr/bin/env python3
"""
Build a Quarto site (several small Quarto projects) from the Baha'u'llah source volumes.

    python3 tools/build_book.py --src source --out .

Settings: tools/project.json.  Hand-edited overrides (never overwritten): tools/titles.json,
tools/headings.json, tools/options.json, tools/volume_titles.json
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path


LONG_DOC_WORDS = 2500
SPLIT_PARA_WORDS = 220
TARGET_CHUNK_WORDS = 120
EDITION_WORD_LIMIT = 450_000     # Gemini Notebook allows 500,000 words per source; leave a safety margin
MIN_EMBEDDED_QUOTE_WORDS = 12
GAP_WORDS = 6000                 # sections longer than this get sub-parts
GAP_TARGET = 3000
SHORT = 16                       # max words for a line to count as a possible heading

SEP_RE = re.compile(r"^\s*={5,}\s*$")
HEAD_RE = re.compile(r"^(BH[A-Za-z]*\d+)\b[ \t]*(.*)$")
KEEP_ID_RE = re.compile(r"^BH\d+$")
INCIPIT_TITLES = True
PLACEHOLDER_RE = re.compile(r"text to be (located|typed)", re.I)
META_RE = re.compile(r"^(To|Date|From|Place|Addressee|Recipient|Written)\s*:\s*\S", re.I)
BASMALA_RE = re.compile(r"^(in (the|his) name|he is\b)", re.I)


def words(t):
    return len(t.split())


# ---------------------------------------------------------------- text cleanup
def fix_encoding(s):
    s = s.replace("ImÃims", "Imams")
    s = s.replace("Ãi", "a")
    s = re.sub(r"Ã(?![A-Za-z])", "a", s)
    return s


import unicodedata

ITAL_OPEN, ITAL_CLOSE = "\ue000", "\ue001"


def _braced(s, start):
    """index of the } matching the { at s[start]"""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def latex_to_md(s):
    r"""\footnote{..} -> ^[..]; \textit{..} -> italics; \d{h} / \u{a} -> accented letters."""
    s = re.sub(r"\\d\{(.)\}", lambda m: unicodedata.normalize("NFC", m.group(1) + "\u0323"), s)
    s = re.sub(r"\\u\{(.)\}", lambda m: unicodedata.normalize("NFC", m.group(1) + "\u0306"), s)
    for cmd in ("footnote", "textit"):
        key = "\\" + cmd + "{"
        while True:
            i = s.find(key)
            if i < 0:
                break
            j = _braced(s, i + len(key) - 1)
            if j < 0:
                s = s[:i] + s[i + len(key):]
                continue
            inner = latex_to_md(s[i + len(key):j])
            if cmd == "footnote":
                inner = inner.replace("[", "\\[").replace("]", "\\]")
                rep = "^[" + inner + "]"
            else:
                rep = ITAL_OPEN + inner + ITAL_CLOSE
            s = s[:i] + rep + s[j + 1:]
    return s


def strip_footnotes(s):
    while True:
        i = s.find("\\footnote{")
        if i < 0:
            return s
        j = _braced(s, i + len("\\footnote{") - 1)
        s = s[:i] + (s[j + 1:] if j >= 0 else s[i + 10:])


def clean_text(s):
    s = latex_to_md(fix_encoding(s.replace("\u00a0", " ")))
    s = s.replace("`", "ʿ")
    s = s.replace("---", "—")
    s = re.sub(r"\s*--\s*", "—", s)
    s = re.sub(r"\s-\s", "—", s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    s = s.replace("*", r"\*").replace("_", r"\_")
    s = s.replace(ITAL_OPEN, "*").replace(ITAL_CLOSE, "*")
    s = re.sub(r"^#", r"\#", s)
    s = re.sub(r"^(\d+)\.", r"\1\\.", s)
    s = re.sub(r"^([-+>])", r"\\\1", s)
    return s


def plain(s):
    """clean_text without markdown escaping (for headings / titles)."""
    return clean_text(s).replace("\\*", "*").replace("\\_", "_").replace("\\#", "#").replace("\\.", ".")


def split_sentences(p):
    parts = re.split(r'(?<=[.!?])(["”’)\]]?)\s+(?=["“‘(A-Z\\])', p)
    out, i = [], 0
    while i < len(parts):
        seg = parts[i]
        if i + 1 < len(parts):
            seg += parts[i + 1]
            i += 2
        else:
            i += 1
        out.append(seg)
    return out


def split_long_paragraph(p):
    if words(p) <= SPLIT_PARA_WORDS:
        return [p]
    chunks, cur, cw = [], [], 0
    for s in split_sentences(p):
        cur.append(s)
        cw += words(s)
        if cw >= TARGET_CHUNK_WORDS:
            chunks.append(" ".join(cur))
            cur, cw = [], 0
    if cur:
        if chunks and cw < 35:
            chunks[-1] += " " + " ".join(cur)
        else:
            chunks.append(" ".join(cur))
    return chunks


# ---------------------------------------------------------------- quotations
EMBEDDED_QUOTE_RE = re.compile(r'^(?P<lead>.*?[:,]) "(?P<quote>[^"]+)"(?P<rest>(?:\s.*)?)$', re.S)


def split_embedded_quote(raw):
    if raw.count('"') != 2:
        return None
    m = EMBEDDED_QUOTE_RE.match(raw.strip())
    if not m or words(m.group("quote")) < MIN_EMBEDDED_QUOTE_WORDS or words(m.group("lead")) < 2:
        return None
    return m.group("lead").strip(), '"' + m.group("quote").strip() + '"', m.group("rest").strip()


def is_whole_quote(p):
    q = p.strip()
    return q.startswith('"') and q.count('"') == 2 and q.endswith(('"', '."', '!"'))


# ---------------------------------------------------------------- headings
PRAYER_LEAD_RE = re.compile(r"^(?:And\s+)?(?:this\s+was\s+)?(?:among\s+)?(?:his|His)?\s*(prayers?)\b(.*)$", re.I)
MONTH_RE = re.compile(r"^\((\d+)\.\s*(.+?)\)$")
ORD_CHAPTER_RE = re.compile(r"^((?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth)\s+chapter)\s*:\s*(.+)$", re.I)
SURAH_RE = re.compile(r"^Surah of [^.]{2,70}$")
GATE_RE = re.compile(r"^Gate\s+(\S+)\s+of\s+the\s+(\S+)\s+Unity\b[\s,.:;]*$", re.I)
CHAPTER_RE = re.compile(r"^[\[(]?\s*(Chapter|Book|Part)\s+([A-Za-z\-]+|\d+)\s*[\])]?\s*(?:[,:]\s*(.*))?$", re.I)
BRACKET_RE = re.compile(r"^\[(?:(\d+)\.\s*)?([^\[\]]+?)\]$")
PAREN_RE = re.compile(r"^\(([^()]+?)\)$")
WEEKDAY_RE = re.compile(r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\s*:?$", re.I)
PRAYER_TITLE_RE = re.compile(r"^(?:His |A |The )?(?:Prayer|Supplication)\b[^.]*:$", re.I)
ORDINAL_UNIT_RE = re.compile(r"^The\s+\w+\s+(Vahid|Unity|Gate|Chapter|Section|Book|Part)\s*[.:]?$", re.I)
DATED_PRAYER_RE = re.compile(r"^(?:A\s+)?(?:Prayer|Du'a'?|Supplication)\b[^.!?:]{0,140}\b(?:Year|Night|Day|Month|Hour)s?\b[^.!?:]{0,80}$", re.I)
VAHID_RE = re.compile(r"^\[Vahid\s+(\d+),\s*Bab\s+(\d+)(?:,\s*([^\]]+))?\]$", re.I)
VAHID_MONTH_RE = re.compile(r"^The\s+\w+\s+(?:Vahid|month),\s*the month of\s+.+?:?$", re.I)
FOOT_TAIL_RE = re.compile(r"^(.*?)\s*\\footnote\{(.*)\}\s*$", re.S)
SUBTITLE_START_RE = re.compile(r"^(concerning|on|in explanation|explaining|regarding|about)\b", re.I)


def up1(s):
    s = s.strip().rstrip(":;,. ")
    return s[:1].upper() + s[1:] if s else s


def title_words(s):
    return " ".join(w[:1].upper() + w[1:] if i == 0 else w for i, w in enumerate(s.split()))


def detect(raw, nxt):
    """Return (level, text, consumed_next_line, unity) or None."""
    r = raw.strip()
    w = words(r)
    if w > SHORT + 18 or not r:
        return None
    m = MONTH_RE.match(r)
    if m:
        n = m.group(2).strip()
        return 2, ("The " + n if n.lower().startswith("month") else f"{m.group(1)}. {up1(n)}"), 0, None
    m = ORD_CHAPTER_RE.match(r)
    if m:
        return 2, f"{m.group(1).title()}: {m.group(2).strip()}", 0, None
    if r.endswith(":") and w <= 32 and re.match(r"^(And|Among|This)\b", r) and re.search(r"\bprayers?\b", r, re.I):
        m = PRAYER_LEAD_RE.match(r.rstrip(":").strip())
        if m:
            rest = m.group(2).strip()
            rest = re.sub(r"^was\s+this\s+one\s+", "", rest, flags=re.I)
            rest = re.sub(r"\s+was(\s+this)?$", "", rest, flags=re.I)
            rest = re.sub(r"^was\s+", "", rest, flags=re.I)
            rest = re.sub(r"\s*\((?:peace|blessings)[^)]*\)", "", rest, flags=re.I).strip()
            return 2, ("Prayer " + rest).strip(), 0, None
    if w > SHORT:
        return None
    if ORDINAL_UNIT_RE.match(r):
        return 2, up1(r), 0, None
    if DATED_PRAYER_RE.match(r) and 4 <= w <= 30:
        return 2, up1(r), 0, None
    m = VAHID_RE.match(r)
    if m:
        name = (m.group(3) or "").strip()
        return 3, f"Bab {m.group(2)}" + (f": {up1(name)}" if name else ""), 0, f"Vahid {m.group(1)}"
    if VAHID_MONTH_RE.match(r):
        return 2, up1(r), 0, None
    m = GATE_RE.match(r)
    if m:
        text = f"Gate {m.group(1).title()} of the {m.group(2).title()} Unity"
        consumed = 0
        if nxt and words(nxt) <= 22 and not BASMALA_RE.match(nxt) and (nxt[:1].islower() or SUBTITLE_START_RE.match(nxt)):
            text += ": " + up1(nxt)
            consumed = 1
        return 3, text, consumed, f"The {m.group(2).title()} Unity"
    if SURAH_RE.match(r) and w <= 10:
        return 2, r, 0, None
    m = CHAPTER_RE.match(r)
    if m and not r.endswith("."):
        text = f"{m.group(1).title()} {m.group(2).title() if not m.group(2).isdigit() else m.group(2)}"
        tail = (m.group(3) or "").strip()
        consumed = 0
        if tail:
            text += ": " + up1(tail)
        elif nxt and words(nxt) <= 14 and SUBTITLE_START_RE.match(nxt):
            text += ": " + up1(nxt)
            consumed = 1
        return 2, text, consumed, None
    m = BRACKET_RE.match(r)
    if m and re.search(r"[A-Za-z]", m.group(2)) and not re.search(r"\bQ\d+:\d+|\d+:\d+|translation|\bnot available\b", m.group(2)) and w <= 14:
        return (2 if m.group(1) else 3), (f"{m.group(1)}. " if m.group(1) else "") + up1(m.group(2)), 0, None
    m = PAREN_RE.match(r)
    if m and re.match(r"[A-Za-z]", m.group(1)) and not re.search(r"\b(peace|blessings|salutations?|may )\b", m.group(1), re.I) and w <= 14:
        return 3, up1(m.group(1)), 0, None
    if WEEKDAY_RE.match(r):
        return 2, up1(r), 0, None
    if PRAYER_TITLE_RE.match(r) and w <= 12:
        return 2, up1(r), 0, None
    return None



# ---------------------------------------------------------------- automatic headings (long writings with no markers)
VERSE_LEAD_RE = re.compile(r'^(?:And\s+)?(?:God|the Almighty)\b[^":“]{0,50}\b(?:said|saith|says|declared)\s*:?\s*["“]', re.I)
QUESTION_LEAD_RE = re.compile(
    r"^(?:And\s+)?(?:As\s+(?:to|for|regarding)|Concerning|Regarding)\s+(?:that\s+which|what|the\s+(?:question|matter))\s+"
    r"(?:thou|you|ye)\s+(?:didst|hast|have|did)?\s*(?:ask(?:ed)?|inquire[d]?|enquire[d]?)\s*(?:concerning|about|regarding|of|touching)?\s*(?P<t>.*)$", re.I)
TRAIL_STOP = {"which", "that", "of", "and", "the", "in", "to", "a", "an", "is", "as", "for", "by", "with", "from", "on"}


def _trim_words(text, n):
    ws = text.split()
    cut = len(ws) > n
    ws = ws[:n]
    while ws and ws[-1].lower().strip(",.;:") in TRAIL_STOP:
        ws.pop()
    out = " ".join(ws).strip(" ,.;:—-\"“”")
    return out, cut


def incipit_chunks(body, a, b, target_words, level):
    """Split body[a:b] into even parts at paragraph boundaries; name each by its opening words."""
    total = sum(words(l) for l in body[a:b])
    n = max(2, round(total / target_words))
    target = total / n
    starts, acc = [a], 0
    for i in range(a, b):
        acc += words(body[i])
        if acc >= target * len(starts) and i + 1 < b and len(starts) < n:
            j = i + 1
            if words(body[i]) <= 15 and i > starts[-1]:     # keep a lone basmala with its text
                j = i
            starts.append(j)
    heads = {}
    for k, st in enumerate(starts, 1):
        j = st
        while j < b - 1 and words(body[j]) <= 15 and BASMALA_RE.match(body[j].strip()):
            j += 1
        inc, cut = _trim_words(re.sub(r'^["“]|^Say:\s*', "", body[j].strip()), 8)
        heads[st] = (level, f"Section {k}: “{inc}…”")
    return heads


def auto_headings(body, kind_hint=None):
    """Return ({idx: (level, text)}, kind) for a long writing that has no explicit section markers."""
    verse = {}
    for i, l in enumerate(body):
        if words(l) <= 70 and VERSE_LEAD_RE.match(l.strip()):
            m = re.search(r'["“](.+?)["”]', l)
            if m:
                q, cut = _trim_words(m.group(1), 9)
                if q:
                    verse[i] = (2, f"On the verse “{q}{'…' if cut else ''}”")
    if len(verse) >= 3:
        return verse, "verse"
    quest = {}
    for i, l in enumerate(body):
        m = QUESTION_LEAD_RE.match(l.strip()) if words(l) <= 400 else None
        if m:
            t_full = m.group("t")
            t, cut = _trim_words(re.split(r'\s*(?:—|--|["“])', t_full)[0], 10)
            qm = re.search(r'["“]([^"”]{5,})["”]', t_full)
            if qm and len(t.split()) <= 3:
                qt, qcut = _trim_words(qm.group(1), 8)
                t = f"{t} “{qt}{'…' if qcut else ''}”".strip()
            quest[i] = (2, "On " + t if t else "Reply to a question")
    if len(quest) >= 3:
        return quest, "question"
    # incipit parts
    return incipit_chunks(body, 0, len(body), 2500, 2), "incipit"


def incipit_title(body):
    """Title for an untitled writing: its first substantive words (skipping a basmala-style opener)."""
    lines = [l.strip() for l in body if l.strip()]
    if not lines:
        return ""
    j = 0
    while j < len(lines) - 1 and words(lines[j]) <= 18 and BASMALA_RE.match(lines[j]):
        j += 1
    text = re.sub(r'^["“\[(]+', "", lines[j])
    t, cut = _trim_words(text, 9)
    if not t:
        t, cut = text[:50].strip(), True
    return t + ("…" if cut else "")

# ---------------------------------------------------------------- one writing
def build_doc_qmd(doc, title_override=None, heading_overrides=None):
    body = list(doc["lines"])
    meta = []
    while body and META_RE.match(body[0].strip()):
        meta.append(body.pop(0).strip())

    title = (title_override or doc["title"] or "").strip()
    derived = False
    if not title:
        for mline in meta:
            if mline.lower().startswith("to:"):
                t = re.sub(r"\s*\?+\s*$", "", mline[3:].strip())
                title, derived = "To " + t, True
                break
    if not title and body and words(body[0]) <= 8 and re.match(r"^(A |An |The )?(Prayer|Tablet|Letter|Epistle|Commentary|Treatise|Answer|Sermon|Supplication|Tafsir|Khutba)\b", body[0].strip()):
        title, derived = body.pop(0).strip().rstrip(":."), True

    incipit_used = False
    if not title and INCIPIT_TITLES:
        title, incipit_used = incipit_title(body), True

    h1 = f"{doc['id']}: {plain(title)}" if title else doc["id"]
    out = [f"# {h1} {{.unnumbered}}", ""]
    for mline in meta:
        out += [f"*{plain(mline)}*", ""]

    # ---- find markers
    markers, notes, i = {}, {}, 0
    while i < len(body):
        nxt = body[i + 1].strip() if i + 1 < len(body) else ""
        line = body[i]
        ft = FOOT_TAIL_RE.match(line.strip())
        d = detect(ft.group(1), nxt) if (ft and words(ft.group(1)) <= 14) else detect(line, nxt)
        if d:
            if ft and words(ft.group(1)) <= 14:
                notes[i] = ft.group(2).strip()
            markers[i] = d
            i += 1 + d[2]
        else:
            i += 1
    consumed = {i + 1 for i, d in markers.items() if d[2]}

    # manual overrides: insert before first paragraph that starts with `match`
    inserts = {}
    for ov in heading_overrides or []:
        for j, l in enumerate(body):
            if l.strip().startswith(ov["match"]):
                inserts.setdefault(j, []).append((int(ov.get("level", 2)), ov["text"]))
                break

    auto_kind = None
    if not markers and not inserts and sum(words(l) for l in body) > LONG_DOC_WORDS:
        ah, auto_kind = auto_headings(body)
        for i2, (lv2, tx2) in ah.items():
            inserts.setdefault(i2, []).append((lv2, tx2))
    # subdivide any section that is still very long
    hl = {i2: d[0] for i2, d in markers.items()}
    for i2, v in inserts.items():
        hl[i2] = min([hl.get(i2, 9)] + [lv for lv, _ in v])
    edges = sorted(set([0] + list(hl) + [len(body)]))
    gap_added = 0
    for a, b in zip(edges, edges[1:]):
        if sum(words(l) for l in body[a:b]) > GAP_WORDS and b - a >= 4:
            parent = hl.get(a, 2)
            for i2, (lv2, tx2) in incipit_chunks(body, a if a not in hl else a + 1, b, GAP_TARGET, min(parent + 1, 4)).items():
                inserts.setdefault(i2, []).append((lv2, tx2))
                gap_added += 1
    levels = [d[0] for d in markers.values()] + [lv for v in inserts.values() for lv, _ in v]
    if any(d[3] for d in markers.values()):
        levels.append(2)          # group headings (Unity / Vahid) are generated at level 2
    shift = (min(levels) - 2) if levels else 0

    total_words = sum(words(l) for l in body)
    first = min(list(markers) + list(inserts)) if (markers or inserts) else None
    pre_words = sum(words(l) for l in body[:first]) if first else 0
    stats = dict(incipit=incipit_used, gap=gap_added, auto=auto_kind, words=total_words + sum(words(m) for m in meta), headings=0, blockquotes=0, split=0, derived_title=derived and not doc["title"], titled=bool(title))

    if first and pre_words >= 40:
        pre_h = "Opening Prayer" if (first in markers and markers[first][1].startswith("Prayer")) else "Introduction"
        out += [f"## {pre_h} {{.unnumbered}}", ""]
        stats["headings"] += 1
    last_h2 = None
    for idx, raw in enumerate(body):
        if idx in consumed:
            continue
        for lv, tx in inserts.get(idx, []):
            lv = max(2, lv - shift)
            out += [f"{'#' * lv} {plain(tx)} {{.unnumbered}}", ""]
            stats["headings"] += 1
            if lv == 2:
                last_h2 = plain(tx)
        if idx in markers:
            lv, tx, _, unity = markers[idx]
            lv = max(2, lv - shift)
            if unity and last_h2 != unity:
                last_h2 = unity
                out += [f"## {last_h2} {{.unnumbered}}", ""]
                stats["headings"] += 1
            out += [f"{'#' * lv} {plain(tx)} {{.unnumbered}}", ""]
            stats["headings"] += 1
            if lv == 2:
                last_h2 = plain(tx)
            if idx in notes:
                out += [f"*{plain(notes[idx])}*", ""]
            continue
        p = clean_text(raw)
        if re.match(r"^\[[^\[\]]+\]$", raw.strip()):          # whole-line editorial note
            out += [f"*{plain(raw.strip())}*", ""]
            continue
        if is_whole_quote(raw.strip()):
            out += ["> " + p, ""]
            stats["blockquotes"] += 1
            continue
        emb = split_embedded_quote(raw)
        if emb:
            lead, quote, rest = emb
            for c in split_long_paragraph(clean_text(lead)):
                out += [c, ""]
            out += ["> " + clean_text(quote), ""]
            stats["blockquotes"] += 1
            if rest:
                for c in split_long_paragraph(clean_text(rest)):
                    out += [c, ""]
            continue
        chunks = split_long_paragraph(p)
        if len(chunks) > 1:
            stats["split"] += 1
        for c in chunks:
            out += [c, ""]
    return "\n".join(out).rstrip() + "\n", stats


# ---------------------------------------------------------------- parsing
CFG = {}


def parse_source(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks, cur = [], []
    for line in text.splitlines():
        if SEP_RE.match(line):
            blocks.append(cur)
            cur = []
        else:
            cur.append(line)
    blocks.append(cur)
    docs = []
    for b in blocks:
        b = [l.strip() for l in b if l.strip() and not re.match(r"^`{3,}\s*\w*$", l.strip())]   # drop stray ``` lines
        if not b:
            continue
        m = HEAD_RE.match(b[0])
        if not m:
            docs.append(dict(id=None, title="", lines=b, bad=True))
        else:
            docs.append(dict(id=m.group(1), title=m.group(2).strip(), lines=b[1:], bad=False))
    return docs


def volume_number(path):
    m = re.match(r"^(\d+)", path.name)
    return int(m.group(1)) if m else None


def volume_years(name):
    t = re.sub(r"\.txt\.md$|\.md$", "", name)
    if re.search(r"unknown", t, re.I):
        return []
    return [int(y) for y in re.findall(r"(?<!\d)(18\d\d)(?!\d)", t)]


def volume_title(path, overrides):
    num = volume_number(path)
    if str(num) in overrides:
        return overrides[str(num)]
    stem = path.name
    for suf in (".txt.md", ".md", ".txt"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    m = re.match(r"^\d+(?:-\d+)*[\s_-]+(.*)$", stem)
    t = m.group(1) if m else stem
    t = t.replace("Baha_u_llah", "Baha'u'llah").replace("Baha u llah", "Baha'u'llah")
    t = re.sub(r"_+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(Writings of )?Baha'u'llah,?\s*(in\s+)?", "", t, flags=re.I) or t
    t = re.sub(r"\byear (\d{4}(?:-\d{4})?) A\.H\.?", r"\1 A.H.", t)
    t = re.sub(r"\bA\.H\.? \(?(\d{4}(?:-\d{4})?)\)?", r"A.H. (\1)", t)
    t = re.sub(r"\s+(part [IVX]+)$", lambda mm: ", Part " + mm.group(1).split()[1].upper(), t, flags=re.I)
    t = re.sub(r"(?<=\d{4})-(?=\d{4})", "–", t)
    t = re.sub(r"\s*\(\s*", " (", t)
    t = re.sub(r"\s+,", ",", t)
    return t[:1].upper() + t[1:]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def edition_cost(text, toc_depth):
    """Words this page adds to a Word/PDF edition: every whitespace-separated token that contains a letter or digit (bare
    markup such as ## or > is not counted, but the contents-list entry for each heading is) plus the contents-list entry (heading words + page number) for headings shown in the TOC."""
    n = sum(1 for t in text.split() if re.search(r"\w", t))
    for m in re.finditer(r"^(#{1,6}) (.*)$", text, re.M):
        if len(m.group(1)) <= toc_depth:
            n += len(m.group(2).split()) + 1
    return n


def demote(qmd):
    return "\n".join(re.sub(r"^(#{1,5}) ", r"#\1 ", l) for l in qmd.split("\n"))


def group_balanced(units, limit):
    """units: [(payload, words)] in order -> list of (payloads, words), fewest groups, evenly sized, each <= limit."""
    total = sum(w for _, w in units)
    if not units:
        return []
    n0 = max(1, -(-total // limit))
    for n_try in range(n0, n0 + 8):
        cap = min(limit, int(total / n_try * 1.12))
        groups, cur, cw = [], [], 0
        for p, w in units:
            if cur and cw + w > cap:
                groups.append((cur, cw))
                cur, cw = [], 0
            cur.append(p)
            cw += w
        if cur:
            groups.append((cur, cw))
        if len(groups) <= n_try and all(g[1] <= limit for g in groups):
            return groups
    return groups


SEARCHBOX_JS = """<script>
/* adds a search box to the top of the sidebar; submitting opens the results page (/search.html?q=...) */
(function () {
  try {
    var host = document.querySelector('#quarto-sidebar .sidebar-menu-container') || document.querySelector('#quarto-sidebar');
    if (!host || document.querySelector('.sidebar-sitesearch')) return;
    var f = document.createElement('form');
    f.className = 'sitesearch sidebar-sitesearch'; f.action = '/search.html'; f.method = 'get';
    f.innerHTML = '<input type="search" name="q" placeholder="Search all writings\u2026" aria-label="Search all writings"><button type="submit">Go</button>';
    host.insertBefore(f, host.firstChild);
  } catch (e) {}
})();
</script>
"""

# ---------------------------------------------------------------- main
DEFAULT_CFG = {
    "title": "A Compilation of Baha'u'llah's Writings",
    "subtitle": "Translated Using GPT-4",
    "author": "Baha'u'llah",
    "site_url": "https://bahaullah.peopleofbaha.world",
    "file_prefix": "Bahaullah-Compilation-GPT",
    "id_prefix": "BH",
    "exclude_ids": ["BH00069"],
    "footer_left": "Created by the Baha'i Translation Project",
    "footer_right": "Translations used were created with GPT 4",
    "html_theme": "minty",
    "html_fontsize": "10pt",
    "html_toc_depth": 2,
    "docx_reference": "reference1.docx",
    "docx_toc_depth": 2,
    "layout": "single",
    "section_word_limit": 900000,
    "page_word_limit": 25000,
    "edition_word_limit": 480000,
    "parts": [
        {"key": "baghdad", "title": "Writings of the Baghdad Period (to 1863)", "short": "Baghdad", "volumes": [28, 32]},
        {"key": "edirne", "title": "Writings of Istanbul and Edirne (1863–1868)", "short": "Edirne", "volumes": [33, 39]},
        {"key": "akka", "title": "Writings of the Akka Period (1868–1892)", "short": "Akka", "volumes": [40, 106]},
        {"key": "undated", "title": "Writings Without Known Dates", "short": "Undated", "volumes": [107, 107]},
    ],
}


def main():
    global HEAD_RE, KEEP_ID_RE, INCIPIT_TITLES
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="source")
    ap.add_argument("--out", default=".")
    ap.add_argument("--layout", choices=["single", "sections"], default=None,
                    help="single = one Quarto site (default); sections = several small sites (fallback if memory is short)")
    args = ap.parse_args()
    src, out = Path(args.src), Path(args.out)
    (out / "tools").mkdir(exist_ok=True)

    cfgp = out / "tools" / "project.json"
    if not cfgp.exists():
        cfgp.write_text(json.dumps(DEFAULT_CFG, indent=2, ensure_ascii=False), encoding="utf-8")
    cfg = dict(DEFAULT_CFG)
    cfg.update(load_json(cfgp))
    if args.layout:
        cfg["layout"] = args.layout
    SINGLE = cfg["layout"] == "single"
    pre = cfg["id_prefix"]
    HEAD_RE = re.compile(rf"^({pre}[A-Za-z]*\d+)\b[ \t]*(.*)$")
    KEEP_ID_RE = re.compile(rf"^{pre}\d+$")
    INCIPIT_TITLES = True
    exclude_ids = set(cfg["exclude_ids"])

    files = sorted({p for p in src.iterdir() if p.is_file() and p.suffix in (".md", ".txt") and volume_number(p) is not None},
                   key=lambda p: (volume_number(p), p.name))
    if not files:
        sys.exit(f"No source files in {src}")
    titles = load_json(out / "tools" / "titles.json")
    headings = load_json(out / "tools" / "headings.json")
    options = load_json(out / "tools" / "options.json")
    vol_titles = load_json(out / "tools" / "volume_titles.json")
    strip_fn = set(options.get("strip_footnotes", []))

    review = {k: [] for k in ("unassigned", "excluded", "bad_blocks", "dup_ids", "long_nohead", "mojibake", "auto", "footnotes", "placeholder", "spans")}
    bhu_ids, derived_csv = [], []
    seen = {}
    volumes = []
    total_kept = 0

    for f in files:
        num = volume_number(f)
        part = next((p for p in cfg["parts"] if p["volumes"][0] <= num <= p["volumes"][1]), None)
        if part is None:
            review["unassigned"].append(f"{f.name} (volume {num}) belongs to no part in tools/project.json")
            continue
        vtitle = volume_title(f, vol_titles)
        kept = []
        for d in parse_source(f):
            if d.get("bad"):
                review["bad_blocks"].append(f"{f.name}: block starting “{d['lines'][0][:60]}”")
            elif d["id"] in exclude_ids:
                review["excluded"].append(f"{d['id']} {d['title']} (vol {num:03d}) — listed in exclude_ids")
            elif not KEEP_ID_RE.match(d["id"]):
                bhu_ids.append(f"{d['id']}\t{num:03d}\t{sum(words(l) for l in d['lines'])}")
            elif sum(words(l) for l in d["lines"]) <= 12 and any(PLACEHOLDER_RE.search(l) for l in d["lines"]):
                review["placeholder"].append(f"{d['id']} {d['title']} (vol {num:03d}) — placeholder only")
            else:
                kept.append(d)
        docs = []
        for d in kept:
            if d["id"] in seen:
                review["dup_ids"].append(f"{d['id']} in vol {num:03d} and vol {seen[d['id']]:03d}")
            seen[d["id"]] = num
            if d["id"] in strip_fn:
                d["lines"] = [strip_footnotes(l) for l in d["lines"]]
            qmd, st = build_doc_qmd(d, titles.get(d["id"]), headings.get(d["id"]))
            if st.get("incipit"):
                derived_csv.append(f"{d['id']}\t{num:03d}\t{qmd.splitlines()[0][2:].split(' {')[0].split(': ', 1)[-1]}")
            elif st["derived_title"]:
                derived_csv.append(f"{d['id']}\t{num:03d}\t{qmd.splitlines()[0][2:].split(' {')[0].split(': ', 1)[-1]}")
            if "^[" in qmd and d["id"] not in strip_fn:
                review["footnotes"].append(f"{d['id']} (vol {num:03d}) — {qmd.count('^[')} footnotes kept; add the ID to \"strip_footnotes\" in tools/options.json to remove")
            if re.search(r"[ÃÂ�]", qmd):
                review["mojibake"].append(f"{d['id']} (vol {num:03d})")
            if st.get("auto"):
                review["auto"].append(f"{d['id']} (vol {num:03d}) {d['title']} — {st['headings']} headings by {st['auto']} ({st['words']:,} words)")
            if st["words"] > LONG_DOC_WORDS and st["headings"] == 0:
                review["long_nohead"].append(f"{d['id']} (vol {num:03d}) {d['title']} — {st['words']:,} words")
            docs.append(dict(id=d["id"], qmd=qmd, words=st["words"]))
            total_kept += 1
        if not docs:
            continue
        years = volume_years(f.name)
        volumes.append(dict(num=num, name=f.name, part=part, title=vtitle, docs=docs, words=sum(x["words"] for x in docs), years=years))

    # ---- pages (writings grouped by volume)
    PAGE_LIMIT = cfg["page_word_limit"]
    for v in volumes:
        pages, cur, cw = [], [], 0
        for d in v["docs"]:
            if d["words"] >= 0.4 * PAGE_LIMIT:          # substantial works get a page of their own
                if cur:
                    pages.append(cur)
                pages.append([d])
                cur, cw = [], 0
                continue
            if cur and cw + d["words"] > PAGE_LIMIT:
                pages.append(cur)
                cur, cw = [], 0
            cur.append(d)
            cw += d["words"]
        if cur:
            pages.append(cur)
        v["pages"] = []
        n = len(pages)
        for k, grp in enumerate(pages, 1):
            w = sum(x["words"] for x in grp)
            single = len(grp) == 1 and (len(v["docs"]) == 1 or grp[0]["words"] >= 0.4 * PAGE_LIMIT)
            if single:
                fname, text = f"{grp[0]['id'].lower()}.qmd", grp[0]["qmd"]
            else:
                htitle = f"Volume {v['num']:03d}: {v['title']}" + (f" ({k} of {n})" if n > 1 else "")
                fname = f"v{v['num']:03d}-{k:02d}.qmd"
                text = f"# {plain(htitle)} {{.unnumbered}}\n\n" + "\n".join(demote(x["qmd"]) for x in grp)
            v["pages"].append(dict(file=fname, text=text, words=w, n=len(grp), src=v, cost=edition_cost(text, cfg["docx_toc_depth"])))

    # ---- sections (separate Quarto projects) or one site; editions (Word files)
    def make_editions(vs):
        res = []
        for ev, ew in group_balanced([(v, v["words"]) for v in vs], cfg["edition_word_limit"]):
            a, b = ev[0]["num"], ev[-1]["num"]
            rng = f"{a:03d}" if a == b else f"{a:03d}–{b:03d}"
            yrs = [y for v in ev for y in v["years"]]
            ytxt = f" ({min(yrs)}–{max(yrs)})" if yrs and min(yrs) != max(yrs) else (f" ({yrs[0]})" if yrs else "")
            res.append(dict(profile=f"docx-{a:03d}-{b:03d}", file=f"{cfg['file_prefix']}_Vols-{a:03d}-{b:03d}" if a != b else f"{cfg['file_prefix']}_Vol-{a:03d}",
                            title=f"{cfg['title']} — Volumes {rng}{ytxt}" if a != b else f"{cfg['title']} — Volume {rng}{ytxt}",
                            label=f"Volumes {rng}{ytxt}" if a != b else f"Volume {rng}{ytxt}", vols=ev, words=ew))
        return res

    sections = []
    cvols = []
    if SINGLE:
        # Condense everything, in order, into the fewest large volumes that stay under the word limit.
        # Pages (<= ~25,000 words) are the building blocks, so a new volume may begin part-way through a source volume.
        allpages = [pg for v in volumes for pg in v["pages"]]
        for gi, (gp, gw) in enumerate(group_balanced([(pg, pg["cost"]) for pg in allpages], cfg["edition_word_limit"]), 1):
            srcs = []
            for pg in gp:
                if pg["src"] not in srcs:
                    srcs.append(pg["src"])
            yrs = sorted({y for sv in srcs for y in sv["years"]})
            ylabel = (f"{yrs[0]}–{yrs[-1]}" if yrs[0] != yrs[-1] else f"{yrs[0]}") if yrs else ""
            cvols.append(dict(num=gi, pages=gp, words=gw, srcs=srcs, years=yrs, ylabel=ylabel, entries=[], part=None,
                              title=f"Volume {gi:02d}" + (f" ({ylabel})" if ylabel else ""), ndocs=sum(pg["n"] for pg in gp)))
        eds = []
        for cv in cvols:
            k = cv["num"]
            lab = f"Volume {k:02d}" + (f" ({cv['ylabel']})" if cv["ylabel"] else "")
            eds.append(dict(profile=f"docx-vol{k:02d}", file=f"{cfg['file_prefix']}_Vol-{k:02d}", title=f"{cfg['title']} — {lab}",
                            label=lab, vols=[cv], words=cv["words"]))
        sections.append(dict(slug="site", label="Volumes", part=None, vols=cvols, words=sum(c["words"] for c in cvols), editions=eds))
    else:
        for part in cfg["parts"]:
            pv = [v for v in volumes if v["part"] is part]
            if not pv:
                continue
            groups = group_balanced([(v, v["words"]) for v in pv], cfg["section_word_limit"])
            for gi, (gv, gw) in enumerate(groups, 1):
                yrs = [y for v in gv for y in v["years"]]
                ylabel = f"{min(yrs)}–{max(yrs)}" if yrs else ""
                multi = len(groups) > 1
                label = part["title"] if not multi else f"{part['short']}, Volumes {gv[0]['num']:03d}–{gv[-1]['num']:03d}" + (f" ({ylabel})" if ylabel else "")
                sections.append(dict(slug=f"s{len(sections) + 1:02d}-{part['key']}" + (f"-{gi}" if multi else ""), label=label,
                                     part=part, vols=gv, words=gw))
        for s_ in sections:
            s_["editions"] = make_editions(s_["vols"])

    # ---- write the project(s)
    for d_ in ("sections", "site"):
        if (out / d_).exists():
            shutil.rmtree(out / d_)
    if SINGLE:
        projects = [dict(slug="site", dir=out / "site", label=cfg["title"], vols=cvols, words=sum(v["words"] for v in cvols),
                         editions=sections[0]["editions"])]
    else:
        projects = [dict(s_, dir=out / "sections" / s_["slug"]) for s_ in sections]
    for s in projects:
        sd = s["dir"]
        (sd / "pages").mkdir(parents=True)
        (sd / "assets").mkdir()
        (sd / "assets" / "custom.scss").write_text(
            "/*-- scss:rules --*/\nblockquote { border-left: 4px solid #b9a37a; padding-left: 1rem; font-style: italic; }\nbody { line-height: 1.7; }\n"
            ".sitesearch { display: flex; gap: .4rem; margin: .2rem 0 1rem; }\n.sitesearch input { flex: 1; padding: .35rem .5rem; }\n", encoding="utf-8")
        for v in s["vols"]:
            v["entries"] = []
            for pg in v["pages"]:
                (sd / "pages" / pg["file"]).write_text(pg["text"], encoding="utf-8")
                v["entries"].append(f"pages/{pg['file']}")
        site_url = cfg["site_url"].rstrip("/") + ("/" if SINGLE else "/" + s["slug"] + "/")
        head = ["# GENERATED by tools/build_book.py from tools/project.json - edit project.json instead",
                "project:", "  type: book", "", "profile:", "  default: web", "", "book:",
                f"  title: {q(cfg['title'])}", f"  subtitle: {q(cfg['subtitle'] if SINGLE else s['label'])}", f"  author: {q(cfg['author'])}", "  language: en",
                f"  site-url: {site_url}", "  reader-mode: true"]
        if SINGLE:
            # Quarto's own search would index all ~7 million words in one file (huge, and the step that exhausts memory).
            # Search is done by Pagefind instead (tools/render_all.py): small index chunks, results on their own page.
            head += ["  search: false", "  tools:", "    - icon: search", "      text: Search all writings", "      href: /search.html",
                     "    - icon: download", "      text: Downloads", "      href: /downloads.html"]
            center = "[Search](/search.html) · [Downloads](/downloads.html)"
        else:
            head += ["  search: true"]
            center = "[All periods](../index.html)"
        head += ["  page-footer:", "    left: |", f"      {cfg['footer_left']}", "    center: |", f"      {center}",
                 "    right: |", f"      {cfg['footer_right']}", "",
                 "execute:", "  eval: false", "", "format:", "  html:", "    theme:", f"      - {cfg['html_theme']}", "      - assets/custom.scss",
                 f"    fontsize: {cfg['html_fontsize']}", "    toc: true", f"    toc-depth: {cfg['html_toc_depth']}", "    number-sections: false", "    smooth-scroll: true"]
        if SINGLE:
            head += ["    include-after-body: assets/searchbox.html"]
        head += ["  docx:", f"    reference-doc: {cfg['docx_reference']}", "    toc: true", f"    toc-depth: {cfg['docx_toc_depth']}", "    number-sections: false", "",
                 "# No PDF format on purpose: convert the Word files to PDF in Word.", ""]
        (sd / "_quarto.yml").write_text("\n".join(head), encoding="utf-8")
        if SINGLE:
            (sd / "assets" / "searchbox.html").write_text(SEARCHBOX_JS, encoding="utf-8")

        web = ["# GENERATED", "project:", "  output-dir: _site", "", "book:", "  chapters:", "    - index.qmd"]
        if SINGLE:
            for v in s["vols"]:
                web += [f"    - part: {q(v['title'])}", "      chapters:"]
                web += [f"        - {e}" for e in v["entries"]]
        else:
            web += [f"    - part: {q(s['part']['title'])}", "      chapters:"]
            for v in s["vols"]:
                web += [f"        - {e}" for e in v["entries"]]
        (sd / "_quarto-web.yml").write_text("\n".join(web) + "\n", encoding="utf-8")

        if SINGLE:
            idx = [f"# {plain(cfg['title'])} {{.unnumbered}}", "", '::: {.content-visible when-format="docx"}',
                   plain(cfg["subtitle"]) + ". Each writing is identified by its BH reference number.", ":::", "",
                   '::: {.content-visible when-format="html"}', "", plain(cfg["subtitle"]) + ". Each writing is identified by its BH reference number.", "",
                   "```{=html}",
                   '<form class="sitesearch" action="/search.html" method="get"><input type="search" name="q" placeholder="Search all writings…" aria-label="Search all writings">'
                   '<button type="submit">Search</button></form>', "```", "",
                   "[Search all writings](/search.html) · [Downloads](/downloads.html)", ""]
            for v in s["vols"]:
                idx += [f"## [{v['title']}]({v['entries'][0]}) {{.unnumbered}}", "",
                        f"About {round(v['words'], -3):,} words; {v['ndocs']:,} writings. Contains: " + "; ".join(
                            f"{sv['num']:03d} {plain(sv['title'])}" for sv in v["srcs"]) + ".", ""]
            idx += [":::", ""]
            (sd / "index.qmd").write_text("\n".join(idx), encoding="utf-8")
        else:
            (sd / "index.qmd").write_text(
                f"# {plain(s['label'])} {{.unnumbered}}\n\n::: {{.content-visible when-format=\"docx\"}}\n{plain(cfg['title'])}. "
                "Each writing is identified by its BH reference number.\n:::\n\n::: {.content-visible when-format=\"html\"}\n"
                f"This section is part of *{plain(cfg['title'])}*. It contains volumes {s['vols'][0]['num']:03d}–{s['vols'][-1]['num']:03d}. "
                "Each writing is identified by its BH reference number.\n\n[← All periods](../index.html)\n:::\n", encoding="utf-8")
        for e in s["editions"]:
            y = ["# GENERATED", "project:", f"  output-dir: _docx/{e['profile']}", "", "book:", f"  title: {q(e['title'])}",
                 f"  output-file: {q(e['file'])}", "  chapters:", "    - index.qmd"]
            for v in e["vols"]:
                y += [f"    - {en}" for en in v["entries"]]
            (sd / f"_quarto-{e['profile']}.yml").write_text("\n".join(y) + "\n", encoding="utf-8")

    # ---- manifest for render_all.py
    manifest = dict(layout=cfg["layout"], project_dir="site" if SINGLE else "sections", title=cfg["title"], subtitle=cfg["subtitle"], site_url=cfg["site_url"], parts=
        ([dict(title="Volumes", sections=[dict(slug="site", label="Volumes", words=sections[0]["words"], first=1, last=len(cvols),
                                               editions=[dict(profile=e["profile"], file=e["file"], label=e["label"], title=e["title"], words=e["words"]) for e in sections[0]["editions"]])])]
         if SINGLE else [
        dict(title=p["title"], sections=[dict(slug=s["slug"], label=s["label"], words=s["words"], first=s["vols"][0]["num"], last=s["vols"][-1]["num"],
                                              editions=[dict(profile=e["profile"], file=e["file"], label=e["label"], title=e["title"], words=e["words"]) for e in s["editions"]])
                                         for s in sections if s["part"] is p]) for p in cfg["parts"] if any(s["part"] is p for s in sections)]))
    (out / "tools" / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "tools" / "derived_titles.tsv").write_text("ID\tvolume\ttitle (taken from the opening words or a To: line)\n" + "\n".join(derived_csv) + "\n", encoding="utf-8")
    (out / "tools" / "excluded_BHU.tsv").write_text("ID\tvolume\twords\n" + "\n".join(bhu_ids) + "\n", encoding="utf-8")
    for fn, text in (("netlify.toml", '# Rendered on your computer (tools/render_all.py); the public/ folder is committed.\n# Leave the Netlify build command EMPTY.\n[build]\n  publish = "public"\n'),
                     (".gitignore", "/docx/\n/site/.quarto/\n/site/_site/\n/site/_docx/\n/site/reference1.docx\n/sections/*/.quarto/\n/sections/*/_site/\n/_pfsite/\n/sections/*/_docx/\n/sections/*/reference1.docx\n__pycache__/\n")):
        if not (out / fn).exists():
            (out / fn).write_text(text, encoding="utf-8")

    # ---- review report
    rep = ["# Review report", "",
           f"Writings kept: **{total_kept:,}** in {len(volumes)} volumes. Dropped: **{len(bhu_ids)}** non-{pre} entries (BHU etc., listed in tools/excluded_BHU.tsv), "
           f"{len(review['excluded'])} listed in exclude_ids, {len(review['placeholder'])} placeholders.", "",
           f"{len(derived_csv):,} writings had no title; their titles come from the opening words or a To: line (see tools/derived_titles.tsv; override in tools/titles.json).", "",
           ("## Periods (all in ONE Quarto site)" if SINGLE else "## Sections (each is its own Quarto project)"), "", "| Section | Volumes | Words | Pages | Word editions |", "|---|---|---:|---:|---:|"]
    for s in sections:
        rep.append(f"| {s['label']} (`{s['slug']}`) | {s['vols'][0]['num']:03d}–{s['vols'][-1]['num']:03d} | {s['words']:,} | {sum(len(v['pages']) for v in s['vols'])} | {len(s['editions'])} |")
    rep += ["", f"Total words: **{sum(v['words'] for v in volumes):,}**; pages: **{sum(len(v['pages']) for v in volumes)}**; Word editions: **{sum(len(s['editions']) for s in sections)}**", "",
            f"## Word editions (limit {cfg['edition_word_limit']:,} words each; Gemini Notebook allows 500,000)", "", "| File | Words |", "|---|---:|"]
    rep += [f"| {e['file']} | {e['words']:,} |" for s in sections for e in s["editions"]]
    rep += ["", "## Volumes", "", "| Vol | Title | Part | Writings | Pages | Words |", "|---:|---|---|---:|---:|---:|"]
    rep += [f"| {v['num']:03d} | {plain(v['title'])} | {v['part']['short']} | {len(v['docs'])} | {len(v['pages'])} | {v['words']:,} |" for v in volumes]
    labels = dict(
        unassigned="Volumes that belong to no part",
        excluded="Excluded on purpose (exclude_ids)",
        placeholder="Excluded: no text in the source",
        dup_ids="Duplicate IDs", bad_blocks="Blocks that did not begin with an ID",
        mojibake="Writings with garbled characters",
        auto="Long writings given AUTOMATIC headings (verse / question / incipit) - override in tools/headings.json",
        long_nohead=f"Long writings (> {LONG_DOC_WORDS:,} words) with NO sub-headers",
        footnotes="Writings containing footnotes")
    for k, lab in labels.items():
        rep += ["", f"## {lab}", ""] + ([f"- {x}" for x in review[k][:300]] or ["- none"]) + ([f"- … and {len(review[k]) - 300} more"] if len(review[k]) > 300 else [])
    (out / "tools" / "review_report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep[:rep.index("## Volumes")]))
    print({k: len(v) for k, v in review.items()})


if __name__ == "__main__":
    main()

"""
MANUAL_TAGGING.py  —  step 2 of 2: hand-set every line's media type and
search term in a point-and-click page (localhost, zero dependencies).

    uv run MANUAL_TAGGING.py [path/to/output.json]

(no argument: newest *-script_to_search_term.json here; a browser tab opens)

What's on the page
------------------
  - the whole script scrollable on the left (that IS the context). one dot
    per row: gold when the required steps are done (media type + term —
    the term is OPTIONAL for hold_previous/background; hold_previous +
    decorate + a term additionally needs step 3's stamp source).
    grouped lines share a coloured stripe. no badge until a type is picked.
  - SPLIT, inline on the left: hover a line's text — it expands to full
    length, a golden cursor snaps to the nearest gap between letters with a
    "click to break here" popup (never at the very start or end); click to
    break into two entries that inherit everything. a toolbar toggle
    switches this to "hover the ✂ at the row's end instead" if hovering
    the text gets in your way.
  - JOIN: hover a row's end for the shimmering "join to above" button —
    the row above previews the appended text as ghost text and the current
    row is struck through. on phones it's a two-step tap + confirm.
  - STEP 1 (blue card): pick ONE media type, grouped NEW / EDIT PREVIOUS,
    ai family in reds, unavailable options flat grey. stack decorate /
    caption / group on top — disabled until a base exists. (i) icons show
    an info card (hover on desktop, tap on phones) docked out of the way
    top-right; the key scrolls through the same cards.
  - STEP 2 (gold card, always pinned on screen at the bottom of the panel):
    the search term. grey ghost suggestion appears when the box is focused
    — tab to accept — plus big tap-to-append noun/place chips. once both
    steps are done a "continue to next" button appears right there.
  - a FINISH button that pulses green when every line is done.
  - phones: no index numbers, per-row join buttons, a ✂ split tool (tap
    the tool, tap a line, tap where — nudge arrows move the point one
    character, then "split at the selected point"), the line being tagged
    shown big in a gold box, and a proper "done — back to list" button.

Every change saves to the JSON instantly (one .bak per session).

(no term-prediction library is bundled: nothing off the shelf hits the
"works very well" bar for narration-to-search-term — the ghost text covers
the quick case and prompts/ covers the quality case.)
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

from MEDIA_TYPES import (COLLAGEABLE_TYPES, GROUPABLE_TYPES, MEDIA_TYPES,
                         MODIFIERS, Tag)

try:    # new names — re-export them from MEDIA_TYPES.py (one line) to keep
        # this in sync with CONFIG; the fallback mirrors CONFIG's defaults.
    from MEDIA_TYPES import STAMP_SOURCE_TYPES, TERM_OPTIONAL_TYPES
except ImportError:
    STAMP_SOURCE_TYPES = ("stock", "wikipedia", "ai_stock")
    TERM_OPTIONAL_TYPES = ("hold_previous", "background")

HERE = Path(__file__).resolve().parent
_PLACE_LABELS = {"GPE", "LOC"}
_NAME_LABELS = {"PERSON", "ORG", "FAC", "EVENT", "WORK_OF_ART", "NORP"}


# =============================================================================
# pure helpers (unit-testable)
# =============================================================================

def build_catalog() -> dict:
    bases = [{"name": n, "label": n.replace("_", " "),
              "color": d["color"], "tags": [t.value for t in d["tags"]],
              "groupable": n in GROUPABLE_TYPES,
              "collageable": n in COLLAGEABLE_TYPES,
              "term_optional": n in TERM_OPTIONAL_TYPES,
              "stampable": n in STAMP_SOURCE_TYPES,
              "info": d["info"], "example": d["example"]}
             for n, d in MEDIA_TYPES.items()]
    mods = [{"name": n, "label": n, "color": d["color"],
             "info": d["info"], "example": d["example"]}
            for n, d in MODIFIERS.items()]
    return {"bases": bases, "modifiers": mods}


def _suggest_from_meta(meta: dict, line: str) -> dict:
    ents = meta.get("ents", [])
    nouns = list(dict.fromkeys(meta.get("nouns", [])))
    keywords = [k for k in meta.get("keywords", []) if k not in nouns]
    places = [e["text"] for e in ents if e["label"] in _PLACE_LABELS]
    names = [e["text"] for e in ents if e["label"] in _NAME_LABELS]
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-']+", line)
             if len(w) > 3 and w.lower() not in nouns][:8]
    ghost = (places[0] if places
             else " ".join(nouns[:2]) if nouns else "")
    return {"nouns": nouns, "places": places, "names": names,
            "keywords": keywords[:8], "words": words, "ghost": ghost}


def recompute(data: Dict[str, dict]) -> None:
    """Derive search_type from media_type+modifiers, and positions from
    consecutive group runs. Called after every mutation so the file is
    always consistent."""
    # drop split-provenance that no longer describes the very next line
    # (the halves were separated or the right half changed), so a stale
    # no-space rejoin can never fire.
    keys = list(data)
    for idx, key in enumerate(keys):
        prov = data[key].get("_split_before")
        nxt = keys[idx + 1] if idx + 1 < len(keys) else None
        if prov and prov.get("text") != nxt:
            data[key].pop("_split_before", None)

    prev_gid, pos = None, 0
    for row in data.values():
        row.pop("search_type", None)   # legacy column — purged on every save
        gid = row.get("group_id")
        if gid is not None and gid == prev_gid:
            pos += 1
        elif gid is not None:
            pos = 1
        else:
            pos = 0
        row["position"] = str(pos if pos else 1)
        prev_gid = gid


def assign_group_id(data: Dict[str, dict], line: str) -> None:
    """A line just gained the 'group' modifier: join the neighbouring
    group if one touches it, else start a new group."""
    lines = list(data)
    i = lines.index(line)
    for j in (i - 1, i + 1):
        if 0 <= j < len(lines):
            other = data[lines[j]]
            if "group" in other.get("modifiers", []) \
                    and other.get("group_id") is not None:
                data[line]["group_id"] = other["group_id"]
                return
    used = [r["group_id"] for r in data.values()
            if r.get("group_id") is not None]
    data[line]["group_id"] = (max(used) + 1) if used else 1


def split_line(data: Dict[str, dict], line: str, index: int) -> Optional[str]:
    """Split one entry at a character index. Both halves keep the media
    type, modifiers, term and rule ids. We remember that the left half was
    immediately followed by the right half (`_split_before`) so that if you
    re-join them we can rebuild the original text WITHOUT inserting a space
    (the split point may have been mid-word). Returns an error or None."""
    if line not in data:
        return "unknown line"
    a, b = line[:index].strip(), line[index:].strip()
    if not a or not b:
        return "cannot break at the very start or end"
    if a in data or b in data:
        return "that split would duplicate an existing line"
    # did we cut at whitespace, or mid-token?  (drives space-free rejoin)
    at_space = bool(re.match(r"\s", line[index - 1:index])) or \
        bool(re.match(r"\s", line[index:index + 1]))
    out = {}
    for key, row in data.items():
        if key == line:
            left = copy.deepcopy(row)
            right = copy.deepcopy(row)
            left["_split_before"] = {"text": b, "glue": " " if at_space else ""}
            out[a] = left
            out[b] = right
        else:
            out[key] = row
    data.clear()
    data.update(out)
    return None


def join_to_above(data: Dict[str, dict], line: str) -> Optional[str]:
    """Merge an entry into the one above it. The merged entry keeps the
    ABOVE line's media type / modifiers / term; rule ids are combined. If
    the above line was split FROM this exact line, we rejoin with the glue
    recorded at split time (no phantom space); otherwise a single space."""
    lines = list(data)
    if line not in data:
        return "unknown line"
    i = lines.index(line)
    if i == 0:
        return "the first line has nothing above it"
    above = lines[i - 1]
    prov = data[above].get("_split_before")
    if prov and prov.get("text") == line:
        merged_text = f"{above}{prov['glue']}{line}"
    else:
        merged_text = f"{above} {line}"
    if merged_text in data and merged_text not in (above, line):
        return "that join would duplicate an existing line"
    merged = copy.deepcopy(data[above])
    merged.pop("_split_before", None)
    ids = list(dict.fromkeys(list(data[above].get("rule_ids", []))
                             + list(data[line].get("rule_ids", []))))
    merged["rule_ids"] = ids
    out = {}
    for key, row in data.items():
        if key == above:
            out[merged_text] = merged
        elif key == line:
            continue
        else:
            out[key] = row
    data.clear()
    data.update(out)
    return None


def apply_patch(data: Dict[str, dict], line: str, patch: dict) -> Optional[str]:
    if line not in data:
        return "unknown line"
    row = data[line]
    mods = list(patch.get("modifiers", row.get("modifiers", [])) or [])
    base = patch.get("media_type", row.get("media_type", ""))
    if "group" in mods and base not in GROUPABLE_TYPES:
        return ("only " + " and ".join(sorted(GROUPABLE_TYPES)).replace("_", " ")
                + " can be grouped")
    if "collage" in mods and base not in COLLAGEABLE_TYPES:
        return ("only " + " and ".join(sorted(COLLAGEABLE_TYPES)).replace("_", " ")
                + " can be collaged")
    # group and collage are mutually exclusive — picking one drops the other
    # (group = one cell per LINE across neighbours; collage = many images on
    # THIS line). Last toggled wins.
    if {"group", "collage"} <= set(mods) and "modifiers" in patch:
        prev = set(row.get("modifiers", []))
        newly = set(mods) - prev
        drop = "collage" if "group" in newly else "group"
        mods = [m for m in mods if m != drop]
        patch = dict(patch, modifiers=mods)
    if "stamp_source" in patch and patch["stamp_source"] is not None \
            and patch["stamp_source"] not in STAMP_SOURCE_TYPES:
        return ("stamp source must be one of "
                + ", ".join(STAMP_SOURCE_TYPES).replace("_", " "))
    for key in {"media_type", "modifiers", "search_term",
                "stamp_source", "stamp_decorate"} & set(patch):
        row[key] = patch[key]
    row["stamp_decorate"] = bool(row.get("stamp_decorate", False))
    # stamp settings only mean something on hold_previous/background +
    # decorate + a non-empty term — clear them the moment the combo breaks
    # so a stale choice can never linger in the json.
    if (row.get("media_type") not in TERM_OPTIONAL_TYPES
            or "decorate" not in (row.get("modifiers") or [])
            or not (row.get("search_term") or "").strip()):
        row["stamp_source"] = None
        row["stamp_decorate"] = False
    if "modifiers" in patch:
        if "group" in row["modifiers"] and row.get("group_id") is None:
            assign_group_id(data, line)
        if "group" not in row["modifiers"]:
            row["group_id"] = None
    return None


# =============================================================================
# state + server
# =============================================================================

class _State:
    def __init__(self, json_path: Path):
        self.json_path = json_path
        self.data: Dict[str, dict] = json.loads(
            json_path.read_text(encoding="utf-8"))
        # CONFIG.py's own convention is "<name>_script_to_search_term.json"
        # (underscore, no TESTING_ prefix) — accept either separator so the
        # split-meta cache (for noun/place suggestion chips) is still found
        # when this tool is driven by main.py instead of run standalone.
        m = re.match(r"(?:TESTING_)?(.+?)[-_]script_to_search_term",
                     json_path.stem)
        prefix = m.group(1) if m else json_path.stem
        triples = None
        # cwd, not HERE: standalone use runs from this directory (cwd==HERE)
        # per MASTER_README; embedded use (main.py) runs from the repo root,
        # where CONFIG._CACHE_DIR ("<prefix>-CACHE") actually lives.
        hits = sorted(Path.cwd().glob(
            f"{prefix}-CACHE/split-and-lable/*SPLITMETA*-{prefix}.json"))
        if hits:
            triples = json.loads(hits[-1].read_text(encoding="utf-8"))
        by_text = {t[0]: t[2] for t in (triples or [])}
        self.suggest = {line: _suggest_from_meta(by_text.get(line, {}), line)
                        for line in self.data}
        self.catalog = build_catalog()
        self.backed_up = False
        # set by the browser's finish button (POST /finish) — lets
        # run_manual_tagging() block only until the user is actually done,
        # instead of forever (the standalone CLI still just uses Ctrl-C).
        self.finished_event = threading.Event()
        recompute(self.data)

    def payload(self) -> dict:
        # suggestions for lines created by splits/joins fall back to words
        for line in self.data:
            if line not in self.suggest:
                self.suggest[line] = _suggest_from_meta({}, line)
        return {"json_path": self.json_path.name,
                "catalog": self.catalog,
                "lines": [{"line": line, "row": row,
                           "suggest": self.suggest[line]}
                          for line, row in self.data.items()]}

    def _checkpoint(self) -> None:
        if not self.backed_up:
            shutil.copy2(self.json_path,
                         self.json_path.with_name(self.json_path.name + ".bak"))
            self.backed_up = True

    def _commit(self) -> None:
        recompute(self.data)
        self.json_path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8")

    def mutate(self, op: str, req: dict) -> Optional[str]:
        self._checkpoint()
        if op == "save":
            err = apply_patch(self.data, req.get("line", ""),
                              req.get("patch", {}))
        elif op == "split":
            err = split_line(self.data, req.get("line", ""),
                             int(req.get("index", 0)))
        elif op == "join":
            err = join_to_above(self.data, req.get("line", ""))
        else:
            err = "unknown operation"
        if err:
            return err
        self._commit()
        return None

    # (reason-logging was removed — split/join are direct)


def make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/data":
                self._send(json.dumps(state.payload()))
            elif self.path.startswith("/example/"):
                name = re.sub(r"[^a-z0-9_]", "", self.path.split("/")[-1])
                p = HERE / "examples" / f"{name}.png"
                if p.exists():
                    self._send(p.read_bytes(), "image/png")
                else:
                    self._send("{}", code=404)
            else:
                self._send(PAGE, "text/html")

        def do_POST(self):
            op = self.path.lstrip("/")
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                req = {}
            if op == "finish":
                state.finished_event.set()
                self._send(json.dumps({"ok": True}))
                return
            err = state.mutate(op, req)
            body = json.dumps({"ok": err is None, "error": err})
            self._send(body, code=200 if err is None else 400)
    return Handler


def make_server(json_path: Path, port: int = 0) -> ThreadingHTTPServer:
    state = _State(json_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    server.state = state
    return server


def _pick_json(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg)
    hits = sorted(HERE.glob("*-script_to_search_term.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        sys.exit("No *-script_to_search_term.json found. "
                 "Run SPLIT_AND_LABEL.py first (see MASTER_README.md).")
    return hits[0]


PAGE = r'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>manual tagging</title><style>
 :root{--gold:#e6c15a;--bg:#16181d;--card:#1c1f27;--blue:#2c4a72}
 body{margin:0;font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:#e8e8e8}
 #bar{display:flex;gap:14px;align-items:center;padding:6px 14px;border-bottom:1px solid #2a2e38;position:sticky;top:0;background:var(--bg);z-index:5}
 #bar details{margin:0;flex:1}
 #finish{padding:7px 18px;border-radius:8px;border:1px solid #3a7a4a;background:#234a30;color:#bfe8c8;cursor:pointer;font-size:14px}
 #finish.ready{background:#2e7d32;color:#fff;border-color:#66bb6a;font-size:16px;padding:9px 26px;animation:pulse 1.6s infinite}
 @keyframes pulse{0%,100%{box-shadow:0 0 0 0 #2e7d3266}50%{box-shadow:0 0 0 9px #2e7d3200}}
 #wrap{display:flex;height:calc(100vh - 44px)}
 #list{width:48%;overflow-y:auto;padding:8px;box-sizing:border-box}
 .navbtn{display:block;width:100%;padding:7px;margin:6px 0;border:1px solid #3a4356;border-radius:7px;background:#20242e;color:#aeb6c6;cursor:pointer;font-size:13px}
 .navbtn:hover{background:#2a2f3c}
 .navbtn small{color:#667;margin-left:6px}
 #editor{flex:1;overflow-y:auto;padding:12px 16px;box-sizing:border-box;display:flex;flex-direction:column}
 .row{padding:7px 8px;border-radius:6px;cursor:pointer;display:flex;gap:8px;align-items:baseline;border-left:4px solid transparent;position:relative}
 .row:hover{background:#22252d}.row.sel{background:#2b3040;outline:1px solid #4a5578}
 .idx{color:#666;width:2em;text-align:right;flex:none;font-size:12px}
 .dot{flex:none;font-size:14px;color:#3a4150;width:1em;text-align:center}
 .dot.done{color:#7bd88f}
 .badge{font-size:11px;padding:1px 7px;border-radius:9px;color:#fff;flex:none}
 .ltxt{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:15.5px;position:relative}
 .ltxt.expand{white-space:normal;overflow:visible}
 .ltxt span.ch{position:relative}
 .lterm{color:#9aa;font-size:12px;flex:none;max-width:9em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .joinbtn{flex:none;visibility:hidden;border:0;background:#2c3342;color:#bcd;border-radius:6px;padding:2px 9px;cursor:pointer;font-size:12px}
 .row:hover .joinbtn{visibility:visible}
 .joinbtn:hover{background:linear-gradient(90deg,var(--gold),#f5dd9a,var(--gold));background-size:200% 100%;color:#222;font-weight:600;animation:shimmer 1s linear infinite}
 @keyframes shimmer{0%{background-position:200% 0}100%{background-position:0 0}}
 .row.joinghost{opacity:.55}.row.joinghost .ltxt{text-decoration:line-through}
 .row.joinghost .joinbtn{opacity:1;visibility:visible}
 .ghostadd{color:#8a8;font-style:italic}
 #caret{position:fixed;width:2px;background:var(--gold);pointer-events:none;display:none;box-shadow:0 0 6px var(--gold);z-index:30}
 #splittip{position:fixed;background:var(--gold);color:#222;font-weight:600;font-size:12px;padding:3px 9px;border-radius:6px;pointer-events:none;display:none;white-space:nowrap;z-index:30}
 h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.08em}
 .req{font-size:11px;margin-left:6px;opacity:.8}
 .info{display:inline-block;width:15px;height:15px;line-height:15px;text-align:center;border-radius:50%;background:#2c3342;color:#9ab;font-size:11px;cursor:help;margin-left:5px;font-style:normal}
 #curline{display:none;background:#26210f;border:2px solid var(--gold);border-radius:10px;padding:10px 12px;margin-bottom:10px;font-size:17px}
 #curline small{display:block;color:#a99a6a;font-size:11px;margin-bottom:4px;letter-spacing:.06em}
 .card{border-radius:10px;margin:0 0 12px;overflow:hidden}
 .card .head{padding:11px 14px;cursor:pointer;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
 .card .head h3{margin:0}
 .card .body{padding:0 14px 12px}
 .card.collapsed .body{display:none}
 .card.collapsed .head{opacity:.75}
 #mediacard.attn{animation:flashblue 1.2s ease-out}
 @keyframes flashblue{0%{box-shadow:0 0 0 0 #6fa6ff99}70%{box-shadow:0 0 0 10px #6fa6ff00}}
 #termcard.attn{animation:flashgold 1.2s ease-out}
 @keyframes flashgold{0%{box-shadow:0 0 0 0 #e6c15a99}70%{box-shadow:0 0 0 10px #e6c15a00}}
 .stepbtn{margin-top:10px;padding:8px 26px;border-radius:8px;border:0;background:#35507a;color:#fff;cursor:pointer;font-weight:600}
 #tostep2{min-width:230px}
 #tostep2.glow{background:var(--gold);color:#222;animation:pulse 1.6s infinite}
 .kbhint{color:#5a6478;font-size:11px;width:100%}
 #mediacard{background:#1a2130;border:1px solid #35507a}
 #mediacard h3{color:#8fb4e8}
 #bottomstick{padding-top:4px}
 #termcard{background:#241f12;border:1px solid #6e5c2a;box-shadow:0 -8px 18px #0008;margin-bottom:8px}
 #termcard h3{color:var(--gold)}
 .cols{display:flex;gap:14px;flex-wrap:wrap}
 .col{min-width:160px;flex:1}.col h4{margin:4px 0;font-size:12px;color:#aab}
 button.tpl{display:flex;justify-content:space-between;align-items:center;width:100%;margin:3px 0;padding:6px 9px;border:0;border-radius:6px;color:#fff;cursor:pointer;opacity:.94}
 button.tpl:hover{opacity:1}button.tpl.on{outline:2px solid #fff}
 button.tpl.kb{outline:2px dashed #fff}
 button.tpl:disabled{background:#3a4150 !important;color:#79808f;cursor:not-allowed;opacity:1}
 .modrow{margin-top:10px;padding-top:8px;border-top:1px dashed #39404f}
 .modrow .lbl{color:#aab;font-size:12px;margin-right:8px}
 button.mod{margin:2px 6px 2px 0;padding:5px 12px;border-radius:14px;border:1px dashed var(--gold);background:transparent;color:var(--gold);cursor:pointer}
 button.mod.on{background:var(--gold);color:#222;border-style:solid}
 button.mod:disabled{opacity:.3;cursor:not-allowed}
 .hint{color:#667;font-size:12px}
 textarea{width:100%;box-sizing:border-box;background:#0f1116;color:#fff;border:1px solid #6e5c2a;border-radius:6px;padding:8px;font:inherit;min-height:54px}
 #termwrap{position:relative}
 #ghost{position:absolute;left:9px;top:9px;color:#556;pointer-events:none;display:none}
 #tabpill{background:#2c3342;color:#9ab;border-radius:5px;font-size:11px;padding:1px 7px;margin-left:8px}
 .chips button{margin:4px 5px 0 0;border-radius:12px;cursor:pointer}
 .chips .big{padding:5px 12px;font-size:14px;border:1px solid #6fae6f;background:#20302a;color:#cfe8cf}
 .chips .big.place{border-color:#5f9ec0;background:#202a33;color:#cfe0ee}
 .chips .small{padding:2px 9px;font-size:12px;border:1px solid #3a4356;background:#20242e;color:#aab}
 .chiplbl{color:#667;font-size:11px;margin:6px 6px 0 0;display:inline-block}
 #nextbtn{display:block;visibility:hidden;margin:0 0 12px;padding:10px 20px;border-radius:8px;border:0;background:var(--gold);color:#222;font-weight:700;cursor:pointer;font-size:15px}
 #nextbtn.amber{background:#c9a13b}
 #nextbtn.fin{background:#2e7d32;color:#fff}
 details{background:var(--card);border-radius:8px;padding:5px 12px}
 summary{cursor:pointer;color:#9aa;font-size:13px}
 #keycards{max-height:46vh;overflow-y:auto}
 .icard{display:flex;gap:12px;align-items:flex-start;border-top:1px solid #262b36;padding:8px 0}
 .icard img{width:96px;height:64px;object-fit:cover;border-radius:6px;background:#333;flex:none}
 .icard .nm{font-weight:600}
 #pop{position:fixed;z-index:50;right:14px;bottom:14px;background:#232836;border:1px solid #4a5578;border-radius:10px;padding:10px 12px;width:300px;display:none;box-shadow:0 6px 24px #0009}
 #finwrap{display:none;position:fixed;inset:0;z-index:70;background:#000b;align-items:center;justify-content:center}
 #finwrap.open{display:flex}
 #finbox{background:#1d2b1f;border:2px solid #2e7d32;border-radius:14px;padding:28px 36px;text-align:center;display:flex;flex-direction:column;gap:10px;font-size:18px;max-width:420px}
 #finbox button{margin-top:8px;padding:9px 18px;border-radius:8px;border:1px solid #4a5060;background:#232733;color:#cdd;cursor:pointer;font-size:14px}
 #toast{position:fixed;left:14px;bottom:14px;background:#20302a;color:#7bd88f;border-radius:8px;padding:5px 13px;font-size:12px;opacity:0;transition:opacity .3s;z-index:40;pointer-events:none}
 #mback,#donebtn,#mobactions,#msplit,#joinconfirm,#joinshield{display:none}
 @media (max-width:700px){
  #wrap{display:block;height:auto}
  #list{width:100%;padding-bottom:70px}
  .idx,.lterm,.joinbtn,.navbtn{display:none}
  .row{display:block;border-radius:0;border-bottom:1px solid rgba(255,255,255,.14);padding:11px 8px;padding-right:104px}
  .badge{display:block;width:max-content;margin:0 0 5px}
  .ltxt{white-space:normal;font-size:16px}
  .mjoin{position:absolute;right:8px;top:10px;padding:9px 12px;border-radius:8px;border:1px solid #5f9ec0;background:#16222c;color:#a9d0ea;font-size:13px}
  .row.joinpend{position:relative;z-index:29;pointer-events:none}
  .row.joinpend .mjoin{pointer-events:auto}
  .row.joinpend .ltxt{text-decoration:line-through;opacity:.6}
  .mjoin.confirm{background:#2e7d32;border-color:#66bb6a;color:#fff;font-weight:700;z-index:29;animation:pulse 1.2s infinite;box-shadow:0 0 14px #66bb6acc}
  .mjoin.confirm.nudgeit{transform:scale(1.12)}
  #joinshield.open{display:block;position:fixed;inset:0;z-index:28;background:transparent}
  #editor{display:none;position:fixed;inset:0;background:var(--bg);z-index:20;padding:10px;padding-bottom:80px}
  #editor.open{display:flex;overflow-y:auto}
  #mback{display:block;background:none;border:0;color:#aab;font-size:30px;line-height:1;padding:0 12px 8px 2px;cursor:pointer;align-self:flex-start}
  #curline{display:block}
  #donebtn{display:block;position:fixed;left:50%;transform:translateX(-50%);bottom:10px;z-index:25;padding:9px 22px;border-radius:9px;border:1px solid #4a5060;background:#232733;color:#cdd;font-size:14px;cursor:pointer}
  #donebtn svg{vertical-align:-2px;margin-right:7px}
  #mobactions{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:15;background:#1c1f27;border-top:1px solid #333;padding:9px;gap:8px;justify-content:center}
  #mobactions button{padding:9px 16px;border-radius:8px;border:1px solid #6e5c2a;background:#241f12;color:var(--gold);font-size:14px}
  #mobactions button.on{background:var(--gold);color:#222;font-weight:700}
  #msplit.open{display:flex;position:fixed;inset:0;z-index:60;background:var(--bg);flex-direction:column;padding:12px}
  #mstitle{color:var(--gold);font-size:13px;margin-bottom:6px}
  #mstext{font-size:23px;line-height:2.1;flex:1;overflow-y:auto;word-break:break-word;user-select:none}
  #mstext span{position:relative;padding:2px 0}
  #mscaret{display:inline-block;width:3px;height:1.15em;background:var(--gold);vertical-align:middle;box-shadow:0 0 7px var(--gold);margin:0 -1px;animation:blink 1s step-end infinite}
  @keyframes blink{50%{opacity:.35}}
  #msbar{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;padding:10px 0}
  #msbar button{padding:11px 14px;border-radius:8px;border:1px solid #555;background:#232733;color:#ddd;font-size:14px}
  #msbar .go{background:var(--gold);color:#222;border:0;font-weight:700}
  #joinconfirm.open{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:30;background:#26210f;border-top:2px solid var(--gold);padding:12px;gap:10px;justify-content:center}
  #joinconfirm button{padding:10px 16px;border-radius:8px;border:0;font-size:14px}
  #joinconfirm .yes{background:var(--gold);color:#222;font-weight:700}
  #joinconfirm .no{background:#333;color:#ddd}
 }
</style></head><body>
<div id="bar">
 <details><summary>key — groups, colours, stacking (hover any ⓘ for its card)</summary>
  <p class="hint">pick ONE base media type per line. NEW puts brand-new material on screen
  (stock and ai stock are both brand-new — ai family is red). EDIT PREVIOUS acts on the
  image already on screen, so it is greyed on the first line. then STACK extras on top:
  decorate, caption, or group — grouped neighbours share a coloured stripe (rule of n).
  a line is done when it gets its green tick: media type AND search term both set.
  splitting: hover a line's text — a golden cursor snaps between letters, click to
  break; the space after the LAST character just selects the line; both halves
  inherit the media type and search term. joining: hover a
  row's end for the join-to-above arrow — the preview shows exactly what you'll get.</p>
  <div id="keycards"></div>
 </details>
 <button id="finish" onclick="finish()">finish</button>
</div>
<div id="wrap">
 <div id="list"></div>
 <div id="editor">
  <button id="mback" onclick="closeEditor()">‹</button>
  <div id="curline"><small>YOU ARE TAGGING THIS LINE</small><span id="curlinetxt"></span></div>
  <div class="card" id="mediacard">
    <div class="head" onclick="expand('media')">
      <h3>step 1 · media type</h3><span class="req">required — pick one</span>
      <i class="info" data-info="_types">i</i>
      <span class="kbhint">← → arrow keys move between types · enter picks · tab → step 2</span>
    </div>
    <div class="body">
      <div class="cols" id="typepanel"></div>
      <div class="modrow"><span class="lbl">stack on top — optional, any
        <i class="info" data-info="_mods">i</i></span>
        <span id="modbar"></span><span id="modhint" class="hint"></span></div>
      <button class="stepbtn" id="tostep2" style="display:none"
        onclick="event.stopPropagation();expand('term')">continue to step 2 ↓</button>
    </div>
  </div>
  <div id="bottomstick">
  <div class="card" id="termcard">
    <div class="head" onclick="expand('term')">
      <h3>step 2 · search term</h3><span class="req" id="termreq">required</span>
    </div>
    <div class="body">
      <div id="termwrap">
        <textarea id="term"></textarea>
        <div id="ghost"></div>
      </div>
      <div class="chips" id="chips"></div>
    </div>
  </div>
  <div class="card" id="stampcard" style="display:none">
    <div class="head">
      <h3>step 3 · stamp pictures from</h3><span class="req">required — pick one</span>
      <i class="info" data-info="_stamp">i</i>
    </div>
    <div class="body">
      <div class="cols"><div class="col" id="stamppanel"></div></div>
    </div>
  </div>
  <button id="nextbtn"></button>
  </div>
  <button id="donebtn" onclick="closeEditor()"><svg width="14" height="12" viewBox="0 0 14 12" fill="none"><rect y="0" width="14" height="2" rx="1" fill="#cdd"/><rect y="5" width="14" height="2" rx="1" fill="#cdd"/><rect y="10" width="14" height="2" rx="1" fill="#cdd"/></svg>done — back to list</button>
 </div>
</div>
<div id="pop"></div>
<div id="caret"></div><div id="splittip">click to break here</div>
<div id="toast">saved ✓</div>
<div id="finwrap"><div id="finbox">
 <div style="font-size:42px;color:#7bd88f">✓</div>
 <div>finished — everything is saved to the json.</div>
 <div class="hint">you can close this tab and stop the server (Ctrl-C in the terminal).</div>
 <button onclick="hideFinish()">← return back to list</button>
</div></div>
<div id="mobactions">
 <button id="mscis" onclick="toggleScissors()">✂ split a line</button>
</div>
<div id="joinshield" onclick="joinWarn()"></div>
<div id="joinconfirm">
 <span style="color:#cbb26a;align-self:center;font-size:13px">confirm the join above ↑ &nbsp;or&nbsp;</span>
 <button class="no" onclick="cancelJoin()">cancel</button>
</div>
<div id="msplit">
 <div id="mstitle">tap where to split — or nudge with the arrows below</div>
 <div id="mstext"></div>
 <div id="msbar">
  <button onclick="nudge(-1)">◀ move split point one character left</button>
  <button onclick="nudge(1)">move split point one character right ▶</button>
  <button class="go" onclick="mobileSplitGo()">✂ split the text at the selected point</button>
  <button onclick="closeMobileSplit()">cancel</button>
 </div>
</div>
<script>
let D=null, sel=0, scissors=false, pendJoin=-1, msLine=null, msB=1;
let step='media', kbType=-1;
const $=q=>document.querySelector(q);
const isMobile=()=>window.innerWidth<=700;
const PLACEHOLDER='data:image/svg+xml;utf8,'+encodeURIComponent(
 '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="64"><rect width="96" height="64" fill="#3a4150"/><text x="48" y="36" fill="#99a" font-size="10" text-anchor="middle">example image</text></svg>');
function toast(msg,color){const t=$('#toast');t.textContent=msg||'saved ✓';
 t.style.color=color||'#7bd88f';t.style.opacity=1;
 clearTimeout(t._h);t._h=setTimeout(()=>t.style.opacity=0,1400);}
function esc(s){return (s||'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
const GP=['#e6c15a','#5fb0c0','#b06fc0','#7bd88f','#c08a5f','#6f8ac0'];
const gcol=g=>GP[(g-1)%GP.length];

async function load(keepLine){
 D=await (await fetch('/data')).json();
 if(keepLine){const i=D.lines.findIndex(L=>L.line===keepLine); if(i>=0) sel=i;}
 sel=Math.max(0,Math.min(sel,D.lines.length-1));
 renderKey(); renderList(); renderEditor(); updateFinish();
}
function baseOf(n){return D.catalog.bases.find(b=>b.name===n);}
function stampNeeded(L,term){
 term=term===undefined?(L.row.search_term||''):term;
 const b=baseOf(L.row.media_type);
 return !!(b&&b.term_optional)
  &&(L.row.modifiers||[]).includes('decorate')&&!!term.trim();}
function lineDone(L){
 if(!L.row.media_type)return false;
 const b=baseOf(L.row.media_type);
 const termOk=!!(L.row.search_term||'').trim()||!!(b&&b.term_optional);
 return termOk&&(!stampNeeded(L)||!!L.row.stamp_source);}
function updateFinish(){
 const all=D.lines.every(lineDone);
 $('#finish').classList.toggle('ready',all);
 $('#finish').textContent=all?'✓ finish':'finish';
}
function renderList(){
 const el=$('#list'); el.innerHTML='';
 const nav=(dir,label,key)=>{
   const b=document.createElement('button');b.className='navbtn';
   b.innerHTML=`${label} <small>(${key} arrow key)</small>`;
   b.onclick=()=>focusLine(sel+dir);return b;};
 el.appendChild(nav(-1,'▲ move to entry above','up'));
 D.lines.forEach((L,i)=>{
  const r=document.createElement('div');
  const pend=isMobile()&&i===pendJoin;
  r.className='row'+(i===sel?' sel':'')+(pend?' joinpend':'');
  const gid=L.row.group_id;
  if(gid) r.style.borderLeftColor=gcol(gid);
  const b=baseOf(L.row.media_type);
  const badge=L.row.media_type
    ? `<span class="badge" style="background:${b?b.color:'#3a4150'}">${esc(L.row.media_type)}${(L.row.modifiers||[]).length?' + …':''}</span>`
    : '';
  r.innerHTML=`<span class="idx">${i}</span>`+
   `<span class="dot ${lineDone(L)?'done':''}" title="${lineDone(L)?'media type + search term set':'still needs media type and/or search term'}">${lineDone(L)?'✓':'○'}</span>`+
   badge+
   `<span class="ltxt" data-i="${i}">${esc(L.line)}</span>`+
   `<span class="lterm">${esc(L.row.search_term||'')}</span>`+
   (i>0?`<button class="joinbtn">⤴ join to above</button>`:'')+
   (isMobile()&&i>0?`<button class="mjoin${pend?' confirm':''}">${pend?'✓ confirm join':'⤴ join to above'}</button>`:'');
  r.onclick=e=>{
   if(e.target.closest('.joinbtn,.mjoin'))return;
   if(scissors){openMobileSplit(i);return;}
   focusLine(i);};
  const jb=r.querySelector('.joinbtn');
  if(jb){jb.onmouseenter=()=>joinGhost(i,true);
         jb.onmouseleave=()=>joinGhost(i,false);
         jb.onclick=e=>{e.stopPropagation();doJoin(i);};}
  const mj=r.querySelector('.mjoin');
  if(mj)mj.onclick=e=>{e.stopPropagation();pend?confirmJoin():startJoin(i);};
  const tx=r.querySelector('.ltxt');
  if(!isMobile()){
    tx.onmouseenter=()=>armSplit(tx,i);
    tx.onmouseleave=()=>disarmSplit(tx,i);
  }
  el.appendChild(r);
 });
 el.appendChild(nav(1,'▼ move to entry below','down'));
 if(isMobile()&&pendJoin>0){
   const rows=document.querySelectorAll('#list .row');
   const prevTx=rows[pendJoin-1]&&rows[pendJoin-1].querySelector('.ltxt');
   if(prevTx&&!prevTx.querySelector('.ghostadd'))
     prevTx.insertAdjacentHTML('beforeend',` <span class="ghostadd">${esc(D.lines[pendJoin].line)}</span>`);
 }
}
// ---- inline splitting on the left ------------------------------------------
function armSplit(tx,i){
 if(tx.dataset.armed)return;
 tx.dataset.armed='1'; tx.classList.add('expand');
 const line=D.lines[i].line;
 tx.innerHTML=[...line].map((c,k)=>`<span class="ch" data-k="${k}">${esc(c)}</span>`).join('');
 tx.onmousemove=e=>{
   // the split zone runs exactly to the LAST CHARACTER: past it (on its own
   // visual line) the caret cancels, so the space after the end of the text
   // is the click-to-select area — always lined up with where the text
   // really ends. wrapped middle lines are splittable across their full
   // width (their text runs to the wrap, so there is no dead zone).
   let limit=Infinity;
   const lastCh=tx.children[line.length-1];
   if(lastCh){
     const lr=lastCh.getBoundingClientRect();
     if(e.clientY>=lr.top&&e.clientY<=lr.bottom)limit=lr.right;
   }
   if(e.clientX>limit){
     $('#caret').style.display='none';$('#splittip').style.display='none';
     tx.dataset.b='';return;}
   const t=document.elementFromPoint(e.clientX,e.clientY);
   if(!t||t.dataset.k===undefined)return;
   const rect=t.getBoundingClientRect();
   let b=+t.dataset.k + (e.clientX>rect.left+rect.width/2?1:0);
   b=Math.max(1,Math.min(b,line.length-1));
   const ref=tx.children[Math.min(b,line.length-1)].getBoundingClientRect();
   const x=b<line.length?ref.left:ref.right;
   const c=$('#caret');
   c.style.display='block';c.style.left=x+'px';c.style.top=ref.top+'px';c.style.height=ref.height+'px';
   const tip=$('#splittip');
   tip.style.display='block';tip.style.left=(x+10)+'px';tip.style.top=(ref.top-28)+'px';
   tx.dataset.b=b;
 };
 tx.onclick=async e=>{
   const b=+tx.dataset.b||0;
   if(b<1)return;              // no break point armed -> bubbles -> selects
   e.stopPropagation();
   await doSplit(line,b);
 };
}
function disarmSplit(tx,i){
 if(!tx.dataset.armed)return;
 delete tx.dataset.armed; delete tx.dataset.b;
 tx.classList.remove('expand');
 tx.onmousemove=null; tx.onclick=null;
 tx.textContent=D.lines[i].line;
 $('#caret').style.display='none';$('#splittip').style.display='none';
}
async function doSplit(line,b){
 await post('/split',{line,index:b},line.slice(0,b).trim());
 $('#caret').style.display='none';$('#splittip').style.display='none';
}
// ---- joining ----------------------------------------------------------------
function joinGhost(i,on){
 const rows=document.querySelectorAll('#list .row');
 const cur=rows[i], prev=rows[i-1];
 if(!prev)return;
 cur.classList.toggle('joinghost',on);
 const t=prev.querySelector('.ltxt');
 const g=prev.querySelector('.ghostadd');
 if(on&&!g) t.insertAdjacentHTML('beforeend',` <span class="ghostadd">${esc(D.lines[i].line)}</span>`);
 if(!on&&g) g.remove();
}
async function doJoin(i){
 if(i<=0)return;
 await post('/join',{line:D.lines[i].line},D.lines[i-1].line);
}
function startJoin(i){
 // MODAL: nothing else can happen until the user confirms or cancels.
 // State-driven: renderList paints the green in-place confirm button (and
 // the ghost preview) FROM pendJoin, so any re-render in between — a saved
 // term reloading, anything — keeps the confirm state instead of quietly
 // reverting the button to plain blue underneath the shield.
 pendJoin=i;
 renderList();
 $('#joinshield').classList.add('open');
 $('#joinconfirm').classList.add('open');
}
function joinWarn(){
 toast('confirm or cancel the join first','#e6c15a');
 const btn=document.querySelector('.mjoin.confirm');
 if(btn){btn.classList.remove('nudgeit');void btn.offsetWidth;btn.classList.add('nudgeit');}
}
function cancelJoin(){
 pendJoin=-1;
 $('#joinshield').classList.remove('open');
 $('#joinconfirm').classList.remove('open');
 renderList();                       // restores the button + clears the ghost
}
async function confirmJoin(){
 const i=pendJoin; pendJoin=-1;
 $('#joinshield').classList.remove('open');
 $('#joinconfirm').classList.remove('open');
 await doJoin(i);
}
// ---- mobile scissors flow -----------------------------------------------------
function toggleScissors(){scissors=!scissors;$('#mscis').classList.toggle('on',scissors);
 $('#mscis').textContent=scissors?'now tap the line you want to split':'✂ split a line';}
function openMobileSplit(i){
 scissors=false;$('#mscis').classList.remove('on');
 $('#mscis').textContent='✂ split a line';
 msLine=D.lines[i].line; msB=Math.max(1,Math.round(msLine.length/2));
 $('#msplit').classList.add('open'); renderMs();
 $('#mstext').onclick=e=>{
   const t=e.target.closest('span[data-k]'); if(!t)return;
   const r2=t.getBoundingClientRect();
   msB=Math.max(1,Math.min(+t.dataset.k+(e.clientX>r2.left+r2.width/2?1:0),msLine.length-1));
   renderMs();};
}
function renderMs(){
 // the caret is an INLINE element inside the text, so it is always visible
 let h='';
 [...msLine].forEach((c,k)=>{
   if(k===msB)h+='<span id="mscaret"></span>';
   h+=`<span data-k="${k}">${esc(c)}</span>`;});
 $('#mstext').innerHTML=h;
}
function nudge(d){msB=Math.max(1,Math.min(msB+d,msLine.length-1));renderMs();}
function closeMobileSplit(){$('#msplit').classList.remove('open');msLine=null;}
async function mobileSplitGo(){
 const line=msLine,b=msB; closeMobileSplit();
 await doSplit(line,b);
}
// ---- editor ------------------------------------------------------------------
function focusLine(i){
 sel=Math.max(0,Math.min(i,D.lines.length-1));
 step='media'; kbType=-1;
 renderList(); renderEditor();
 const r=document.querySelectorAll('#list .row')[sel];
 if(r)r.scrollIntoView({block:'nearest'});
 if(isMobile()) $('#editor').classList.add('open');
}
function closeEditor(){$('#editor').classList.remove('open');}
function expand(which){
 step=which;
 applyCollapse();
 const card=$(which==='media'?'#mediacard':'#termcard');
 card.classList.remove('attn'); void card.offsetWidth;   // restart animation
 if(which==='term'){
   // refresh + focus in one frame so the ghost/tab-pill always appears,
   // whichever path got us here (button, tab key, header click)
   requestAnimationFrame(()=>{renderTerm();$('#term').focus();updateGhost();});
 }
}
function applyCollapse(){
 $('#mediacard').classList.toggle('collapsed',step!=='media');
 $('#termcard').classList.toggle('collapsed',step!=='term');
}
function flash(id){const c=$(id);c.classList.remove('attn');void c.offsetWidth;c.classList.add('attn');}
function renderEditor(){
 const L=D.lines[sel];
 $('#curlinetxt').textContent=L.line;
 renderTypes(); renderMods(); renderTerm();
 const ts=$('#tostep2');
 ts.style.display=L.row.media_type?'inline-block':'none';
 ts.classList.toggle('glow',!!L.row.media_type);
 applyCollapse();
}
let kbAvail=[];
function renderTypes(){
 const L=D.lines[sel];
 let html=''; kbAvail=[];
 for(const [tag,title] of [['new','NEW — brand-new material'],['edit_previous','EDIT PREVIOUS — act on what is on screen']]){
  html+=`<div class="col"><h4>${title}</h4>`;
  D.catalog.bases.filter(b=>b.tags.includes(tag)).forEach(b=>{
   const disabled=(tag==='edit_previous'&&sel===0);
   if(!disabled)kbAvail.push(b.name);
   const on=L.row.media_type===b.name?' on':'';
   const kb=(kbType>=0&&kbAvail[kbType]===b.name&&!disabled)?' kb':'';
   html+=`<button class="tpl${on}${kb}" ${disabled?'disabled':''} style="background:${b.color}" onclick="pick('${b.name}')">`+
     `<span>${b.label}</span><i class="info" data-info="${b.name}">i</i></button>`;});
  html+='</div>';
 }
 $('#typepanel').innerHTML=html;
 hookInfos($('#typepanel'));
}
function renderMods(){
 const L=D.lines[sel]; const has=!!L.row.media_type;
 const b=baseOf(L.row.media_type);
 $('#modbar').innerHTML=D.catalog.modifiers.map(m=>{
  const on=(L.row.modifiers||[]).includes(m.name)?' on':'';
  const dis=!has||(m.name==='group'&&!(b&&b.groupable))||(m.name==='collage'&&!(b&&b.collageable));
  const why=m.name==='group'&&has&&!(b&&b.groupable)?'only stock and ai stock can be grouped'
           :m.name==='collage'&&has&&!(b&&b.collageable)?'only stock can be collaged':'';
  return `<button class="mod${on}" ${dis?'disabled':''} title="${why}" onclick="toggleMod('${m.name}')">${m.label}`+
         ` <i class="info" data-info="${m.name}">i</i></button>`;}).join('');
 $('#modhint').textContent=has?'':'pick a base first — there must be something to put these on';
 hookInfos($('#modbar').parentElement);
}
function renderTerm(){
 const L=D.lines[sel];
 $('#term').value=L.row.search_term||'';
 const b=baseOf(L.row.media_type);
 $('#termreq').textContent=(b&&b.term_optional)
   ?'optional for this type'
   :'required';
 updateGhost();
 const s=L.suggest; let chips='';
 const add=(lbl,arr,cls)=>{if(arr&&arr.length){chips+=`<span class="chiplbl">${lbl}:</span>`+
   arr.map(w=>`<button tabindex="-1" class="${cls}" onclick="chip('${esc(w).replace(/'/g,"\\'")}')">${esc(w)}</button>`).join('');}};
 add('places',s.places,'big place'); add('names',s.names,'big place');
 add('nouns',s.nouns,'big'); add('keywords',s.keywords,'small'); add('words',s.words,'small');
 $('#chips').innerHTML=chips||'<span class="chiplbl">(no extracted suggestions for this line)</span>';
 renderStamp(L);
 renderNext(L);
}
function renderStamp(L){
 const card=$('#stampcard'); const need=stampNeeded(L);
 card.style.display=need?'block':'none';
 if(!need)return;
 const deco=L.row.stamp_decorate?' on':'';
 $('#stamppanel').innerHTML=D.catalog.bases.filter(b=>b.stampable).map(b=>{
  const on=L.row.stamp_source===b.name?' on':'';
  return `<button class="tpl${on}" style="background:${b.color}" onclick="pickStamp('${b.name}')">`+
    `<span>${b.label}</span><i class="info" data-info="${b.name}">i</i></button>`;}).join('')
  +`<button class="tpl${deco}" style="background:#7a4a8a" onclick="toggleStampDeco()">`+
   `<span>✎ decorate the pick first</span><i class="info" data-info="_stampdeco">i</i></button>`;
 hookInfos($('#stamppanel'));
}
async function pickStamp(name){const L=D.lines[sel];
 await post('/save',{line:L.line,patch:{stamp_source:name}},L.line);}
async function toggleStampDeco(){const L=D.lines[sel];
 await post('/save',{line:L.line,patch:{stamp_decorate:!L.row.stamp_decorate}},L.line);}
function renderNext(L){
 const nb=$('#nextbtn');
 if(!lineDone(L)){nb.style.visibility='hidden';return;}
 nb.style.visibility='visible';
 const all=D.lines.every(lineDone);
 if(all){nb.className='fin';nb.textContent='✓ finish';
   nb.onclick=()=>showFinish();}
 else if(sel>=D.lines.length-1){nb.className='amber';
   nb.textContent='not all items are done — continue to next incomplete item →';
   nb.onclick=()=>focusLine(D.lines.findIndex(x=>!lineDone(x)));}
 else{nb.className='';nb.textContent='✓ continue to next line →';
   nb.onclick=()=>focusLine(sel+1);}
}
function updateGhost(){
 const L=D.lines[sel]; const g=$('#ghost');
 const focused=document.activeElement===$('#term');
 if(!$('#term').value && L.suggest.ghost && focused){
   g.innerHTML=esc(L.suggest.ghost)+'<span id="tabpill">tab ⇥ to accept</span>';
   g.style.display='block';
 } else g.style.display='none';
}
$('#term').addEventListener('focus',updateGhost);
$('#term').addEventListener('blur',()=>setTimeout(updateGhost,120));
$('#term').addEventListener('input',()=>{updateGhost();debSave();});
$('#term').addEventListener('keydown',e=>{
 if(e.key==='Tab'&&e.shiftKey){e.preventDefault();expand('media');return;}
 if(e.key!=='Tab')return;
 if(!$('#term').value&&D.lines[sel].suggest.ghost){
   e.preventDefault();$('#term').value=D.lines[sel].suggest.ghost;
   updateGhost();debSave();return;}
 const L=D.lines[sel];
 if(L.row.media_type&&$('#term').value.trim()){
   e.preventDefault();
   if(stampNeeded(L,$('#term').value)&&!L.row.stamp_source){
     flash('#stampcard');                     // step 3 still needs a pick
     $('#stampcard').style.display='block';
   } else $('#nextbtn').click();}   // straight to the next line
});
function chip(w){const t=$('#term');t.value=(t.value?t.value+' ':'')+w;updateGhost();debSave();}
let tmr=null;
function debSave(){clearTimeout(tmr);tmr=setTimeout(()=>{
 post('/save',{line:D.lines[sel].line,patch:{search_term:$('#term').value}},
      D.lines[sel].line,true);
 if(!D.lines[sel].row.media_type)flash('#mediacard');},400);}
async function pick(name){const L=D.lines[sel];
 await post('/save',{line:L.line,patch:{media_type:name}},L.line);
 const t2=$('#tostep2');
 t2.style.display='inline-block'; t2.classList.add('glow');
 flash('#termcard');}
async function toggleMod(name){const L=D.lines[sel];
 const mods=(L.row.modifiers||[]).slice();
 const k=mods.indexOf(name); if(k>=0)mods.splice(k,1); else mods.push(name);
 await post('/save',{line:L.line,patch:{modifiers:mods}},L.line);}
async function post(url,body,keepLine,quietReload){
 const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify(body)});
 const j=await r.json().catch(()=>({}));
 if(!r.ok){alert(j.error||'error');return false;}
 toast();
 if(quietReload){
   const cur=$('#term'), pos=cur.selectionStart, val=cur.value;
   await load(keepLine); cur.value=val; try{cur.setSelectionRange(pos,pos);}catch(e){}
   updateGhost();          // re-evaluate ghost/tab-pill for the restored value
 } else await load(keepLine);
 return true;
}
// ---- info cards ---------------------------------------------------------------
function cardHTML(name){
 const all=[...D.catalog.bases,...D.catalog.modifiers];
 const e=all.find(x=>x.name===name);
 if(name==='_types')return '<div class="nm">media type</div><div class="hint">every line needs exactly one base media type. NEW fetches brand-new material; EDIT PREVIOUS reuses or changes the image already on screen. colours show the family — ai in reds.</div>';
 if(name==='_mods')return '<div class="nm">stack on top</div><div class="hint">optional extras layered onto the base you picked: decorate (draw on it), caption (text on it), group (this line is one cell of a multi-cell group with its neighbours — rule of n).</div>';
 if(name==='_stamp')return '<div class="nm">stamp pictures from</div><div class="hint">hold previous/background + decorate + a search term: the term describes what to STAMP ("jar of nutmeg"). the scene joins the NORMAL candidates review as this type — you click the picture you want there, and it waits ready (and active) in the decorate editor\'s STAMP tab.</div>';
 if(name==='_stampdeco')return '<div class="nm">decorate the pick first</div><div class="hint">after you click your pick in the review, it opens in the decorator ON ITS OWN first — cut it out, remove its background, clean it up — BEFORE it\'s offered as a stamp.</div>';
 if(!e)return '';
 return `<div class="icard"><img src="/example/${e.name}" onerror="this.src='${PLACEHOLDER}'">`+
   `<div><div class="nm">${e.label||e.name}</div><div class="hint">${esc(e.info)}</div></div></div>`;
}
function hookInfos(root){
 root.querySelectorAll('.info').forEach(el=>{
  const show=e=>{e.stopPropagation();$('#pop').innerHTML=cardHTML(el.dataset.info);$('#pop').style.display='block';};
  el.onmouseenter=show;
  el.onmouseleave=()=>{$('#pop').style.display='none';};
  el.onclick=show;
 });
}
function renderKey(){
 $('#keycards').innerHTML=[...D.catalog.bases,...D.catalog.modifiers]
   .map(e=>cardHTML(e.name)).join('');
}
function finish(){
 const bad=D.lines.findIndex(L=>!lineDone(L));
 if(bad>=0){focusLine(bad);
   toast('not finished yet — this line still needs tagging','#e6c15a');return;}
 showFinish();
}
function showFinish(){$('#finwrap').classList.add('open');
 fetch('/finish',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});}
function hideFinish(){$('#finwrap').classList.remove('open');closeEditor();}
document.addEventListener('keydown',e=>{
 if(e.target.tagName==='TEXTAREA')return;
 if(e.key==='ArrowDown'){focusLine(sel+1);e.preventDefault();}
 else if(e.key==='ArrowUp'){focusLine(sel-1);e.preventDefault();}
 else if(step==='media'&&(e.key==='ArrowLeft'||e.key==='ArrowRight')&&kbAvail.length){
   kbType=kbType<0?0:(kbType+(e.key==='ArrowRight'?1:-1)+kbAvail.length)%kbAvail.length;
   renderTypes();e.preventDefault();}
 else if(step==='media'&&e.key==='Enter'&&kbType>=0&&kbAvail[kbType]){
   pick(kbAvail[kbType]);e.preventDefault();}
 else if(e.key==='Tab'&&!e.shiftKey&&step==='media'
         &&D.lines[sel].row.media_type){expand('term');e.preventDefault();}
});
document.addEventListener('click',e=>{if(!e.target.closest('.info,#pop'))$('#pop').style.display='none';});
load();
</script></body></html>'''


def run_manual_tagging(json_path: Path, port: int = 0,
                        auto_open_browser: bool = True) -> None:
    """Blocking helper for embedding (main.py): opens the tagging page and
    returns once the user clicks finish in the browser (every line done),
    or Ctrl-C. Unlike main() below, the server runs in a background thread
    so the finish button can shut it down instead of running forever."""
    server = make_server(json_path, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"manual tagging: {json_path.name}\n  open {url}  "
          f"(finish in the browser, or Ctrl-C here)")
    if auto_open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        server.state.finished_event.wait()
    except KeyboardInterrupt:
        print("\n  stopped manually. edits are already saved.")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    print("  manual tagging done — resuming pipeline.")


def main() -> None:
    json_path = _pick_json(sys.argv[1] if len(sys.argv) > 1 else None)
    server = make_server(json_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"manual tagging: {json_path.name}\n  open {url}  (Ctrl-C to stop)")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped. edits are already saved.")


if __name__ == "__main__":
    main()

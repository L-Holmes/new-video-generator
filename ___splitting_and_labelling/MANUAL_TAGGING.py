"""
MANUAL_TAGGING.py  —  hand-set the media type and search term per line, in
a proper point-and-click UI (localhost page, zero dependencies).

    uv run MANUAL_TAGGING.py [path/to/output.json]

(no argument: newest *-script_to_search_term.json here; a browser tab opens
automatically — or visit the printed URL)

WHAT IT GIVES YOU
-----------------
  • the WHOLE script as a scrollable list on the left (full context; the
    focused line also shows a dimmed strip of its neighbours in the editor)
  • media-type BUTTONS grouped the way we think: NEW / EDIT PREVIOUS /
    EDIT GROUP columns, with the AI family in red shades, stock in blue,
    wikipedia teal, map green, object purple, reuse-previous grey.  An
    expandable KEY explains the grouping and colours.
  • LAYERING: after you pick a base type you can optionally add an overlay
    (+ caption / + draw / + object edit) onto the NEW material — and you
    cannot pick an overlay before there's something to put it on (edit-
    previous types are also disabled on the very first line: nothing
    previous exists yet).
  • the search term starts as a greyed, clearly-clickable pane; click to
    expand it — type freely, or tap the QUICK CHIPS (nouns, places, names,
    keywords the splitter extracted from that line) to append them.
  • you can do it in either order: term first if you don't know the type
    yet — the type panel works the same way afterwards.

Every save patches the JSON in place (a .bak is written once per session)
and appends "manual: set by hand" to the row's why-trail, so calibration
and the AI prompts can tell human choices from sampled ones.

Layered overlays beyond the built-in templates (e.g. new stock + draw) are
recorded on the shot's overlay axis; the legacy `search_type` stays the
base type's string until the renderer switchover (see
TODO_LEGACY_SWITCHOVER.md).
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

from SPLIT_AND_LABEL_CONFIG import (TEMPLATE_DEFS, AI_TEMPLATES, Strategy,
                                    to_legacy)

HERE = Path(__file__).resolve().parent

_PLACE_LABELS = {"GPE", "LOC"}
_NAME_LABELS = {"PERSON", "ORG", "FAC", "EVENT", "WORK_OF_ART", "NORP"}

# material -> button colour family (the KEY in the UI explains these)
MATERIAL_COLOR = {
    "ai_stock": "#c0392b",       # reds = AI-generated
    "stock": "#2e6da4",          # blue = stock footage
    "stock_image": "#7d3c98",    # purple = stock image -> object editor
    "wikipedia": "#148f77",      # teal = wikipedia lookup
    "map": "#1e8449",            # green = map render
    "none": "#7f8c8d",           # grey = reuse / no new material
}


# =============================================================================
# data assembly (pure functions — unit-testable)
# =============================================================================

def build_catalog() -> List[dict]:
    """One button per TemplateDef, with everything the UI needs to group,
    colour, gate and emit it.  Derived from the config master table — a new
    media type added there appears here automatically."""
    cat = []
    for name, d in TEMPLATE_DEFS.items():
        strategy = d.spec.strategy.value
        cat.append({
            "template": name,
            "label": name.split("__", 1)[1].replace("_", " "),
            "strategy": strategy,
            "material": d.spec.material.value,
            "base": d.spec.base.value,
            "layout_kind": d.spec.layout.kind.value,
            "overlay": d.spec.overlay.value,
            "ai": name in AI_TEMPLATES,
            "legacy": to_legacy(name, False),
            "legacy_ai": to_legacy(name, True),
            "color": ("#e74c3c" if d.spec.base.value != "none"
                      else MATERIAL_COLOR.get(d.spec.material.value, "#555")),
            "shot": d.spec.to_dict(),
        })
    return cat


def _suggest_from_meta(meta: dict, line: str) -> dict:
    ents = meta.get("ents", [])
    nouns = list(dict.fromkeys(meta.get("nouns", [])))
    keywords = [k for k in meta.get("keywords", []) if k not in nouns]
    places = [e["text"] for e in ents if e["label"] in _PLACE_LABELS]
    names = [e["text"] for e in ents if e["label"] in _NAME_LABELS]
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-']+", line)
             if len(w) > 3 and w.lower() not in nouns]
    return {"nouns": nouns, "places": places, "names": names,
            "keywords": keywords[:8], "words": words[:10]}


def build_suggestions(json_lines: List[str],
                      triples: Optional[list]) -> Dict[str, dict]:
    """Quick-chip material per line, from the SPLITMETA cache when present
    (nouns/entities/keywords the splitter extracted), else from the line's
    own words."""
    by_text = {}
    if triples:
        for t in triples:
            text, _ids, meta = t
            by_text[text] = meta
    return {line: _suggest_from_meta(by_text.get(line, {}), line)
            for line in json_lines}


def find_split_cache(prefix: str) -> Optional[Path]:
    for pattern in (f"{prefix}-CACHE/split-and-lable/*SPLITMETA*-{prefix}.json",):
        hits = sorted(HERE.glob(pattern))
        if hits:
            return hits[-1]
    return None


def apply_patch(data: Dict[str, dict], line: str, patch: dict) -> bool:
    """Merge an allowed patch into one row.  Returns True if changed."""
    if line not in data:
        return False
    row = data[line]
    allowed = {"search_term", "search_type", "template", "shot"}
    changed = False
    for key in allowed & set(patch):
        if row.get(key) != patch[key]:
            row[key] = patch[key]
            changed = True
    if changed:
        row["manual"] = True
        note = "manual: set by hand in MANUAL_TAGGING"
        why = row.setdefault("why", [])
        if note not in why:
            why.append(note)
    return changed


# =============================================================================
# the page (single-file HTML/CSS/JS, no external assets)
# =============================================================================

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>manual tagging</title><style>
 body{margin:0;font:14px/1.45 system-ui,sans-serif;background:#16181d;color:#e8e8e8;display:flex;height:100vh}
 #list{width:44%;overflow-y:auto;border-right:1px solid #333;padding:8px}
 #editor{flex:1;overflow-y:auto;padding:14px 18px}
 .row{padding:6px 8px;border-radius:6px;cursor:pointer;display:flex;gap:8px;align-items:baseline}
 .row:hover{background:#22252d}.row.sel{background:#2b3040;outline:1px solid #4a5578}
 .idx{color:#666;width:2.2em;text-align:right;flex:none}
 .badge{font-size:11px;padding:1px 7px;border-radius:9px;color:#fff;flex:none}
 .line{flex:1}.term{color:#9aa;font-size:12px;flex:none;max-width:11em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .manual .idx::after{content:"✎";color:#e6c15a;margin-left:2px}
 h3{margin:14px 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#8b93a7}
 .ctx{color:#777;font-size:12px;margin:2px 0}.ctx b{color:#ddd;font-size:14px}
 .cols{display:flex;gap:14px;flex-wrap:wrap}
 .col{min-width:150px}.col h4{margin:4px 0;font-size:12px;color:#aab}
 button.tpl{display:block;width:100%;margin:3px 0;padding:6px 8px;border:0;border-radius:6px;color:#fff;cursor:pointer;text-align:left;opacity:.92}
 button.tpl:hover{opacity:1;transform:translateX(2px)}
 button.tpl.on{outline:2px solid #fff}
 button.tpl:disabled{opacity:.25;cursor:not-allowed;transform:none}
 button.tpl small{float:right;opacity:.75}
 #overlaybar button{margin:2px 6px 2px 0;padding:5px 10px;border-radius:14px;border:1px dashed #888;background:#232733;color:#ddd;cursor:pointer}
 #overlaybar button.on{border-style:solid;border-color:#e6c15a;color:#e6c15a}
 #overlaybar button:disabled{opacity:.3;cursor:not-allowed}
 #termbox{margin-top:10px}
 #termcollapsed{padding:10px;border:1px dashed #555;border-radius:8px;color:#888;cursor:pointer;background:#1c1f26}
 #termcollapsed:hover{color:#ccc;border-color:#888}
 #termopen{display:none}
 textarea{width:100%;box-sizing:border-box;background:#0f1116;color:#fff;border:1px solid #444;border-radius:6px;padding:8px;font:inherit;min-height:56px}
 .chips button{margin:3px 4px 0 0;padding:3px 9px;border-radius:12px;border:1px solid #3a4356;background:#20242e;color:#cfd6e4;cursor:pointer;font-size:12px}
 .chips button:hover{background:#2c3342}
 .chiplbl{color:#667;font-size:11px;margin:6px 6px 0 0;display:inline-block}
 details{margin:10px 0;background:#1b1e26;border-radius:8px;padding:8px 12px}
 summary{cursor:pointer;color:#9aa}
 #saved{color:#7bd88f;font-size:12px;margin-left:10px;opacity:0;transition:opacity .4s}
 .swatch{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}
 kbd{background:#2a2e39;border-radius:3px;padding:0 5px}
</style></head><body>
<div id="list"></div>
<div id="editor">
 <details><summary>key — groups &amp; colours</summary>
  <p><b>Columns = strategy</b>: <b>NEW</b> fetches/renders brand-new material
  (stock and AI generation are both "brand new"); <b>EDIT PREVIOUS</b> acts on
  the image already on screen (so it's disabled on the first line — nothing
  previous exists); <b>EDIT GROUP</b> is a related run of cells (rule of N —
  grids).</p>
  <p><b>Colours = material</b>:
   <span class="swatch" style="background:#c0392b"></span>AI-generated (reds; a red <i>board</i> button = stock/wiki sitting on the stickman board)
   <span class="swatch" style="background:#2e6da4"></span>stock footage
   <span class="swatch" style="background:#7d3c98"></span>stock image → object editor
   <span class="swatch" style="background:#148f77"></span>wikipedia
   <span class="swatch" style="background:#1e8449"></span>map
   <span class="swatch" style="background:#7f8c8d"></span>reuse previous / no new material</p>
  <p><b>Layering</b>: pick a base first, then optionally <i>+ caption / + draw /
  + object edit</i> to put an overlay ON the new material. You can't overlay
  first — there must be something to decorate. (Overlays beyond the built-in
  templates are stored on the shot's overlay axis; the legacy search_type
  stays the base's until the renderer switchover.)</p>
  <p><kbd>↑</kbd>/<kbd>↓</kbd> move between lines. The search-term pane is
  collapsed/greyed until you click it — either order works: term first, type
  first.</p>
 </details>
 <div id="ctx"></div>
 <div id="typepanel"></div>
 <div id="overlaybar"></div>
 <div id="termbox">
   <div id="termcollapsed">search term — click to edit ✎</div>
   <div id="termopen">
     <textarea id="term" placeholder="type, or tap chips below to append…"></textarea>
     <div class="chips" id="chips"></div>
   </div>
 </div>
 <p><span id="saved">saved ✓</span></p>
</div>
<script>
let D=null, sel=0, curOverlay=null;
const $=q=>document.querySelector(q);
async function load(){D=await (await fetch('/data')).json(); renderList(); focus(0);}
function renderList(){
 const el=$('#list'); el.innerHTML='';
 D.lines.forEach((L,i)=>{
  const r=document.createElement('div');
  r.className='row'+(i===sel?' sel':'')+(L.row.manual?' manual':'');
  const c=colorFor(L.row.template);
  r.innerHTML=`<span class="idx">${i}</span>`+
   `<span class="badge" style="background:${c}">${L.row.search_type||'?'}</span>`+
   `<span class="line">${esc(L.line)}</span>`+
   `<span class="term">${esc(L.row.search_term||'')}</span>`;
  r.onclick=()=>focus(i); el.appendChild(r);
 });
}
function colorFor(tpl){const e=D.catalog.find(c=>c.template===tpl);return e?e.color:'#555';}
function esc(s){return (s||'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
function focus(i){
 sel=Math.max(0,Math.min(i,D.lines.length-1)); curOverlay=null; renderList();
 document.querySelectorAll('#list .row')[sel].scrollIntoView({block:'nearest'});
 const L=D.lines[sel];
 // context strip: two dimmed neighbours either side, focused line bold
 let ctx='';
 for(let k=sel-2;k<=sel+2;k++){ if(k<0||k>=D.lines.length)continue;
   ctx+=`<div class="ctx">${k===sel?'<b>'+esc(D.lines[k].line)+'</b>':esc(D.lines[k].line)}</div>`;}
 $('#ctx').innerHTML=ctx;
 renderTypes(); renderOverlays(); renderTerm();
}
function renderTypes(){
 const groups={new:'NEW (brand-new material)',edit_previous:'EDIT PREVIOUS',};
 const L=D.lines[sel];
 let html='<h3>media type</h3><div class="cols">';
 for(const [strat,title] of Object.entries({new:'NEW — brand-new material',edit_previous:'EDIT PREVIOUS',grid:'EDIT GROUP — rule of N'})){
   html+=`<div class="col"><h4>${title}</h4>`;
   D.catalog.filter(c=> strat==='grid'? c.layout_kind==='grid' : (c.strategy===strat && c.layout_kind!=='grid'))
    .forEach(c=>{
      const dis=(c.strategy==='edit_previous'&&sel===0)?'disabled':'';
      const on=(L.row.template===c.template)?' on':'';
      html+=`<button class="tpl${on}" ${dis} style="background:${c.color}" `+
            `onclick="pick('${c.template}')">${c.label}`+
            `<small>${c.ai?'AI':''}</small></button>`;});
   html+='</div>';}
 html+='</div>';
 $('#typepanel').innerHTML=html;
}
function renderOverlays(){
 const L=D.lines[sel]; const hasBase=!!L.row.template;
 const cur=(L.row.shot&&L.row.shot.overlay)||'none';
 const opts=[['none','no overlay'],['auto_text','+ caption it'],['draw','+ draw on it'],['object_edit','+ object edit']];
 $('#overlaybar').innerHTML='<h3>layer on top (optional)</h3>'+opts.map(([v,l])=>
   `<button ${hasBase?'':'disabled'} class="${cur===v?'on':''}" onclick="overlay('${v}')">${l}</button>`).join('')
   +(hasBase?'':'<span class="chiplbl">pick a base type first — there must be something to decorate</span>');
}
function renderTerm(){
 const L=D.lines[sel]; const has=(L.row.search_term||'').length>0;
 $('#termopen').style.display=has?'block':'none';
 $('#termcollapsed').style.display=has?'none':'block';
 $('#term').value=L.row.search_term||'';
 const s=L.suggest; let chips='';
 const add=(lbl,arr)=>{if(arr&&arr.length){chips+=`<span class="chiplbl">${lbl}:</span>`+
   arr.map(w=>`<button onclick="chip('${esc(w).replace(/'/g,"\\'")}')">${esc(w)}</button>`).join('');}};
 add('nouns',s.nouns);add('places',s.places);add('names',s.names);add('keywords',s.keywords);add('words',s.words);
 $('#chips').innerHTML=chips||'<span class="chiplbl">(no extracted suggestions for this line)</span>';
}
$('#termcollapsed').onclick=()=>{$('#termcollapsed').style.display='none';$('#termopen').style.display='block';$('#term').focus();};
function chip(w){const t=$('#term');t.value=(t.value?t.value+' ':'')+w;t.dispatchEvent(new Event('input'));}
async function pick(tpl){
 const c=D.catalog.find(x=>x.template===tpl); const L=D.lines[sel];
 const shot=JSON.parse(JSON.stringify(c.shot));
 if(curOverlay&&curOverlay!=='none') shot.overlay=curOverlay;
 await save({template:tpl, search_type:(c.ai?c.legacy_ai:c.legacy), shot});
 renderTypes(); renderOverlays();
}
async function overlay(v){
 const L=D.lines[sel]; if(!L.row.template)return; curOverlay=v;
 const shot=JSON.parse(JSON.stringify(L.row.shot||{})); shot.overlay=v;
 await save({shot}); renderOverlays();
}
let tmr=null;
$('#term').addEventListener('input',()=>{clearTimeout(tmr);tmr=setTimeout(()=>save({search_term:$('#term').value}),400);});
async function save(patch){
 const L=D.lines[sel];
 const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({line:L.line,patch})});
 if(r.ok){Object.assign(L.row,patch);L.row.manual=true;renderList();
   $('#saved').style.opacity=1;setTimeout(()=>$('#saved').style.opacity=0,900);}
}
document.addEventListener('keydown',e=>{
 if(e.target.tagName==='TEXTAREA')return;
 if(e.key==='ArrowDown'){focus(sel+1);e.preventDefault();}
 if(e.key==='ArrowUp'){focus(sel-1);e.preventDefault();}
});
load();
</script></body></html>"""


# =============================================================================
# server
# =============================================================================

class _State:
    def __init__(self, json_path: Path):
        self.json_path = json_path
        self.data: Dict[str, dict] = json.loads(
            json_path.read_text(encoding="utf-8"))
        m = re.match(r"(?:TESTING_)?(.+?)-script_to_search_term",
                     json_path.stem)
        prefix = m.group(1) if m else json_path.stem
        cache = find_split_cache(prefix)
        triples = (json.loads(cache.read_text(encoding="utf-8"))
                   if cache else None)
        self.suggest = build_suggestions(list(self.data), triples)
        self.catalog = build_catalog()
        self.backed_up = False

    def payload(self) -> dict:
        return {
            "json_path": self.json_path.name,
            "catalog": self.catalog,
            "lines": [{"line": line, "row": row,
                       "suggest": self.suggest[line]}
                      for line, row in self.data.items()],
        }

    def save(self, line: str, patch: dict) -> bool:
        if not self.backed_up:
            shutil.copy2(self.json_path,
                         self.json_path.with_name(self.json_path.name + ".bak"))
            self.backed_up = True
        if apply_patch(self.data, line, patch):
            self.json_path.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False),
                encoding="utf-8")
        return line in self.data


def make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):        # keep the terminal quiet
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/data":
                self._send(json.dumps(state.payload()).encode("utf-8"),
                           "application/json")
            else:
                self._send(PAGE.encode("utf-8"), "text/html")

        def do_POST(self):
            if self.path != "/save":
                return self._send(b"{}", "application/json", 404)
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n).decode("utf-8"))
                ok = state.save(req["line"], req.get("patch", {}))
            except Exception:
                ok = False
            self._send(json.dumps({"ok": ok}).encode("utf-8"),
                       "application/json", 200 if ok else 400)
    return Handler


def make_server(json_path: Path, port: int = 0) -> ThreadingHTTPServer:
    """Build (but don't run) the server — port 0 picks a free one."""
    state = _State(json_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    server.state = state          # exposed for tests
    return server


def _pick_json(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg)
    hits = sorted(HERE.glob("*-script_to_search_term.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        sys.exit("No *-script_to_search_term.json found here. "
                 "Run SPLIT_AND_LABEL.py first (see MASTER_README.md).")
    return hits[0]


def main() -> None:
    json_path = _pick_json(sys.argv[1] if len(sys.argv) > 1 else None)
    server = make_server(json_path)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"manual tagging: {json_path.name}")
    print(f"  open {url}  (Ctrl-C to stop; every change saves instantly)")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped. edits are already saved.")


if __name__ == "__main__":
    main()

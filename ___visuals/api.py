"""
The decorator's generic entry point: base picture in, edited picture out.
A small chooser (tk buttons; terminal fallback if there is no display) lets
you stack tools in any order — each tool's output feeds the next — then
'finish' saves to out_path. Cancelling with no edits returns None so the
caller keeps the original.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import shutil
import tempfile
from pathlib import Path

from ___visuals.decorator.tools import TOOLS, ToolContext


def _choose_tool(available: list[str], title: str, step: int) -> str:
    """One tool pick: tk button menu, or terminal input without a display.
    Returns a tool name or 'finish'."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title(title)
        root.configure(bg="#1e1e24")
        choice = {"v": "finish"}
        tk.Label(root, text=("what next?" if step else "pick a tool:"),
                 bg="#1e1e24", fg="#dddddd",
                 font=("Arial", 12, "bold")).pack(padx=18, pady=(14, 6))

        def pick(name):
            choice["v"] = name
            root.destroy()

        for name in available:
            label, _ = TOOLS[name]
            tk.Button(root, text=f"{name} — {label}", width=34, anchor="w",
                      command=lambda n=name: pick(n)
                      ).pack(padx=18, pady=3)
        tk.Button(root, text=("✓ finish — use the edited image" if step
                              else "cancel — keep the original"),
                  width=34, command=lambda: pick("finish")
                  ).pack(padx=18, pady=(10, 14))
        root.lift()
        root.attributes("-topmost", True)
        root.mainloop()
        return choice["v"]
    except Exception:
        opts = "/".join(available) + "/finish"
        while True:
            ans = input(f"[decorator] tool? ({opts}): ").strip().lower()
            if ans in TOOLS or ans == "finish":
                return ans
            print(f"[decorator]   unknown: {ans!r}")


def run_decorator(base_image_path: str,
                  out_path: str,
                  stamps: list[str] | tuple = (),
                  prefill_text: str = "",
                  title: str = "decorate",
                  tools: tuple[str, ...] = ("stamp", "zoom", "draw", "text"),
                  ) -> str | None:
    """Open the decorator on base_image_path. Returns out_path once at least
    one tool changed the image and you hit finish; None if you finished with
    no edits (caller should keep the original footage)."""
    base_image_path = str(base_image_path)
    available = [t for t in tools if t in TOOLS]
    work = Path(tempfile.mkdtemp(prefix="decorator_"))
    ctx = ToolContext(current=base_image_path, work_dir=work,
                      stamps=[str(s) for s in stamps],
                      prefill_text=prefill_text, title=title)
    print(f"[decorator] editing {Path(base_image_path).name}  "
          f"(tools: {', '.join(available)})")

    edited = False
    step = 0
    while True:
        name = _choose_tool(available, title, step)
        if name == "finish":
            break
        _, fn = TOOLS[name]
        result = fn(ctx)
        if result:
            ctx.current = result
            edited = True
            step += 1

    if not edited:
        print("[decorator] finished with no edits — keeping the original")
        return None
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ctx.current, out_path)
    print(f"[decorator] ✓ saved {out_path}")
    return out_path

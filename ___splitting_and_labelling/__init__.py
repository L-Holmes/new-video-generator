"""
___splitting_and_labelling — script in, tagged shot list out.

    from ___splitting_and_labelling import main, MANUAL_TAGGING

    main.generate_script_to_search_term("script-whales.txt")   # 0, 1, 2
    MANUAL_TAGGING.run_manual_tagging(out_path)                # 3

That is the whole outside surface — main.py is its only caller. Everything
else in here talks to its neighbours directly, by bare module name, which
works because importing this package (or any module in it) runs PATHS.

WHY THIS FILE IS THE ONE THING NOT IN A FOLDER
    __init__.py names the folder it is IN. Move it down a level and that
    subfolder becomes the package instead, and the import above stops
    resolving. Its home IS the package root — there is no folder to file it
    under.

    Read shared/PATHS.py next: it is the map of every other folder here, and
    the three lines a new file needs to reach them.
"""
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent / "shared"))
import PATHS  # noqa: F401,E402  — every folder of this package on sys.path


def __getattr__(name):
    """Fetch a stage module on demand, so `import ___splitting_and_labelling`
    stays cheap.

    main.py is a real submodule and needs none of this. MANUAL_TAGGING
    lives in a folder no import statement can name ("3-manual-tagging"), so
    the attribute access is what stands in for the dotted path that cannot
    be written. Eager imports would also drag spaCy and a http server in for
    anything that touched the package at all.
    """
    if name in ("MANUAL_TAGGING",):
        import importlib
        module = importlib.import_module(name)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

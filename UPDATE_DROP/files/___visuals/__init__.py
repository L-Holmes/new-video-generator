# ___visuals is a plain package. Keep this file EMPTY of imports — modules
# are imported directly (from ___visuals.CONFIG import ...), and the
# decorator lives in the ___visuals/decorator/ SUBFOLDER (its own package
# with its own __init__.py). Putting the decorator's init here by mistake
# makes every `import ___visuals.X` try to load the decorator first.

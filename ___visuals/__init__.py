# ___visuals is a plain package. Keep this file EMPTY of imports — modules
# are imported directly (from CONFIG import ...), and the SUBPACKAGES below
# each have their own __init__.py:
#
#   ai/         model-generated pictures (fal): generation, edit, postprocess,
#               generate_stickman_images
#   sources/    where footage comes from when it comes from outside:
#               downloads (Pexels), wikipedia, stamp_fetch
#   decorator/  the ONE hand-editing window (draw / stamp / zoom / object)
#   maths/      manim animations built from a scene's `data` column
#
# Putting a subpackage's init here by mistake makes every `import
# ___visuals.X` try to load that subpackage first.
#
# Every module name in here is lowercase_with_underscores — the file name and
# the import line then read the same, and there is no "was it SHOUTING?" to
# remember.

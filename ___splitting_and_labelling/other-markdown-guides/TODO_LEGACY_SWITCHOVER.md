# TODO — FULL LEGACY SWITCHOVER

The decision side already thinks entirely in the new `new__* / editprev__* /
editgroup__*` vocabulary; the renderer still consumes the old MediaType
strings via the bridge (`to_legacy()` writes them into `search_type`).
This file is the checklist for retiring the bridge when the renderer is
ready. **Until then: keep all legacy references.**

## Current legacy constraints being papered over

- `search_type` carries old strings ("joint_3_row", "stickman", ...).
- `editgroup__*` RULE OF N: the emitted `shot.layout.n` carries the REAL
  list size, but `position` still cycles 1..3 because the legacy renderer
  only draws 3-cell rows.
- `editgroup__same_stock` (same term every cell) emits as plain
  "joint_3_row" — the renderer has no same-term mode yet.
- `editprev__caption` emits "decorate_previous" (AI off) or
  "stickman_text_overlay" (AI on) — one concept, two legacy strings.
- `new__typography` emits "read_out" (the renderer's kinetic typography).
- `editgroup__*` semantics are richer than the renderer's collage: the
  intent is "first cell unique, following cells are related edits building
  alongside it" — the renderer currently just tiles.

## Switchover steps (in order)

1. **Renderer reads `template` + `shot`** instead of `search_type`:
   strategy/material/base/layout/overlay are all in the `shot` dict already
   emitted on every row. Run both paths in parallel behind a flag.
2. **Rule of N in the renderer**: draw `shot.layout.n` cells; `position`
   becomes the true 1..n index (remove the modulo in
   `SPLIT_AND_LABEL._advance_state_for`).
3. **Same-term grids**: implement `layout.same_term` (one search, n crops /
   n results of the same query).
4. **Editgroup semantics**: cell 1 unique, cells 2..n rendered as related
   edits building on cell 1.
5. **Overlay compositing**: honour `shot.overlay` on ANY strategy (e.g.
   `new__stock` + `draw`), retiring the dedicated caption/draw templates
   into overlay flags if desired.
6. Delete `legacy` / `legacy_ai` fields from `TemplateDef`, delete
   `to_legacy()` / `LEGACY_TO_TEMPLATE`, delete the `search_type` column.
7. Regenerate outputs for every in-flight script; update the review GUI to
   display template names.
8. Update EXTENDING_GUIDE.md ("Add a media type" loses its legacy bullet)
   and delete this file.

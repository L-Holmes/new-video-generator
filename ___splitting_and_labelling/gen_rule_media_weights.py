"""Generator for RULE_MEDIA_WEIGHTS.py.

Pulls the authoritative rule descriptions from sentence_splitter.RULE_DESCRIPTIONS
and pairs each rule id with a hand-tuned, stock-dominant affinity distribution
over the SHOT TEMPLATE names defined in SPLIT_AND_LABEL_CONFIG.SHOT_TEMPLATES.

These weights are TIER-3 material only: they never override the deterministic
tier-0 gates and tier-1 locks in SPLIT_AND_LABEL.py — they just shape the
sampling for lines that nothing locked.

v2 vocabulary changes (see SPLIT_AND_LABEL_CONFIG.py for the taxonomy):
    read_out                      -> REMOVED (folded into hold_previous)
    joint_3_row                   -> grid_different
    (new) grid_same               -> N cells, SAME search term
    manual_stock_add_to_previous  -> composite_onto_previous
    zoom_prev_img                 -> zoom_previous
    static_of_previous            -> hold_previous

Run:  python3 gen_rule_media_weights.py > RULE_MEDIA_WEIGHTS.py
"""
import sentence_splitter as ss

# Canonical order of the template names (mirrors SHOT_TEMPLATES).
# NO GRID COLUMNS: grids are LOCK-ONLY (tier-1 list lock) and can never be
# sampled at tier 3, so per-rule grid affinities would be dead weight.
MEDIA_ORDER = [
    "stock", "object_generate", "wikipedia", "map", "typography_blank",
    "stickman", "ai_edit_previous",
    "stickman_board_stock", "stickman_board_wikipedia",
    "composite_onto_previous", "zoom_previous",
    "hold_previous", "decorate_previous", "caption_previous",
]

# Stock-dominant default for EVERY rule (the "most things are stock" prior).
# hold_previous absorbs the retired read_out's baseline; typography_blank
# exists almost exclusively via the cold-open lock.
DEFAULT = {
    "stock": 0.45, "object_generate": 0.12, "wikipedia": 0.08, "map": 0.05,
    "typography_blank": 0.01,
    "composite_onto_previous": 0.03, "zoom_previous": 0.03,
    "hold_previous": 0.08, "decorate_previous": 0.04,
}

# AI + caption columns are DERIVED from the hand-tuned non-AI ones so the
# two vocabularies can never drift apart (see build_dist):
#   stickman                  = 0.90 x stock      (the AI workhorse)
#   ai_edit_previous          = max(composite, zoom)
#   stickman_board_stock      = 0.50 x stock
#   stickman_board_wikipedia  = 0.75 x wikipedia
#   caption_previous          = decorate_previous
# Override any of them per rule with the short keys below if a rule needs
# a different AI taste than its non-AI counterpart.

# Per-rule OVERRIDES — only the types this rule should raise above the default.
# (Anything omitted keeps the DEFAULT / derived value.) Short keys:
#   S  stock            OG object_generate   WK wikipedia
#   MP map              TY typography_blank  SM stickman
#   AE ai_edit_previous B+ stickman_board_stock  BW stickman_board_wikipedia
#   C+ composite_onto_previous               ZP zoom_previous
#   HP hold_previous    DP decorate_previous CP caption_previous
_K = {"S": "stock", "OG": "object_generate", "WK": "wikipedia", "MP": "map",
      "TY": "typography_blank", "SM": "stickman", "AE": "ai_edit_previous",
      "B+": "stickman_board_stock", "BW": "stickman_board_wikipedia",
      "C+": "composite_onto_previous", "ZP": "zoom_previous",
      "HP": "hold_previous", "DP": "decorate_previous",
      "CP": "caption_previous"}

OVERRIDES = {
    # --- SPLITTING RULES ---------------------------------------------------
    1:  {"S": 0.55, "OG": 0.20},                                  # sentence boundary
    2:  {"S": 0.45, "OG": 0.25, "ZP": 0.15},                      # dash break (dramatic)
    3:  {"S": 0.30, "ZP": 0.35, "HP": 0.30, "OG": 0.15},          # ellipsis end (hold/suspense)
    4:  {"OG": 0.60, "S": 0.35, "ZP": 0.20},                      # pre-ellipsis dramatic reveal
    5:  {"DP": 0.60, "HP": 0.35, "S": 0.20},                      # quoted speech (text on screen)
    6:  {"DP": 0.40, "HP": 0.30, "S": 0.25},                      # bracketed aside
    7:  {"S": 0.40, "MP": 0.12, "HP": 0.25, "DP": 0.15},          # scene-setting opener comma
    8:  {"S": 0.55, "OG": 0.18},                                  # plain clause comma
    9:  {"DP": 0.50, "WK": 0.22, "S": 0.30},                      # appositive "which/that ..."
    10: {"HP": 0.40, "ZP": 0.20, "S": 0.30},                      # clause starter when/because
    11: {"HP": 0.40, "ZP": 0.20, "S": 0.30},                      # but/or/so/yet coordination
    12: {"S": 0.50, "OG": 0.25},                                  # second finished action
    13: {"HP": 0.40, "S": 0.35, "ZP": 0.15},                      # long lead-in safety net
    14: {"MP": 0.12, "S": 0.42, "HP": 0.18},                      # long PP "beneath the floor"
    15: {"S": 0.30, "OG": 0.20},          # noun list
    16: {"S": 0.30, "OG": 0.20},          # bare noun list
    17: {"HP": 0.35, "ZP": 0.25, "S": 0.30},                      # list wrap-up "all sped past"
    18: {"WK": 0.65, "OG": 0.35, "MP": 0.30, "S": 0.35},          # NAME reveal (person/place/date/amount)
    19: {"OG": 0.60, "S": 0.40},                                  # money reveal (coins/gold)
    20: {"DP": 0.45, "HP": 0.30, "S": 0.30, "OG": 0.20},          # short imperative (punchy text)
    21: {"HP": 0.35, "S": 0.35, "ZP": 0.15},                      # and/or joins two sentences
    23: {"DP": 0.55, "OG": 0.25, "S": 0.28},                      # adjective reveal
    24: {"OG": 0.35, "S": 0.42, "WK": 0.12},                      # numeric phrase reveal
    25: {"S": 0.30, "OG": 0.20},          # extra comma-list
    26: {"HP": 0.42, "ZP": 0.20, "S": 0.30},                      # long subordinate opener comma
    27: {"DP": 0.52, "OG": 0.25, "S": 0.28},                      # terminal adjective pair
    28: {"S": 0.40, "OG": 0.28, "WK": 0.15},          # noun-stuffed PP
    29: {"OG": 0.35, "C+": 0.38, "S": 0.30},                      # participle intro "revealing"
    30: {"MP": 0.78, "S": 0.30, "WK": 0.15},                      # post-entity "in Oregon" (PLACE)
    31: {"HP": 0.40, "S": 0.30, "ZP": 0.15},                      # comma after long clause
    32: {"S": 0.42, "MP": 0.10, "OG": 0.20},                      # infinitive "to reach the coast"
    33: {"OG": 0.30, "MP": 0.10, "S": 0.44},                      # terminal "of ..." reveal
    34: {"HP": 0.35, "ZP": 0.32, "S": 0.30, "OG": 0.20},          # progressive action
    35: {"DP": 0.48, "OG": 0.30, "S": 0.30},                      # copula attribute reveal
    36: {"HP": 0.40, "ZP": 0.22, "S": 0.25},                      # "of being watched"
    37: {"MP": 0.12, "S": 0.38, "DP": 0.22},                      # terminal PP "for absurd distances"
    38: {"OG": 0.52, "S": 0.40},                                  # phrasal object reveal "a huge tent"
    39: {"S": 0.42, "MP": 0.12, "OG": 0.25},                      # prep object "in the rock"
    40: {"C+": 0.38, "HP": 0.30, "ZP": 0.22, "S": 0.30},          # transition "then/suddenly"
    41: {"ZP": 0.35, "HP": 0.40, "DP": 0.18},                     # cliffhanger conj "because"
    42: {"OG": 0.52, "S": 0.40, "DP": 0.20},                      # copula reveal "a frozen wasteland"
    43: {"OG": 0.55, "C+": 0.28, "S": 0.35},                      # possession reveal "whale bones"
    44: {"OG": 0.52, "S": 0.40, "MP": 0.15},                      # creation reveal "a deep canyon"
    45: {"OG": 0.52, "S": 0.40, "WK": 0.18},                      # perception reveal "fossil skeletons"
    46: {"MP": 0.58, "S": 0.35, "ZP": 0.18},                      # spatial "across the plain"
    47: {"HP": 0.35, "OG": 0.25, "S": 0.30, "ZP": 0.20},          # result "that satellites use it"
    48: {"WK": 0.52, "OG": 0.30, "S": 0.30, "DP": 0.20},          # equation "Valley of the Whales"
    49: {"OG": 0.30, "C+": 0.26, "S": 0.35},          # "and blistering heat"
    50: {"WK": 0.72, "OG": 0.30, "S": 0.30},                      # title-name "Alaric the Goth"
    51: {"S": 0.30, "OG": 0.20},          # first list item
    52: {"OG": 0.35, "DP": 0.32, "WK": 0.22, "S": 0.35},          # explaining label
    53: {"S": 0.42, "OG": 0.25, "DP": 0.20, "MP": 0.15},          # numeric opener "In 1946,"
    54: {"DP": 0.52, "OG": 0.25, "S": 0.28},                      # terminal descriptor
    55: {"OG": 0.30, "MP": 0.08, "S": 0.44},                      # approximate amount
    # --- v18 rules (56-60) — previously missing from the weights table -----
    56: {"OG": 0.45, "S": 0.45, "ZP": 0.15},                      # comparison "like a graveyard"
    57: {"ZP": 0.40, "DP": 0.30, "S": 0.35, "OG": 0.25},          # exception "except one house"
    58: {"DP": 0.55, "HP": 0.35, "S": 0.25},                      # retention hook "here's the thing"
    59: {"S": 0.45, "WK": 0.30, "OG": 0.25},                      # passive agent "by a farmer"
    60: {"DP": 0.60, "HP": 0.35, "S": 0.20},                      # SFX beat "boom"

    # --- MERGING / GLUE RULES (low-visual connective tissue) --------------
    1000: {"HP": 0.40, "ZP": 0.20, "S": 0.30},
    1001: {"HP": 0.40, "ZP": 0.20, "S": 0.30},
    1002: {"HP": 0.35, "S": 0.35, "ZP": 0.15},
    1003: {"DP": 0.35, "HP": 0.30, "S": 0.30},
    1004: {"HP": 0.35, "ZP": 0.15, "S": 0.35},
    1005: {"OG": 0.30, "C+": 0.25, "S": 0.35, "HP": 0.20},
    1006: {"S": 0.50, "OG": 0.20},
    1007: {"HP": 0.35, "S": 0.35, "ZP": 0.15},
    1008: {"HP": 0.45, "ZP": 0.25, "S": 0.30},
    1009: {"HP": 0.45, "ZP": 0.25, "S": 0.30},
}


def build_dist(rid):
    d = dict(DEFAULT)
    overrides = OVERRIDES.get(rid, {})
    for short, val in overrides.items():
        d[_K[short]] = val
    # derived AI + caption columns (only when not explicitly overridden)
    derived = {
        "stickman": round(0.90 * d["stock"], 3),
        "ai_edit_previous": max(d["composite_onto_previous"],
                                d["zoom_previous"]),
        "stickman_board_stock": round(0.50 * d["stock"], 3),
        "stickman_board_wikipedia": round(0.75 * d["wikipedia"], 3),
        "caption_previous": d["decorate_previous"],
    }
    for name, val in derived.items():
        short = next(s for s, full in _K.items() if full == name)
        if short not in overrides:
            d[name] = val
    return {k: round(d[k], 3) for k in MEDIA_ORDER}


def main():
    ids = sorted(ss.RULE_DESCRIPTIONS.keys())
    out = []
    out.append('"""')
    out.append("RULE_MEDIA_WEIGHTS.py  —  AUTO-GENERATED, but meant to be reviewed & edited")
    out.append("=========================================================================")
    out.append("For every tagged rule id the splitter can stamp on a line, this maps:")
    out.append("")
    out.append("    <rule id> -> { 'description': <what the splitter spotted>,")
    out.append("                   'media_type_probabilities': { <shot template>: affinity } }")
    out.append("")
    out.append("The keys are SHOT TEMPLATE names from SPLIT_AND_LABEL_CONFIG.SHOT_TEMPLATES")
    out.append("(NOT the renderer's legacy MediaType strings — the legacy bridge in the")
    out.append("config translates at emit time).")
    out.append("")
    out.append("The affinities are INDEPENDENT scores in [0,1] ('how well does this shot")
    out.append("template suit a line the splitter cut for THIS reason'), NOT a")
    out.append("distribution — they do not sum to 1.  They matter ONLY for TIER 3 of the")
    out.append("decision ladder in SPLIT_AND_LABEL.py: tier-0 gates and tier-1 locks run")
    out.append("first and are deterministic; these weights just shape the sampling for")
    out.append("lines that nothing locked.")
    out.append("")
    out.append("Descriptions are mirrored from sentence_splitter.RULE_DESCRIPTIONS so you")
    out.append("can review the number -> meaning -> shot-template linking in one place.")
    out.append("Regenerate with:  python3 gen_rule_media_weights.py > RULE_MEDIA_WEIGHTS.py")
    out.append('"""')
    out.append("")
    out.append("RULE_MEDIA_WEIGHTS = {")
    for rid in ids:
        desc = ss.RULE_DESCRIPTIONS[rid]
        dist = build_dist(rid)
        out.append(f"    {rid}: {{")
        out.append(f"        \"description\": {desc!r},")
        out.append(f"        \"media_type_probabilities\": {{")
        for k in MEDIA_ORDER:
            out.append(f"            {k!r}: {dist[k]},")
        out.append(f"        }},")
        out.append(f"    }},")
    out.append("}")
    out.append("")
    print("\n".join(out))


if __name__ == "__main__":
    main()

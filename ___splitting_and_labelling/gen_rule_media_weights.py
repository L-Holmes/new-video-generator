"""Generator for RULE_MEDIA_WEIGHTS.py.

Pulls the authoritative rule descriptions from sentence_splitter.RULE_DESCRIPTIONS
and pairs each rule id with a hand-tuned, stock-dominant affinity distribution
over the (non-AI) media types. Emits a clean, reviewable Python file.

Run:  python3 gen_rule_media_weights.py > RULE_MEDIA_WEIGHTS.py
"""
import sys
import sentence_splitter as ss

# Canonical order of the choosable (non-AI) media types.
MEDIA_ORDER = [
    "stock", "object_generate", "read_out", "wikipedia", "map",
    "joint_3_row", "manual_stock_add_to_previous", "zoom_prev_img",
    "static_of_previous", "decorate_previous",
]

# Stock-dominant default for EVERY rule (the "most things are stock" prior).
DEFAULT = {
    "stock": 0.45, "object_generate": 0.12, "read_out": 0.10, "wikipedia": 0.08,
    "map": 0.05, "joint_3_row": 0.05, "manual_stock_add_to_previous": 0.03,
    "zoom_prev_img": 0.03, "static_of_previous": 0.04, "decorate_previous": 0.03,
}

# Per-rule OVERRIDES — only the types this rule should raise above the default.
# (Anything omitted keeps the DEFAULT value.) Short keys:
#   S  stock          OG object_generate   RO read_out       WK wikipedia
#   MP map            J3 joint_3_row        M+ manual_stock_add_to_previous
#   ZP zoom_prev_img  SP static_of_previous DP decorate_previous
_K = {"S": "stock", "OG": "object_generate", "RO": "read_out",
      "WK": "wikipedia", "MP": "map", "J3": "joint_3_row",
      "M+": "manual_stock_add_to_previous", "ZP": "zoom_prev_img",
      "SP": "static_of_previous", "DP": "decorate_previous"}

OVERRIDES = {
    # --- SPLITTING RULES ---------------------------------------------------
    1:  {"S": 0.55, "OG": 0.20},                                  # sentence boundary
    2:  {"S": 0.45, "OG": 0.25, "ZP": 0.15},                      # dash break (dramatic)
    3:  {"S": 0.30, "ZP": 0.35, "SP": 0.30, "OG": 0.15},          # ellipsis end (hold/suspense)
    4:  {"OG": 0.60, "S": 0.35, "ZP": 0.20},          # pre-ellipsis dramatic reveal
    5:  {"RO": 0.70, "S": 0.20, "DP": 0.15},                      # quoted speech
    6:  {"DP": 0.40, "RO": 0.30, "SP": 0.20, "S": 0.25},          # bracketed aside
    7:  {"S": 0.40, "MP": 0.12, "RO": 0.20, "SP": 0.20},          # scene-setting opener comma
    8:  {"S": 0.55, "OG": 0.18},                                  # plain clause comma
    9:  {"DP": 0.50, "WK": 0.22, "S": 0.30},                      # appositive "which/that ..."
    10: {"RO": 0.40, "SP": 0.30, "S": 0.30},                      # clause starter when/because
    11: {"RO": 0.40, "SP": 0.25, "S": 0.30},                      # but/or coordination
    12: {"S": 0.50, "OG": 0.25},                                  # second finished action
    13: {"RO": 0.40, "S": 0.35, "SP": 0.20},                      # long lead-in safety net
    14: {"MP": 0.12, "S": 0.42, "SP": 0.18},                      # long PP "beneath the floor"
    15: {"J3": 0.80, "S": 0.30, "OG": 0.20},                      # noun list
    16: {"J3": 0.80, "S": 0.30, "OG": 0.20},                      # bare noun list
    17: {"RO": 0.35, "SP": 0.30, "S": 0.30},                      # list wrap-up "all sped past"
    18: {"WK": 0.65, "OG": 0.35, "MP": 0.30, "S": 0.35},          # NAME reveal (person/place/date/amount)
    19: {"OG": 0.60, "S": 0.40},                                  # money reveal (coins/gold)
    20: {"RO": 0.50, "S": 0.30, "OG": 0.20, "DP": 0.15},          # short imperative
    21: {"RO": 0.35, "S": 0.35, "SP": 0.20},                      # and/or joins two sentences
    23: {"DP": 0.55, "OG": 0.25, "S": 0.28},                      # adjective reveal
    24: {"OG": 0.35, "S": 0.42, "WK": 0.12},                      # numeric phrase reveal
    25: {"J3": 0.72, "S": 0.30, "OG": 0.20},                      # extra comma-list
    26: {"RO": 0.42, "SP": 0.30, "S": 0.30},                      # long subordinate opener comma
    27: {"DP": 0.52, "OG": 0.25, "S": 0.28},                      # terminal adjective pair
    28: {"S": 0.40, "J3": 0.30, "OG": 0.28, "WK": 0.15},          # noun-stuffed PP
    29: {"OG": 0.35, "M+": 0.38, "S": 0.30},                      # participle intro "revealing"
    30: {"MP": 0.78, "S": 0.30, "WK": 0.15},                      # post-entity "in Oregon" (PLACE)
    31: {"RO": 0.40, "S": 0.30, "SP": 0.20},                      # comma after long clause
    32: {"S": 0.42, "MP": 0.10, "OG": 0.20},                      # infinitive "to reach the coast"
    33: {"OG": 0.30, "MP": 0.10, "S": 0.44},                      # terminal "of ..." reveal
    34: {"SP": 0.38, "ZP": 0.32, "S": 0.30, "OG": 0.20},          # progressive action
    35: {"DP": 0.48, "OG": 0.30, "S": 0.30},                      # copula attribute reveal
    36: {"RO": 0.42, "SP": 0.30, "S": 0.25},                      # "of being watched"
    37: {"MP": 0.12, "S": 0.38, "DP": 0.22},                      # terminal PP "for absurd distances"
    38: {"OG": 0.52, "S": 0.40},                                  # phrasal object reveal "a huge tent"
    39: {"S": 0.42, "MP": 0.12, "OG": 0.25},                      # prep object "in the rock"
    40: {"M+": 0.38, "RO": 0.30, "SP": 0.26, "S": 0.30},          # transition "then/suddenly"
    41: {"RO": 0.50, "ZP": 0.22, "SP": 0.20},                     # cliffhanger conj "because"
    42: {"OG": 0.52, "S": 0.40, "DP": 0.20},                      # copula reveal "a frozen wasteland"
    43: {"OG": 0.55, "M+": 0.28, "S": 0.35},                      # possession reveal "whale bones"
    44: {"OG": 0.52, "S": 0.40, "MP": 0.15},                      # creation reveal "a deep canyon"
    45: {"OG": 0.52, "S": 0.40, "WK": 0.18},                      # perception reveal "fossil skeletons"
    46: {"MP": 0.58, "S": 0.35, "SP": 0.20},                      # spatial "across the plain"
    47: {"RO": 0.42, "OG": 0.25, "S": 0.30},                      # result "that satellites use it"
    48: {"WK": 0.52, "OG": 0.30, "RO": 0.20, "S": 0.30},          # equation "Valley of the Whales"
    49: {"J3": 0.42, "OG": 0.30, "M+": 0.26, "S": 0.35},          # "and blistering heat"
    50: {"WK": 0.72, "OG": 0.30, "S": 0.30},                      # title-name "Alaric the Goth"
    51: {"J3": 0.72, "S": 0.30, "OG": 0.20},                      # first list item
    52: {"OG": 0.35, "DP": 0.32, "WK": 0.22, "S": 0.35},          # explaining label
    53: {"S": 0.42, "OG": 0.25, "RO": 0.20, "MP": 0.15},          # numeric opener "In 1946,"
    54: {"DP": 0.52, "OG": 0.25, "S": 0.28},                      # terminal descriptor
    55: {"OG": 0.30, "MP": 0.08, "S": 0.44},                      # approximate amount

    # --- MERGING / GLUE RULES (low-visual connective tissue) --------------
    1000: {"RO": 0.40, "SP": 0.30, "S": 0.30},
    1001: {"RO": 0.40, "SP": 0.30, "S": 0.30},
    1002: {"RO": 0.30, "S": 0.35, "SP": 0.25},
    1003: {"DP": 0.35, "RO": 0.30, "S": 0.30},
    1004: {"RO": 0.35, "SP": 0.25, "S": 0.35},
    1005: {"OG": 0.30, "M+": 0.25, "S": 0.35, "RO": 0.20},
    1006: {"S": 0.50, "OG": 0.20},
    1007: {"RO": 0.35, "S": 0.35, "SP": 0.20},
    1008: {"RO": 0.45, "SP": 0.35, "S": 0.30},
    1009: {"RO": 0.45, "SP": 0.35, "S": 0.30},
}


def build_dist(rid):
    d = dict(DEFAULT)
    for short, val in OVERRIDES.get(rid, {}).items():
        d[_K[short]] = val
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
    out.append("                   'media_type_probabilities': { <media type>: affinity } }")
    out.append("")
    out.append("The affinities are INDEPENDENT scores in [0,1] ('how well does this media")
    out.append("type suit a line the splitter cut for THIS reason'), NOT a distribution —")
    out.append("they do not sum to 1. SPLIT_AND_LABEL combines the per-rule affinities for")
    out.append("a line, applies the BIG GENERAL RULES (see SPLIT_AND_LABEL.py), then")
    out.append("normalises + samples to pick one media type.")
    out.append("")
    out.append("Descriptions are mirrored from sentence_splitter.RULE_DESCRIPTIONS so you")
    out.append("can review the number -> meaning -> media-type linking in one place.")
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

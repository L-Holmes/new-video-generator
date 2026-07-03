"""
Smoke tests for the v18 rules (56-60) using hand-annotated fake tokens.

The sandbox can't download en_core_web_sm, so this harness fakes the small
slice of the spaCy API the new rules touch (pos_/tag_/dep_/head/children/
subtree/sent/like_num/ent_type_) with annotations matching what the real
parser would produce.  It verifies rule LOGIC, not parser behaviour —
run the module's __main__ demo on a machine with the model for that.
"""
import sys, types

# ---- stub the spacy import so the module loads -----------------------------
spacy_stub = types.ModuleType("spacy")
tokens_stub = types.ModuleType("spacy.tokens")
class _T: ...
tokens_stub.Doc = tokens_stub.Span = tokens_stub.Token = _T
spacy_stub.tokens = tokens_stub
spacy_stub.language = types.ModuleType("spacy.language")
sys.modules["spacy"] = spacy_stub
sys.modules["spacy.tokens"] = tokens_stub

import sentence_splitter as ss


# ---- minimal fake Doc / Token ----------------------------------------------
class FakeToken:
    def __init__(self, i, text, pos, tag="", dep="", lemma=None, head=None,
                 ent="", like_num=False):
        self.i, self.text = i, text
        self.pos_, self.tag_, self.dep_ = pos, tag, dep
        self.lemma_ = lemma if lemma is not None else text.lower()
        self.lower_ = text.lower()
        self.ent_type_, self.like_num = ent, like_num
        self.is_punct = pos == "PUNCT"
        self.is_space = False
        self.ent_iob_ = "O"
        self._head_i = head          # index of head; resolved by FakeDoc
        self.head = self
        self.children = []
        self.sent = None

    @property
    def subtree(self):
        out, stack = [], [self]
        while stack:
            t = stack.pop()
            out.append(t)
            stack.extend(t.children)
        return sorted(out, key=lambda t: t.i)


class FakeSent(list):
    @property
    def start(self): return self[0].i
    @property
    def end(self): return self[-1].i + 1


class FakeDoc:
    def __init__(self, rows):
        self.toks = [FakeToken(i, *r) for i, r in enumerate(rows)]
        for t in self.toks:
            if t._head_i is not None and t._head_i != t.i:
                t.head = self.toks[t._head_i]
                self.toks[t._head_i].children.append(t)
        sent = FakeSent(self.toks)          # single-sentence docs are enough
        for t in self.toks:
            t.sent = sent
    def __len__(self): return len(self.toks)
    def __getitem__(self, k):
        if isinstance(k, slice):
            return self.toks[k]
        return self.toks[k]
    def __iter__(self): return iter(self.toks)


def show(name, doc, splits):
    idx = sorted(splits | {0, len(doc)})
    parts = [" ".join(t.text for t in doc[lo:hi])
             for lo, hi in zip(idx, idx[1:])]
    print(f"{name:28s} → {' | '.join(p for p in parts if p)}")
    return parts


fails = []
def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


# ============ RULE 56 — comparison reveal ====================================
# "From above it looked like a graveyard of giants"
#   (text, pos, tag, dep, lemma, head)
doc = FakeDoc([
    ("From",      "ADP",  "IN",  "prep",  "from",      3),
    ("above",     "ADV",  "RB",  "pcomp", "above",     0),
    ("it",        "PRON", "PRP", "nsubj", "it",        3),
    ("looked",    "VERB", "VBD", "ROOT",  "look",      None),
    ("like",      "ADP",  "IN",  "prep",  "like",      3),
    ("a",         "DET",  "DT",  "det",   "a",         6),
    ("graveyard", "NOUN", "NN",  "pobj",  "graveyard", 4),
    ("of",        "ADP",  "IN",  "prep",  "of",        6),
    ("giants",    "NOUN", "NNS", "pobj",  "giant",     7),
])
s = ss.rule_comparison_reveal(doc, {0, len(doc)})
show("56 simile 'like'", doc, s)
check(4 in s, "split lands before 'like'")

# approximator use must NOT fire: "There were like 500 people there yesterday"
doc = FakeDoc([
    ("There",     "PRON", "EX",  "expl",  "there",  1),
    ("were",      "VERB", "VBD", "ROOT",  "be",     None),
    ("like",      "ADP",  "IN",  "prep",  "like",   1),
    ("500",       "NUM",  "CD",  "nummod","500",    4),
    ("people",    "NOUN", "NNS", "pobj",  "people", 2),
    ("there",     "ADV",  "RB",  "advmod","there",  1),
    ("yesterday", "NOUN", "NN",  "npadvmod","yesterday", 1),
])
doc[3].like_num = True
s = ss.rule_comparison_reveal(doc, {0, len(doc)})
check(2 not in s, "approximator 'like 500' does not fire")

# "as if" clausal simile — split AFTER the bigram
doc = FakeDoc([
    ("The",       "DET",  "DT",  "det",    "the",     1),
    ("ground",    "NOUN", "NN",  "nsubj",  "ground",  2),
    ("moved",     "VERB", "VBD", "ROOT",   "move",    None),
    ("as",        "SCONJ","IN",  "mark",   "as",      7),
    ("if",        "SCONJ","IN",  "mark",   "if",      7),
    ("something", "PRON", "NN",  "nsubj",  "something",7),
    ("underneath","ADV",  "RB",  "advmod", "underneath",7),
    ("was",       "AUX",  "VBD", "aux",    "be",      7),
    ("breathing", "VERB", "VBG", "advcl",  "breathe", 2),
])
# fix: 'was' aux of breathing (index 8), nsubj to 8 too
s = ss.rule_comparison_reveal(doc, {0, len(doc)})
show("56 'as if' clause", doc, s)
check(5 in s, "split lands after 'as if'")

# ============ RULE 57 — exception reveal =====================================
doc = FakeDoc([
    ("Everything","PRON", "NN",  "nsubj", "everything", 1),
    ("on",        "ADP",  "IN",  "prep",  "on",         1),
    ("the",       "DET",  "DT",  "det",   "the",        3),
    ("street",    "NOUN", "NN",  "pobj",  "street",     1),
    ("burned",    "VERB", "VBD", "ROOT",  "burn",       None),
    ("except",    "ADP",  "IN",  "prep",  "except",     4),
    ("one",       "NUM",  "CD",  "nummod","one",        7),
    ("house",     "NOUN", "NN",  "pobj",  "house",      5),
])
s = ss.rule_exception_reveal(doc, {0, len(doc)})
show("57 'except'", doc, s)
check(5 in s, "split lands before 'except'")

# bigram "apart from"
doc = FakeDoc([
    ("The",    "DET",  "DT", "det",   "the",    1),
    ("valley", "NOUN", "NN", "nsubj", "valley", 2),
    ("is",     "AUX",  "VBZ","ROOT",  "be",     None),
    ("empty",  "ADJ",  "JJ", "acomp", "empty",  2),
    ("apart",  "ADV",  "RB", "advmod","apart",  2),
    ("from",   "ADP",  "IN", "prep",  "from",   4),
    ("the",    "DET",  "DT", "det",   "the",    7),
    ("bones",  "NOUN", "NNS","pobj",  "bone",   5),
])
s = ss.rule_exception_reveal(doc, {0, len(doc)})
show("57 'apart from'", doc, s)
check(4 in s, "split lands before 'apart from'")

# ============ RULE 58 — discourse pivot ======================================
doc = FakeDoc([
    ("Here",  "ADV",  "RB",  "advmod","here",  2),
    ("'s",    "AUX",  "VBZ", "ROOT",  "be",    None),
    ("the",   "DET",  "DT",  "det",   "the",   3),
    ("thing", "NOUN", "NN",  "attr",  "thing", 1),
    ("the",   "DET",  "DT",  "det",   "the",   5),
    ("map",   "NOUN", "NN",  "nsubj", "map",   6),
    ("was",   "AUX",  "VBD", "ccomp", "be",    1),
    ("wrong", "ADJ",  "JJ",  "acomp", "wrong", 6),
])
s = ss.rule_discourse_pivot(doc, {0, len(doc)})
show("58 here's the thing", doc, s)
check(4 in s, "split lands after 'here's the thing'")
check(0 not in {x for x in s if 0 < x < 4}, "no split inside the hook")

# noun-use guard: "A fun fact about whales" must not fire
doc = FakeDoc([
    ("A",     "DET",  "DT", "det",     "a",     2),
    ("fun",   "ADJ",  "JJ", "amod",    "fun",   2),
    ("fact",  "NOUN", "NN", "ROOT",    "fact",  None),
    ("about", "ADP",  "IN", "prep",    "about", 2),
    ("whales","NOUN", "NNS","pobj",    "whale", 3),
])
s = ss.rule_discourse_pivot(doc, {0, len(doc)})
check(not {1, 3} & s, "'a fun fact' noun use does not fire")

# ============ RULE 59 — passive agent reveal =================================
doc = FakeDoc([
    ("The",       "DET",  "DT",  "det",       "the",      1),
    ("skeletons", "NOUN", "NNS", "nsubjpass", "skeleton", 3),
    ("were",      "AUX",  "VBD", "auxpass",   "be",       3),
    ("uncovered", "VERB", "VBN", "ROOT",      "uncover",  None),
    ("by",        "ADP",  "IN",  "agent",     "by",       3),
    ("a",         "DET",  "DT",  "det",       "a",        7),
    ("passing",   "VERB", "VBG", "amod",      "pass",     7),
    ("herder",    "NOUN", "NN",  "pobj",      "herder",   4),
])
s = ss.rule_passive_agent_reveal(doc, {0, len(doc)})
show("59 passive agent", doc, s)
check(4 in s, "split lands before agent 'by'")

# deadline use must NOT fire: "The final report is due by Friday"
doc = FakeDoc([
    ("The",    "DET",  "DT",  "det",   "the",    2),
    ("final",  "ADJ",  "JJ",  "amod",  "final",  2),
    ("report", "NOUN", "NN",  "nsubj", "report", 4),
    ("is",     "AUX",  "VBZ", "ROOT",  "be",     None),
    ("due",    "ADJ",  "JJ",  "acomp", "due",    3),
    ("by",     "ADP",  "IN",  "prep",  "by",     4),
    ("Friday", "PROPN","NNP", "pobj",  "friday", 5),
])
doc[6].ent_type_ = "DATE"
s = ss.rule_passive_agent_reveal(doc, {0, len(doc)})
check(5 not in s, "'due by Friday' deadline does not fire")

# ============ RULE 60 — SFX beat =============================================
doc = FakeDoc([
    ("and",  "CCONJ","CC", "cc",       "and",  2),
    ("then", "ADV",  "RB", "advmod",   "then", 2),
    ("boom", "INTJ", "UH", "intj",     "boom", 6),
    ("the",  "DET",  "DT", "det",      "the",  4),
    ("roof", "NOUN", "NN", "nsubj",    "roof", 6),
    ("came", "VERB", "VBD","ROOT",     "come", None),
    ("down", "ADP",  "RP", "prt",      "down", 5),
])
# fix head of 'came' — token 5 is 'came'; adjust indices: rebuild properly
doc = FakeDoc([
    ("and",  "CCONJ","CC", "cc",     "and",  5),
    ("then", "ADV",  "RB", "advmod", "then", 5),
    ("boom", "INTJ", "UH", "intj",   "boom", 5),
    ("the",  "DET",  "DT", "det",    "the",  4),
    ("roof", "NOUN", "NN", "nsubj",  "roof", 5),
    ("came", "VERB", "VBD","ROOT",   "come", None),
    ("down", "ADP",  "RP", "prt",    "down", 5),
])
s = ss.rule_sfx_beat(doc)
show("60 bare 'boom'", doc, s)
check(2 in s and 3 in s, "'boom' isolated on its own line")

# noun use must NOT fire: "The crash killed the market"
doc = FakeDoc([
    ("The",    "DET",  "DT", "det",   "the",    1),
    ("crash",  "NOUN", "NN", "nsubj", "crash",  2),
    ("killed", "VERB", "VBD","ROOT",  "kill",   None),
    ("the",    "DET",  "DT", "det",   "the",    4),
    ("market", "NOUN", "NN", "dobj",  "market", 2),
])
s = ss.rule_sfx_beat(doc)
check(1 not in s and 2 not in s, "'the crash' noun use does not fire")

# verb-with-object must NOT fire: "She snapped a photo"
doc = FakeDoc([
    ("She",     "PRON", "PRP","nsubj", "she",   1),
    ("snapped", "VERB", "VBD","ROOT",  "snap",  None),
    ("a",       "DET",  "DT", "det",   "a",     3),
    ("photo",   "NOUN", "NN", "dobj",  "photo", 1),
])
s = ss.rule_sfx_beat(doc)
check(1 not in s, "'snapped a photo' verb use does not fire")

# ============ lexicon extensions =============================================
check("hear" in ss.ALL_PERCEPTION_LEMMAS and "swear" in ss.ALL_PERCEPTION_LEMMAS
      and "picture" in ss.ALL_PERCEPTION_LEMMAS,
      "RULE 45 lemma sets extended (hear / swear / picture)")

# rule 11 extension: "so" CCONJ with long lead
doc = FakeDoc([
    ("the",     "DET",  "DT", "det",   "the",    1),
    ("dam",     "NOUN", "NN", "nsubj", "dam",    2),
    ("broke",   "VERB", "VBD","ROOT",  "break",  None),
    ("apart",   "ADV",  "RB", "advmod","apart",  2),
    ("so",      "CCONJ","CC", "cc",    "so",     2),
    ("the",     "DET",  "DT", "det",   "the",    6),
    ("valley",  "NOUN", "NN", "nsubj", "valley", 7),
    ("flooded", "VERB", "VBD","conj",  "flood",  2),
])
s = ss.rule_but_or_coord(doc, {0, len(doc)})
show("11 'so' coordination", doc, s)
check(4 in s, "RULE 11 now splits before coordinating 'so'")

# pipeline / description wiring
check(all(n in dict((a, b) for a, b, *_ in ss._POSITIVE_PIPELINE)
          for n in ["rule_comparison_reveal", "rule_exception_reveal",
                    "rule_discourse_pivot", "rule_passive_agent_reveal",
                    "rule_sfx_beat"]),
      "all five rules registered in _POSITIVE_PIPELINE")
check(all(ss._SPLIT_RULE_IDS.get(n) == i for n, i in [
        ("rule_comparison_reveal", 56), ("rule_exception_reveal", 57),
        ("rule_discourse_pivot", 58), ("rule_passive_agent_reveal", 59),
        ("rule_sfx_beat", 60)]),
      "ids 56-60 wired in _SPLIT_RULE_IDS")
check(all(i in ss.RULE_DESCRIPTIONS for i in range(56, 61)),
      "descriptions 56-60 present")

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
sys.exit(1 if fails else 0)

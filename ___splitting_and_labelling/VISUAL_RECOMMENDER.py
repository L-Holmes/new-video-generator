"""
VISUAL_RECOMMENDER.py — smarter keyword recommendation for MANUAL_TAGGING.

WHAT THIS DOES (three stages, each usable on its own):

  1. MEMORY + SCORING
     Keeps track of every "visualisable" word/phrase seen in previous
     entries and continuously scores each one:  is it worth showing as a
     recommendation right now?  Capitalised nouns (proper names) score
     very high, frequently-mentioned things score high, nouns score high,
     verbs score low, and everything decays with distance from the
     current sentence (previous sentence ≈ full strength, 15 sentences
     back ≈ small but NOT zero).

  2. PAIRING
     Looks at the CURRENT entry's visualisables next to the remembered
     previous ones and asks "do these two naturally go together?"
     ("jar" + "nutmeg"  → yes → recommend "jar of nutmeg";
      "jar" + "concrete" → no).  Evidence comes from three places:
        a) curated category rules   (containers pair with substances…)
        b) what THIS transcript already showed us (words seen adjacent
           or in the same sentence earlier pair again)
        c) optionally, spaCy word vectors if installed (auto-detected —
           the module works fine without it).

  3. PRONOUN RESOLUTION
     Uses the same scored memory to guess what abstract words point at:
        'he'  -> Jerry Patternly (0.61)
        'it'  -> nutmeg          (0.55)
        'it' (2) -> saucepan     (0.20)     <- second 'it' in the sentence
     These are surfaced as a small table for the tagger's scrollable
     panel.

HOW THE TAGGER USES IT (stateless, idiot-proof — one call per line):

    from VISUAL_RECOMMENDER import suggestions_for_line
    payload = suggestions_for_line(all_lines, current_index,
                                   confirmed_terms=terms_already_tagged)
    # payload is plain dicts/lists -> json.dumps straight to the frontend
    # payload["singles"]  -> the recommendation tab buttons
    # payload["pairs"]    -> combined suggestions ("jar of nutmeg")
    # payload["pronouns"] -> rows for the scrollable pronoun panel

No third-party dependencies required.  If spaCy (+ a vector model such
as en_core_web_md) is importable it is used automatically to strengthen
the pairing stage; otherwise the built-in rules carry everything, and
ALL tests pass either way.
"""
from __future__ import annotations

import math
import re
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

# ============================================================================
# ║                                                                          ║
# ║   TUNING TABLE — every knob lives HERE and only here.                    ║
# ║   Change a number, rerun test_visual_recommender.py, done.               ║
# ║                                                                          ║
# ============================================================================
WEIGHTS = {
    # ---- part-of-speech base weights (the user-facing scoring rules) ------
    "PROPER_NOUN": 3.0,     # capitalised noun e.g. "Leonard Nimoy" — VERY HIGH
    "NOUN":        1.5,     # ordinary noun                          — HIGH
    "ADJECTIVE":   0.5,     # adjectives — mildly visual
    "VERB":        0.3,     # verbs                                  — LOW
    "OTHER":       0.1,     # anything else that slipped through

    # ---- frequency ---------------------------------------------------------
    # every extra mention adds to the score (each mention contributes its own
    # recency-weighted term, see below), PLUS this bonus per doubling of the
    # mention count so repeated concepts clearly rise to the top.
    "FREQUENCY_BONUS": 0.35,        # score *= 1 + BONUS * log2(times seen)

    # ---- recency (distance from the current sentence) ----------------------
    "RECENCY_HALF_LIFE": 5.0,       # a mention 1+HALF_LIFE sentences back is
                                    # worth half of one in the prev. sentence
    "RECENCY_FLOOR": 0.15,          # ...but NEVER less than this — old
                                    # context still matters, per the spec

    # ---- extra signals (Claude's additions — each with its reason) ---------
    "CONFIRMED_BOOST": 1.6,    # the user already tagged/searched this term
                               # before => a human said it matters. Strong.
    "MULTIWORD_BOOST": 1.25,   # "sausage roll" beats "roll" — multiword
                               # phrases are more specific search terms.
    "CONCRETE_BOOST": 1.3,     # in our concrete-thing lists => photographable
    "ABSTRACT_PENALTY": 0.35,  # -ness/-tion/-ity nouns ("happiness") are
                               # nouns but you can't take a picture of them.
    "SIMILE_PENALTY": 0.35,    # "texture OF CONCRETE", "LIKE a rocket" — the
                               # word is a comparison, not an object actually
                               # in the scene, so it should rarely win.
    "NEGATION_PENALTY": 0.30,  # "there was NO jar", "WITHOUT a map" — the
                               # thing is explicitly absent from the scene.
    "IDIOM_PENALTY": 0.30,     # "raining cats and dogs" — no cats, no dogs.
    "GENERIC_PENALTY": 0.30,   # "thing/way/time/week" — real nouns, useless
                               # search terms; keep them near the bottom.

    # ---- pronoun extras (Claude's additions, each with its reason) ----------
    "SUBJECT_BOOST": 1.3,      # the SUBJECT of a recent sentence is what the
                               # story is about — pronouns love subjects
                               # (centering theory, the rule newsreaders use)
    "REFLEXIVE_LOCAL_BOOST": 2.0,  # "Jerry hurt himself" — reflexives bind
                                   # to their OWN sentence's earlier person
    "GENDER_MISMATCH": 0.15,   # 'she' -> a known-male first name (or vice
                               # versa) is almost never right
    "PAIR_PARTNER_HORIZON": 40,   # only pair with words seen in the last
                                  # N entries (older can't clear the bar)
    "LINK_THEME": 0.80,        # theme combos: "Roman temples"
    "THEME_RECENCY_FLOOR": 0.6,   # themes barely decay — the setting of
                                  # the piece stays relevant to the end
    "THEME_MIN_STRENGTH": 2.0,    # gate before something counts as theme
    "PRONOUN_HORIZON": 25,        # pronoun candidates further back than
                                  # this have prob ~= the floor: pointless

    # ---- what actually gets shown ------------------------------------------
    "SHOW_THRESHOLD": 0.55,    # minimum score for the recommendation tabs
    "MAX_SINGLES": 6,          # cap on single-word recommendations
    "MAX_PAIRS": 3,            # cap on paired suggestions

    # ---- pairing stage ------------------------------------------------------
    "PAIR_THRESHOLD": 0.45,    # min pair score before we dare suggest it
    "LINK_CATEGORY_RULE": 0.90,   # "jar"+"nutmeg" match a curated rule
    "LINK_SEEN_ADJACENT": 0.95,   # the words were literally next to each
                                  # other earlier ("sausage roll") — best
                                  # evidence there is.
    "LINK_SEEN_TOGETHER": 0.55,   # same sentence earlier, not adjacent
    "LINK_EMBEDDING_SCALE": 0.80, # spaCy similarity (0..1) * this, if avail.

    # ---- pronoun stage -------------------------------------------------------
    # pronouns almost never point further back than 2–3 sentences, so their
    # recency decay is much steeper than the general one above.
    "PRONOUN_HALF_LIFE": 1.5,
    "PRONOUN_FLOOR": 0.02,
    "PRONOUN_VERB_FRAME_BOOST": 1.5,  # "poured the nutmeg" earlier, now
                                      # "poured it" => 'it' likely = nutmeg
    "PRONOUN_SMOOTHING": 1.0,   # added to the denominator when turning
                                # scores into probabilities, so they never
                                # sum to 1 — leaves mass for "none of these"
    "PRONOUN_MAX_CANDIDATES": 3,   # rows shown per pronoun in the panel
    "PRONOUN_MIN_PROB": 0.08,      # hide hopeless guesses
}

# ============================================================================
# ║   WORD LISTS — arranged for easy extending.  Add a word, save, done.    ║
# ============================================================================

# --- closed-class words (never visualisable, used by the mini POS tagger) --
PRONOUN_WORDS = {
    "he", "him", "his", "she", "her", "hers", "it", "its",
    "they", "them", "their", "theirs", "this", "that", "these", "those",
    "i", "me", "my", "mine", "we", "us", "our", "ours", "you", "your", "yours",
    # reflexives
    "himself", "herself", "itself", "themselves", "themself",
    "myself", "yourself", "yourselves", "ourselves", "oneself",
    # indefinite / substitute / positional anaphors
    "one", "ones", "another", "other", "others", "both", "either",
    "neither", "former", "latter", "such", "same", "whoever", "whatever",
    "whichever",
    # non-referential indefinites (introduce unknowns — never resolved)
    "something", "anything", "nothing", "everything",
    "someone", "anyone", "everyone", "somebody", "anybody", "everybody",
    "nobody",
}
STOPWORDS = PRONOUN_WORDS | {
    "a", "an", "the", "and", "or", "but", "so", "if", "then", "than",
    "of", "in", "on", "at", "to", "for", "with", "from", "by", "as",
    "into", "onto", "over", "under", "down", "up", "out", "off", "about",
    "is", "am", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "will", "would", "shall",
    "should", "can", "could", "may", "might", "must",
    "not", "no", "yes", "there", "here", "when", "where", "what", "which",
    "who", "whom", "whose", "why", "how", "all", "any", "some", "each",
    "very", "just", "also", "too", "again", "once", "only", "own", "same",
    "such", "both", "few", "more", "most", "other", "another", "next",
    "now", "still", "yet", "ever", "never", "always", "while", "because",
    "across", "against", "along", "among", "around", "before", "behind",
    "below", "beneath", "beside", "besides", "between", "beyond",
    "despite", "during", "except", "inside", "outside", "near", "past",
    "since", "through", "throughout", "toward", "towards", "underneath",
    "until", "upon", "within", "without", "although", "though", "unless",
    "whether", "however", "therefore", "perhaps", "maybe", "quite",
    "rather", "really", "almost", "enough", "already", "soon", "often",
    "sometimes", "usually", "together", "away", "back", "even", "instead",
    "anyway", "indeed", "meanwhile", "finally", "suddenly", "actually",
    "like", "unlike", "via", "per", "versus",
    "ad", "bc", "ce", "bce",       # era markers: "64 AD" — never objects
    # number words — "THREE lamps hung": 'three' is a count, not an object
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "twenty", "thirty", "forty",
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "billion", "zero", "several", "few", "many",
    "much", "half",
}

# --- common verbs the mini tagger should recognise even without -ed/-ing ---
COMMON_VERBS = {
    "drive", "drives", "drove", "driven", "blame", "blamed", "blames",
    "burn", "burns", "rebuild", "rebuilt", "devour", "devoured",
    "brings", "bring", "hung", "hang", "hangs", "rose", "risen", "rise",
    "lay", "lain",
    "swam", "swum", "sang", "sung", "rang", "rung", "sank", "sunk",
    "shone", "shook", "shaken", "flew", "flown", "drew", "drawn", "blew",
    "blown", "drove", "driven", "rode", "ridden", "broke", "broken",
    "stole", "stolen", "chose", "chosen", "froze", "frozen", "tore",
    "torn", "wore", "bore", "borne", "built", "caught", "taught",
    "fought", "sought", "slept", "swept", "wept", "crept", "dealt",
    "meant", "bent", "lent", "spent", "burnt", "learnt", "spat", "bit",
    "bitten", "hid", "hidden", "slid", "struck", "stuck", "stung",
    "swung", "sprang", "sprung", "clung", "flung", "spun", "won", "shot",
    "lit", "fed", "fled", "bled", "bred", "sped", "leapt", "knelt",
    "go", "goes", "went", "gone", "get", "got", "gotten", "make", "made",
    "take", "took", "taken", "put", "puts", "give", "gave", "given",
    "come", "came", "see", "saw", "seen", "look", "looks", "find", "found",
    "think", "thought", "know", "knew", "known", "say", "said", "says",
    "tell", "told", "want", "wants", "use", "uses", "run", "ran", "sit",
    "sat", "stand", "stood", "hold", "held", "keep", "kept", "let", "begin",
    "began", "begun", "bring", "brought", "buy", "bought", "eat", "ate",
    "eaten", "drink", "drank", "drunk", "pour", "pours", "gather", "gathers",
    "throw", "threw", "thrown", "cut", "cuts", "grow", "grew", "grown",
    "fall", "fell", "fallen", "feel", "felt", "leave", "left", "meet",
    "met", "pay", "paid", "read", "write", "wrote", "written", "speak",
    "spoke", "spoken", "send", "sent", "show", "showed", "shown", "wear",
    "wore", "worn", "win", "won", "lose", "lost", "sell", "sold",
}

# --- words ending in -ing that are actually (visualisable) nouns -----------
ING_NOUNS = {
    "building", "ceiling", "morning", "evening", "painting", "drawing",
    "wedding", "clothing", "lightning", "railing", "awning", "dumpling",
    "pudding", "herring", "duckling", "sapling", "ring", "king", "wing",
    "string", "spring", "swing", "thing", "sibling", "darling", "earring",
    "stocking", "carving", "engraving", "piping",
}

# --- common adjectives with no telltale suffix ("the OLD mule") -------------
COMMON_ADJECTIVES = {
    "old", "new", "big", "small", "little", "large", "young", "long",
    "short", "high", "low", "hot", "cold", "warm", "cool", "dark",
    "light", "heavy", "dry", "wet", "clean", "dirty", "fresh", "red",
    "blue", "green", "yellow", "black", "white", "brown", "grey", "gray",
    "pink", "purple", "orange", "golden", "silver", "tall", "wide",
    "narrow", "thick", "thin", "empty", "full", "fast", "slow", "hard",
    "soft", "loud", "quiet", "deep", "shallow", "strong", "weak", "rich",
    "poor", "sharp", "dull", "smooth", "rough", "tight", "loose", "early",
    "late", "good", "bad", "fine", "nice", "pretty", "ugly", "busy",
    "tired", "angry", "happy", "sad", "great", "huge", "tiny", "broken",
    "rusty", "shiny", "sticky", "damp", "bare", "flat", "round", "square",
    "steep", "gentle", "fierce", "calm", "wild", "tame", "sour", "sweet",
    "bitter", "salty", "ripe", "raw", "whole", "half", "double", "single",
    "open", "closed", "shut", "alive", "dead", "asleep", "awake", "alone",
    "entire", "unpopular", "popular", "ancient", "modern", "holy",
    "mighty", "sacred", "royal", "hungry", "thirsty", "sleepy", "lonely",
    "lovely", "friendly", "deadly", "silly", "vast", "grand", "inner",
    "outer", "upper", "middle", "nearby", "distant", "foreign", "local",
    "native", "urban", "rural", "private", "common", "rare", "usual",
    "exact", "pure", "brief", "sudden", "mere", "sheer", "utter", "prime",
    "first", "last", "final", "main", "certain", "sure", "ready", "free",
}

# --- suffixes that mark ABSTRACT nouns (nouns you can't photograph) --------
ABSTRACT_SUFFIXES = (
    "ness", "ity", "tion", "sion", "ment", "ance", "ence",
    "ism", "ship", "hood", "dom", "ancy", "ency", "logy",
)

# --- roles/words that mean "a person" (for he/she compatibility) -----------
PERSON_WORDS = {
    "man", "woman", "boy", "girl", "person", "people", "child", "children",
    "kid", "baby", "guy", "lady", "gentleman", "chef", "cook", "doctor",
    "nurse", "teacher", "farmer", "sailor", "soldier", "king", "queen",
    "prince", "princess", "merchant", "trader", "captain", "pirate",
    "butcher", "baker", "worker", "driver", "pilot", "waiter", "waitress",
    "friend", "neighbour", "neighbor", "brother", "sister", "mother",
    "father", "mum", "mom", "dad", "uncle", "aunt", "grandma", "grandpa",
    "husband", "wife", "son", "daughter", "artist", "painter", "singer",
    "actor", "actress", "customer", "stranger", "villager", "tailor",
    # occupations & roles, continued — the wider this gazetteer, the fewer
    # people get mistaken for things ('it' -> "fiddler" is never right)
    "fiddler", "dancer", "juggler", "piper", "drummer", "musician",
    "poet", "writer", "author", "hunter", "keeper", "porter", "vicar",
    "priest", "monk", "nun", "judge", "lawyer", "clerk", "guard",
    "thief", "beggar", "miner", "shepherd", "fisherman", "blacksmith",
    "carpenter", "plumber", "barber", "grocer", "banker", "postman",
    "policeman", "fireman", "nanny", "maid", "butler", "servant",
    "visitor", "traveller", "traveler", "passenger", "tourist", "guest",
    "student", "pupil", "professor", "scientist", "engineer", "surgeon",
    "dentist", "vet", "librarian", "innkeeper", "landlord",
    "landlady", "tenant", "owner", "boss", "manager", "colleague",
    "partner", "rival", "enemy", "hero", "heroine", "witch", "wizard",
    "giant", "dwarf", "knight", "squire", "duke", "duchess", "lord",
    "twin", "toddler", "teenager", "grandmother", "grandfather",
    "cousin", "nephew", "niece", "widow", "widower", "bride", "groom",
}

# --- collective nouns: singular form, plural behaviour ("the crowd...they")
COLLECTIVE_NOUNS = {
    "crowd", "team", "family", "group", "committee", "band", "crew",
    "staff", "audience", "police", "army", "gang", "mob", "jury",
    "council", "government", "public", "herd", "flock", "swarm", "pack",
}

# --- singular words that end in 's' (so 'they' doesn't grab them) -----------
SINGULAR_S_WORDS = {
    "glass", "grass", "dress", "boss", "bus", "gas", "kiss", "class",
    "mattress", "octopus", "cactus", "chess", "brass", "moss", "walrus",
    "atlas", "canvas", "lens", "iris", "compass", "harness", "witness",
    "princess", "waitress", "actress", "mistress", "fortress", "press",
}

# ============================================================================
# ║   PAIRING KNOWLEDGE — curated category rules.                            ║
# ║   Each rule:  when a word from LEFT meets a word from RIGHT,             ║
# ║   suggest TEMPLATE.  Whitelists on purpose: "jar of nutmeg" fires        ║
# ║   because nutmeg is in JARRABLE; "jar of concrete" never fires           ║
# ║   because concrete isn't — precision over recall.                        ║
# ============================================================================

CONTAINERS = {
    "jar", "bottle", "bag", "box", "cup", "bowl", "jug", "tin", "can",
    "sack", "basket", "pot", "pan", "saucepan", "crate", "barrel",
    "bucket", "mug", "glass", "tub", "packet", "pouch", "flask",
    "carton", "tube", "vial", "urn", "chest", "trunk", "vase",
}

# things that plausibly sit INSIDE a container (spices, foods, liquids,
# small objects...) — extend freely, this is the main list to grow
JARRABLE = {
    "nutmeg", "cinnamon", "pepper", "salt", "sugar", "flour", "rice",
    "coffee", "tea", "spice", "spices", "herbs", "honey", "jam",
    "marmalade", "pickles", "olives", "sweets", "candy", "biscuits",
    "cookies", "nuts", "seeds", "grain", "oats", "beans", "lentils",
    "milk", "water", "juice", "wine", "beer", "oil", "vinegar", "sauce",
    "soup", "cream", "butter", "coins", "buttons", "marbles", "beads",
    "pins", "needles", "nails", "screws", "matches", "pencils", "letters",
    "sand", "shells", "pebbles", "flowers", "fireflies", "paint", "ink",
    "gold", "treasure", "apples", "oranges", "lemons", "eggs", "bread",
    "cheese", "fish", "worms", "soil",
}

# "pile of X", "stack of X" — the left-hand collective words
GROUP_WORDS = {
    "pile", "stack", "heap", "bunch", "row", "pair", "mound", "mountain",
    "bundle", "cluster", "collection",
}
# crowd/herd/flock/swarm gather LIVING things — pairing them with objects
# ("crowd of jar") is nonsense, so they get their own animate-only rule
ANIMATE_GROUP_WORDS = {"herd", "flock", "crowd", "swarm", "pack"}
ANIMATE_PLURALS = {
    "people", "villagers", "children", "men", "women", "sheep", "cattle",
    "cows", "goats", "horses", "dogs", "cats", "birds", "geese", "ducks",
    "chickens", "pigeons", "bees", "wasps", "flies", "ants", "fish",
    "wolves", "deer", "pigs", "gulls", "starlings", "sparrows", "crows",
}

# things you can wear (for "PERSON wearing X")
WEARABLES = {
    "hat", "cap", "coat", "jacket", "scarf", "gloves", "boots", "shoes",
    "dress", "suit", "tie", "crown", "helmet", "apron", "cloak", "uniform",
    "glasses", "spectacles", "goggles", "mask", "necklace", "ring", "watch",
}

# "slice of bread", "bar of chocolate", "sheet of paper" — portion words
PORTION_WORDS = {
    "slice", "slices", "wedge", "chunk", "loaf", "bar", "sheet", "ball",
    "deck", "stick", "lump", "knob", "sprig", "clove", "scoop", "block",
}
PORTIONABLE = {
    "bread", "cake", "pizza", "cheese", "lemon", "ham", "pie", "melon",
    "toast", "butter", "chocolate", "soap", "gold", "silver", "paper",
    "wool", "string", "cards", "wood", "ice", "clay", "dough", "garlic",
    "mint", "rosemary", "cream",
}

# Each rule: (name, left-word set, right-word set, template).
#   {L} / {R} are replaced with the matching words.
# ORDER-AGNOSTIC: the engine tries current-word-as-L with previous-as-R
# and vice versa, so "jar" can be in the current entry OR in the memory.
PAIR_RULES = [
    ("container_of", CONTAINERS,  JARRABLE,  "{L} of {R}"),
    ("portion_of",   PORTION_WORDS, PORTIONABLE, "{L} of {R}"),
    ("group_of",     GROUP_WORDS, None,      "{L} of {R}"),   # None = any
                                                              # CONCRETE noun
    ("wearing",      PERSON_WORDS, WEARABLES, "{L} wearing {R}"),
]

# --- a broad "concrete thing" vocabulary (boosts scoring; feeds group_of) --
CONCRETE_WORDS = (
    CONTAINERS | JARRABLE | WEARABLES | PERSON_WORDS | ING_NOUNS |
    {
        # household / kitchen
        "sink", "table", "chair", "sofa", "bed", "lamp", "door", "window",
        "shelf", "cupboard", "drawer", "oven", "fridge", "kettle", "spoon",
        "fork", "knife", "plate", "towel", "mirror", "clock", "carpet",
        "needle", "thread", "scissors", "book", "books", "newspaper",
        "candle", "broom", "rope", "ladder", "hammer", "nail", "brush",
        # outdoors / places
        "house", "cottage", "barn", "shed", "garden", "tree", "trees",
        "forest", "river", "lake", "sea", "beach", "mountain", "hill",
        "road", "street", "bridge", "castle", "church", "market", "shop",
        "farm", "field", "fence", "gate", "well", "harbour", "harbor",
        "ship", "boat", "car", "cars", "truck", "train", "bicycle", "bike",
        "plane", "cart", "wagon",
        # animals
        "dog", "dogs", "cat", "cats", "horse", "horses", "cow", "cows",
        "sheep", "pig", "pigs", "chicken", "chickens", "goat", "duck",
        "bird", "birds", "rabbit", "fox", "wolf", "bear", "deer", "mouse",
        # food (beyond JARRABLE)
        "sausage", "roll", "cake", "pie", "stew", "pasta", "pizza",
        "sandwich", "meat", "chicken", "potato", "potatoes", "carrot",
        "carrots", "onion", "onions", "tomato", "tomatoes", "mushroom",
        "mushrooms", "banana", "grapes",
        # misc props
        "map", "sword", "shield", "flag", "coin", "key", "keys", "lock",
        "bell", "drum", "guitar", "violin", "camera", "phone", "umbrella",
        "suitcase", "wheel", "engine", "anchor", "telescope", "compass",
        "presser", "foot", "machine", "fabric", "cloth", "wool", "cotton",
    }
)

PAIR_PARTNER_HORIZON = WEIGHTS["PAIR_PARTNER_HORIZON"]
PRONOUN_HORIZON = WEIGHTS["PRONOUN_HORIZON"]

# ============================================================================
# ║  THEMES — the piece's setting (place / culture / era).                  ║
# ║  "Rome" in a script about Rome is not an object that fades like a      ║
# ║  saucepan: it colours every later visualisable ("ROMAN temples",       ║
# ║  "ROMAN merchant stalls 64 AD").  Standard IR practice: a gazetteer    ║
# ║  + demonym map (how news search engines do query expansion).           ║
# ============================================================================

# place -> its adjectival (demonym) form, used to build theme combos
DEMONYMS = {
    "rome": "Roman", "greece": "Greek", "athens": "Athenian",
    "sparta": "Spartan", "troy": "Trojan", "egypt": "Egyptian",
    "persia": "Persian", "babylon": "Babylonian",
    "carthage": "Carthaginian", "byzantium": "Byzantine",
    "venice": "Venetian", "florence": "Florentine", "paris": "Parisian",
    "france": "French", "italy": "Italian", "spain": "Spanish",
    "germany": "German", "russia": "Russian", "england": "English",
    "britain": "British", "scotland": "Scottish", "ireland": "Irish",
    "wales": "Welsh", "america": "American", "mexico": "Mexican",
    "china": "Chinese", "japan": "Japanese", "india": "Indian",
    "korea": "Korean", "vietnam": "Vietnamese", "turkey": "Turkish",
    "arabia": "Arabian", "morocco": "Moroccan", "kenya": "Kenyan",
    "brazil": "Brazilian", "peru": "Peruvian", "cuba": "Cuban",
    "canada": "Canadian", "australia": "Australian", "norway": "Norwegian",
    "sweden": "Swedish", "denmark": "Danish", "holland": "Dutch",
    "netherlands": "Dutch", "poland": "Polish", "hungary": "Hungarian",
    "austria": "Austrian", "switzerland": "Swiss", "portugal": "Portuguese",
    "iceland": "Icelandic", "mongolia": "Mongolian", "tibet": "Tibetan",
}
# places whose own name works fine as a modifier ("London markets")
PLACE_NAMES = set(DEMONYMS) | {
    "london", "york", "oxford", "cambridge", "edinburgh", "dublin",
    "berlin", "vienna", "moscow", "madrid", "lisbon", "amsterdam",
    "prague", "istanbul", "constantinople", "jerusalem", "mecca",
    "cairo", "alexandria", "pompeii", "tokyo", "kyoto", "beijing",
    "shanghai", "delhi", "mumbai", "chicago", "boston", "texas",
    "california", "hollywood", "manhattan", "brooklyn", "sydney",
}
# adjectives that ARE themes in their own right ("medieval castles")
ERA_THEME_WORDS = {
    "medieval", "victorian", "ancient", "prehistoric", "colonial",
    "tudor", "edwardian", "georgian", "elizabethan", "renaissance",
    "baroque", "gothic", "viking", "aztec", "mayan", "incan",
    "ottoman", "byzantine", "roman", "greek", "egyptian", "celtic",
    "futuristic", "vintage", "retro",
}
# words that stand alone — theme-combining them reads wrong
# ("Roman wind"?  no: wind is wind everywhere)
THEME_IMMUNE = {
    "wind", "rain", "sky", "sun", "moon", "cloud", "clouds", "storm",
    "fire", "flames", "flame", "blaze", "smoke", "water", "snow",
    "lightning", "thunder", "sunrise", "sunset", "dawn", "dusk",
    "sea", "ocean", "waves", "fog", "mist", "stars", "rainbow", "air",
    "darkness", "light", "shadow", "shadows", "silence", "night",
}
# "the CITY was rebuilt" — a category word that anchors back to the
# theme place (mini-hypernym: Rome IS-A city)
CATEGORY_PLACE_WORDS = {"city", "town", "village", "capital", "empire",
                        "kingdom", "metropolis"}
# "a very unpopular EMPEROR ... NERO" — title words anchor to a person,
# and the template puts the title FIRST ("Emperor Nero"), so the output
# is grammatical by construction — no grammar checker needed
TITLE_WORDS = {"emperor", "empress", "king", "queen", "prince",
               "princess", "pharaoh", "tsar", "sultan", "pope", "caesar",
               "president", "general", "ruler", "chief", "chieftain"}
ERA_MARKERS = {"ad", "bc", "ce", "bce"}


# words like "texture", "colour" that introduce a COMPARISON — a noun right
# after "<these> of" is being used descriptively, not as an object in shot
COMPARISON_HEADS = {
    "texture", "consistency", "colour", "color", "shape", "size", "smell",
    "taste", "sound", "feel", "look", "weight", "density",
}

# --- GENERIC nouns: grammatically nouns, useless as search terms ------------
GENERIC_NOUNS = {
    "thing", "things", "way", "ways", "lot", "lots", "kind", "kinds",
    "sort", "sorts", "bit", "bits", "stuff", "fact", "facts", "idea",
    "ideas", "reason", "reasons", "problem", "problems", "question",
    "questions", "point", "points", "case", "cases", "part", "parts",
    "side", "sides", "matter", "issue", "issues", "type", "types",
    "example", "examples", "amount", "number", "numbers", "couple",
    "rest", "history", "future", "past", "beginning", "end", "middle",
}
# measurement units: "five MILES", "a KILOGRAM of rice" — never the visual
UNIT_WORDS = {
    # NB: no "foot/feet" (body part!), no "yard" (garden!) — only words
    # that are unambiguously measurements go in here
    "mile", "miles", "kilometre", "kilometres", "kilometer", "kilometers",
    "metre", "metres", "meter", "meters", "inch", "inches",
    "kilogram", "kilograms", "kilo", "kilos", "gram",
    "grams", "pound", "pounds", "ounce", "ounces", "tonne", "tonnes",
    "ton", "tons", "litre", "litres", "liter", "liters", "gallon",
    "gallons", "pint", "pints", "degree", "degrees", "percent", "dozen",
    "dozens",
}
GENERIC_NOUNS |= UNIT_WORDS      # same treatment everywhere
# compass directions: "the compass pointed north" — north is not an object
GENERIC_NOUNS |= {"north", "south", "east", "west", "northeast",
                  "northwest", "southeast", "southwest", "left", "right"}

# time words: real nouns, almost never the visual you want
TIME_WORDS = {
    "time", "times", "moment", "moments", "second", "seconds", "minute",
    "minutes", "hour", "hours", "day", "days", "week", "weeks", "month",
    "months", "year", "years", "today", "tomorrow", "yesterday", "while",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
}

# --- NEGATION cues: the noun that follows is explicitly NOT in the scene ---
# NB: "never" is deliberately absent — it negates VERBS, and only absence
# verbs transfer that to their object ("never SAW the ghost" => no ghost,
# but "never FORGOT the temples" => the temples are remembered, present).
NEGATION_WORDS = {"no", "without", "lacked", "lacking", "nor"}
# "not a jar", "not any coins" — 'not' negates only through a determiner
NEGATION_BRIDGE = {"a", "an", "any", "the", "some", "one", "single"}
# negating THESE verbs means their object is absent: "couldn't FIND the
# map" => no map in shot.  Negating other verbs does NOT ("didn't drop
# the jar" => the jar is right there) — that's why this is a whitelist.
ABSENCE_VERBS = {
    "find", "found", "see", "saw", "seen", "have", "has", "had", "get",
    "got", "bring", "brought", "own", "owned", "carry", "carried",
    "spot", "spotted", "notice", "noticed", "locate", "located",
    "contain", "contained", "hold", "held", "keep", "kept",
}

# --- common idioms: their nouns are figurative, not objects in shot ---------
# plain lowercase substrings, checked against the whole sentence.
# each entry: (idiom text, the noun words inside it to demote)
IDIOMS = [
    ("raining cats and dogs", ("cats", "dogs")),
    ("piece of cake", ("cake", "piece")),
    ("break the ice", ("ice",)), ("broke the ice", ("ice",)),
    ("breaking the ice", ("ice",)),
    ("spill the beans", ("beans",)), ("spilled the beans", ("beans",)),
    ("under the weather", ("weather",)),
    ("on the other hand", ("hand",)),
    ("cost an arm and a leg", ("arm", "leg")),
    ("costs an arm and a leg", ("arm", "leg")),
    ("hit the sack", ("sack",)), ("hit the hay", ("hay",)),
    ("out of the blue", ()),
    ("once in a blue moon", ("moon",)),
    ("kick the bucket", ("bucket",)), ("kicked the bucket", ("bucket",)),
    ("let the cat out of the bag", ("cat", "bag")),
    ("in hot water", ("water",)),
    ("a grain of truth", ("grain", "truth")),
    ("food for thought", ("food", "thought")),
    ("down to earth", ("earth",)),
    ("over the moon", ("moon",)),
    ("in a nutshell", ("nutshell",)),
    ("the rest is history", ("rest", "history")),
    ("rest is history", ("rest", "history")),
]

# --- pleonastic 'it' — an 'it' that refers to NOTHING ("it was raining") ----
WEATHER_VERBS = {"raining", "snowing", "drizzling", "hailing", "thundering",
                 "sleeting", "rained", "snowed", "drizzled", "hailed"}
SEEM_VERBS = {"seems", "seemed", "appears", "appeared", "turns", "turned"}
# "it was IMPORTANT to/that ..." — extraposition adjectives (curated so
# "it was next to the jar" can never be mistaken for one)
EXTRAPOSITION_ADJ = {
    "important", "clear", "obvious", "easy", "hard", "difficult",
    "impossible", "possible", "nice", "good", "best", "better", "vital",
    "crucial", "likely", "unlikely", "true", "false", "safe", "wise",
    "necessary", "essential", "surprising", "strange", "odd", "time",
}
_AUX_SKIP = {"was", "is", "be", "been", "being", "had", "has", "have",
             "will", "would", "still", "just", "really", "all"}

# --- first-name gender hints ('she' -> "Jerry" is almost never right) -------
MALE_NAMES = {
    "james", "john", "robert", "michael", "william", "david", "richard",
    "joseph", "thomas", "charles", "daniel", "matthew", "anthony", "mark",
    "donald", "steven", "paul", "andrew", "joshua", "kenneth", "kevin",
    "brian", "george", "edward", "ronald", "timothy", "jason", "jeffrey",
    "ryan", "jacob", "gary", "nicholas", "eric", "jonathan", "stephen",
    "larry", "justin", "scott", "brandon", "benjamin", "samuel", "frank",
    "gregory", "raymond", "alexander", "patrick", "jack", "dennis",
    "jerry", "tyler", "aaron", "jose", "adam", "nathan", "henry", "peter",
    "harry", "oliver", "leonard", "arthur", "albert", "fred", "vincent",
}
FEMALE_NAMES = {
    "mary", "patricia", "jennifer", "linda", "elizabeth", "barbara",
    "susan", "jessica", "sarah", "karen", "nancy", "lisa", "margaret",
    "betty", "sandra", "ashley", "dorothy", "kimberly", "emily", "donna",
    "michelle", "carol", "amanda", "melissa", "deborah", "stephanie",
    "rebecca", "laura", "sharon", "cynthia", "kathleen", "amy", "shirley",
    "angela", "helen", "anna", "brenda", "pamela", "nicole", "samantha",
    "katherine", "emma", "ruth", "christine", "catherine", "debra",
    "rachel", "carolyn", "janet", "virginia", "maria", "heather", "diane",
    "julie", "joyce", "victoria", "olivia", "kelly", "christina", "lauren",
    "joan", "evelyn", "judith", "megan", "grace", "rose", "alice", "lucy",
}

# --- POS hint word classes (used by the tagger pre-pass) --------------------
# after one of these, the next content word is a NOUN, never a verb
# ("the cut", "a swing", "his rolls")
DETERMINER_HINTS = {
    "the", "a", "an", "this", "that", "these", "those", "my", "your",
    "his", "her", "its", "their", "our", "some", "any", "no", "each",
    "every", "another", "such",
}
# after one of these, the next content word leans NOUN too ("down the sink"
# handles via determiner; "of nutmeg", "with coins")
PREPOSITION_HINTS = {
    "of", "in", "on", "at", "with", "from", "by", "into", "onto", "over",
    "under", "near", "behind", "beside", "down", "up", "through", "off",
}
# after a subject pronoun, the next content word is (almost always) the VERB
# ("he rolls", "she books a room", "they cut the rope")
SUBJECT_PRONOUNS = {"he", "she", "it", "they", "i", "we", "you", "who"}
# after an auxiliary — or a negated one like "didn't" — comes a VERB too
# ("didn't DROP", "could JUMP", "must HIDE", "did not FALL")
AUX_HINTS = {"do", "does", "did", "can", "could", "will", "would",
             "shall", "should", "may", "might", "must", "not"}

# ============================================================================
# ║   TOKENISING + a tiny heuristic POS tagger.                              ║
# ║   If spaCy is installed we quietly use it instead (better accuracy);     ║
# ║   the heuristics below are the guaranteed, dependency-free floor.        ║
# ============================================================================

# [^\W\d_] = "any unicode letter" — so piñata, café, naïve tokenize whole
# (apostrophes/hyphens joined via alternation: don't, well-worn, O'Brien).
# The second branch keeps NUMBERS: "64", "1600s", "5th" — invisible dates
# were how "In 64 AD" lost its meaning entirely.
_TOKEN_RE = re.compile(
    r"[^\W\d_](?:[^\W\d_]|['\u2019\-])*|\d+(?:st|nd|rd|th|s)?")

# unicode punctuation people paste in all the time — normalise before work
_UNICODE_FIXES = str.maketrans({
    "\u2019": "'",   # curly apostrophe  (it’s -> it's)
    "\u2018": "'",
    "\u2013": "-",   # en dash
    "\u2014": "-",   # em dash
    "\u00a0": " ",   # non-breaking space
})

# contraction suffixes we strip to reach the base word:
#   it's -> it, they're -> they, he'll -> he, jerry's -> jerry ...
_CONTRACTION_SUFFIXES = ("'s", "'re", "'ll", "'ve", "'d", "'m")


@dataclass
class Token:
    text: str            # working form  ("Nutmeg" — contractions stripped)
    lower: str           # lowercased    ("nutmeg")
    index: int           # position in the sentence (0-based)
    sentence_start: bool  # first token of A sentence (incl. after . ! ?)
    cap_ok: bool = True  # False when the whole line is SHOUTING — its
                         # capitals then say nothing about proper nouns
    negation: bool = False   # was an -n't contraction ("didn't") — an
                             # auxiliary verb, never visualisable
    is_number: bool = False  # "64", "1600s", "5th" — read by era logic
    comma_after: bool = False  # "temples, villas" — a LIST, not a compound


def tokenize(text: str) -> list[Token]:
    """Split a sentence into word tokens.  Handles curly apostrophes,
    contractions, mid-line sentence boundaries and ALL-CAPS lines."""
    text = str(text or "").translate(_UNICODE_FIXES)
    raw = list(_TOKEN_RE.finditer(text))

    # ALL-CAPS guard: if most alphabetic tokens are fully uppercase the
    # capitals carry no proper-noun information ("HE PUT IT IN A JAR")
    caps = [m.group(0).isupper() and len(m.group(0)) > 1 for m in raw]
    shouting = len(raw) >= 2 and sum(caps) / len(raw) >= 0.7

    toks: list[Token] = []
    prev_end = 0
    for i, m in enumerate(raw):
        word = m.group(0)
        is_number = word[0].isdigit()
        negation = (not is_number) and word.lower().endswith("n't")
        if not negation:
            for suf in _CONTRACTION_SUFFIXES:
                if word.lower().endswith(suf) and len(word) > len(suf) + 1:
                    word = word[: -len(suf)]
                    break
        word = word.strip("'-")
        if not word:
            prev_end = m.end()
            continue
        # a token starts a sentence if it's the first, or the gap since
        # the previous token contains a sentence-ending mark
        gap = text[prev_end: m.start()]
        starts = (i == 0) or any(c in gap for c in ".!?;\n")
        if toks and ("," in gap or ";" in gap):
            toks[-1].comma_after = True
        toks.append(Token(word, word.lower(), len(toks), starts,
                          cap_ok=not shouting, negation=negation,
                          is_number=is_number))
        prev_end = m.end()
    # trailing punctuation after the final word ("temples,")
    if toks and ("," in text[prev_end:] or ";" in text[prev_end:]):
        toks[-1].comma_after = True
    return toks


def looks_like_verb(word: str) -> bool:
    """Cheap verb detector: known verbs + -ing/-ed suffixes (with the
    ING_NOUNS exception list so 'building' stays a noun)."""
    w = word.lower()
    if w in COMMON_VERBS:
        return True
    if w.endswith("ing") and w not in ING_NOUNS and len(w) > 4:
        return True
    if w.endswith("ed") and len(w) > 3:
        return True
    return False


# ============================================================================
# ║  KNOWLEDGE BASE — layered lookups against big maintained resources.     ║
# ║                                                                          ║
# ║  Every OPEN word class (names, places, person-nouns, weather, units,    ║
# ║  concreteness ...) resolves in three layers:                            ║
# ║    1. an installed library     (gender-guesser: 48k names; WordNet)     ║
# ║    2. recommender_data.json    (built by build_wordlists.py from        ║
# ║       Brysbaert concreteness ratings + mledoze/countries demonyms)      ║
# ║    3. the small seed lists below — a documented FALLBACK only, so the   ║
# ║       engine still runs on a bare Python install.                       ║
# ║  Only CLOSED grammatical classes (era markers, absence verbs,           ║
# ║  extraposition adjectives, months ...) are hardcoded by design —        ║
# ║  those genuinely are 30-word finite sets.                               ║
# ============================================================================
from functools import lru_cache

_DATA_PATH = Path(__file__).resolve().parent / "recommender_data.json"
try:
    _DATA = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
except Exception:                                  # pragma: no cover
    _DATA = {}
_CONC: dict = _DATA.get("concreteness", {})
_DATA_DEMONYMS: dict = _DATA.get("demonyms", {})
_DATA_PLACES: set = set(_DATA.get("places", []))

try:
    import gender_guesser.detector as _gg
    _GENDER = _gg.Detector(case_sensitive=False)
except Exception:                                  # pragma: no cover
    _GENDER = None

_WN = None            # lazy WordNet handle: None = untried, False = absent


def _wn():
    global _WN
    if _WN is None:
        try:
            from nltk.corpus import wordnet as wordnet_corpus
            wordnet_corpus.synsets("test")         # force corpus load
            _WN = wordnet_corpus
        except Exception:                          # pragma: no cover
            _WN = False
    return _WN


def _wn_has_hypernym(word: str, targets: frozenset,
                     n_senses: int, pos=None) -> bool:
    wn = _wn()
    if not wn:
        return False
    try:
        synsets = wn.synsets(word, pos=pos or wn.NOUN)[:n_senses]
        for s in synsets:
            for path in s.hypernym_paths():
                if any(h.name() in targets for h in path):
                    return True
    except Exception:                              # pragma: no cover
        pass
    return False


@lru_cache(maxsize=50000)
def kb_gender(first_name: str) -> str | None:
    """'male' / 'female' / None — 48k international names when
    gender-guesser is installed (Greek, Slavic, Arabic, Asian ... names
    included), seed lists otherwise.  None = unknown = stays open."""
    w = first_name.lower()
    if _GENDER is not None:
        g = _GENDER.get_gender(w)
        if g in ("male", "mostly_male"):
            return "male"
        if g in ("female", "mostly_female"):
            return "female"
        return None
    if w in MALE_NAMES:
        return "male"
    if w in FEMALE_NAMES:
        return "female"
    return None


@lru_cache(maxsize=50000)
def kb_is_person(word: str) -> bool:
    """prophet, centurion, oracle, fiddler ... — WordNet's person
    hypernym over the common senses, seeds as fallback."""
    w = word.lower()
    if w in PERSON_WORDS:
        return True
    # FIRST sense only: "machine" has a person sense ("he's a machine")
    # buried at sense 4 — precision matters more than recall here
    return _wn_has_hypernym(w, frozenset({"person.n.01"}), 1)


@lru_cache(maxsize=50000)
def kb_is_collective(word: str) -> bool:
    """crowd, sect, congregation, jury — groups that take 'they'."""
    w = word.lower()
    if w in COLLECTIVE_NOUNS:
        return True
    return _wn_has_hypernym(
        w, frozenset({"social_group.n.01", "gathering.n.01"}), 1)


@lru_cache(maxsize=50000)
def kb_is_natural(word: str) -> bool:
    """wind, drizzle, aurora — phenomena that are the same everywhere,
    so they never take a theme ("Roman wind" is nonsense)."""
    w = word.lower()
    if w in THEME_IMMUNE:
        return True
    return _wn_has_hypernym(
        w, frozenset({"natural_phenomenon.n.01",
                      "atmospheric_phenomenon.n.01", "weather.n.01"}), 3)


@lru_cache(maxsize=50000)
def kb_is_unit(word: str) -> bool:
    """furlong, hectare — FIRST sense only: 'foot' must stay a body
    part, so this check needs precision, not recall."""
    w = word.lower()
    if w in UNIT_WORDS:
        return True
    return _wn_has_hypernym(
        w, frozenset({"unit_of_measurement.n.01"}), 1)


@lru_cache(maxsize=50000)
def kb_is_time(word: str) -> bool:
    """fortnight, eon — first sense only, same precision reasoning."""
    w = word.lower()
    if w in TIME_WORDS:
        return True
    return _wn_has_hypernym(
        w, frozenset({"time_period.n.01", "time_unit.n.01"}), 1)


@lru_cache(maxsize=50000)
def kb_is_place(word: str) -> bool:
    """250 countries + capitals + alt spellings from mledoze/countries,
    the curated ancient/city seeds, then WordNet's location hypernym."""
    w = word.lower()
    if w in _DATA_PLACES or w in PLACE_NAMES:
        return True
    return _wn_has_hypernym(
        w, frozenset({"geographical_area.n.01", "district.n.01",
                      "urban_area.n.01"}), 2)


@lru_cache(maxsize=50000)
def kb_demonym(place: str) -> str | None:
    """Italy -> Italian (814 mapped names from mledoze/countries), the
    curated ancient-world seeds (Rome -> Roman: city adjectivals have no
    maintained open dataset), else None — and the CALLER then uses the
    place name itself as the modifier ("London markets", "Kyoto
    temples"), which is grammatical for any place on earth."""
    w = place.lower()
    if w in DEMONYMS:
        return DEMONYMS[w]
    d = _DATA_DEMONYMS.get(w)
    return d if d else None


@lru_cache(maxsize=50000)
def kb_concreteness(word: str) -> float | None:
    """Brysbaert et al. (2014) human rating, 1 (abstract) .. 5
    (concrete), ~37k words.  None = word not rated."""
    return _CONC.get(word.lower())


@lru_cache(maxsize=50000)
def kb_verb_only(word: str) -> bool:
    """WordNet says this word can be a verb and NOTHING nominal —
    catches irregular verbs no suffix rule ever will."""
    wn = _wn()
    if not wn:
        return False
    try:
        w = word.lower()
        return bool(wn.synsets(w, pos=wn.VERB)) \
            and not wn.synsets(w, pos=wn.NOUN) \
            and not wn.synsets(w, pos=wn.ADJ)
    except Exception:                              # pragma: no cover
        return False


@lru_cache(maxsize=50000)
def kb_adj_only(word: str) -> bool:
    """Adjective senses and nothing nominal ('ornate', 'devout')."""
    wn = _wn()
    if not wn:
        return False
    try:
        w = word.lower()
        return bool(wn.synsets(w, pos=wn.ADJ)) \
            and not wn.synsets(w, pos=wn.NOUN) \
            and not wn.synsets(w, pos=wn.VERB)
    except Exception:                              # pragma: no cover
        return False


def looks_abstract(word: str) -> bool:
    """Can you photograph it?  Brysbaert human ratings (37k words) when
    the data file is present; abstract suffixes as the fallback."""
    w = word.lower()
    rating = kb_concreteness(w)
    if rating is not None:
        return rating <= 3.0     # Brysbaert scale midpoint: 1=abstract..5
    return any(w.endswith(s) for s in ABSTRACT_SUFFIXES)


def classify_token(tok: Token, seen_lowercase: set[str],
                   hint: str | None = None) -> str:
    """
    Return one of: 'PROPER_NOUN' | 'NOUN' | 'VERB' | 'ADJECTIVE' | 'OTHER'.

    Proper-noun rule: capitalised, AND either mid-sentence or (at sentence
    start) not a word we've ever seen written lowercase and not a common
    word — so "Nutmeg is nice" at line start doesn't fake a name, but
    "Leonard Nimoy said..." does get caught.

    `hint` comes from the pre-pass:
      'noun' — a determiner/preposition sits right before this word, so it
               cannot be a verb ("the cut", "a swing", "of nutmeg")
      'verb' — a subject pronoun sits right before it, so it almost
               certainly IS the verb ("he rolls", "she books a room")
    """
    if tok.lower in STOPWORDS or tok.negation or tok.is_number:
        return "OTHER"
    if tok.cap_ok and tok.text[0].isupper():
        known_common = (tok.lower in seen_lowercase
                        or tok.lower in COMMON_VERBS
                        or tok.lower in CONCRETE_WORDS
                        or tok.lower in STOPWORDS)
        if not tok.sentence_start:
            return "PROPER_NOUN"
        if not known_common:
            return "PROPER_NOUN"
    if hint == "verb":
        return "VERB"
    if hint != "noun" and looks_like_verb(tok.text):
        return "VERB"
    w = tok.lower
    if w in COMMON_ADJECTIVES:
        return "ADJECTIVE"
    if w.endswith(("ous", "ful", "ive", "able", "ible", "ish")):
        return "ADJECTIVE"
    if kb_verb_only(w):        # "smite", "strive" — WordNet: verb only
        return "VERB"
    if kb_adj_only(w):         # "devout", "ornate" — adjective only
        return "ADJECTIVE"
    # default content word => noun. Visualisables are overwhelmingly nouns,
    # so this default errs the right way.
    return "NOUN"


def _pos_hints(tokens: list[Token]) -> list[str | None]:
    """The context pre-pass: for each token, what does the word BEFORE it
    tell us?  Determiners/prepositions promise a noun; subject pronouns
    promise a verb.  This is the single biggest accuracy lever a tagger
    without a full parser has."""
    hints: list[str | None] = [None] * len(tokens)
    for i in range(1, len(tokens)):
        prev = tokens[i - 1]
        if prev.lower in DETERMINER_HINTS or prev.lower in PREPOSITION_HINTS:
            hints[i] = "noun"
        elif prev.lower in SUBJECT_PRONOUNS \
                and not tokens[i].text[0].isupper():
            hints[i] = "verb"
        elif (prev.lower in AUX_HINTS or prev.negation) \
                and not tokens[i].text[0].isupper():
            # "didn't DROP the jar", "could JUMP" — a verb follows
            hints[i] = "verb"
    return hints


# lowercase words allowed INSIDE a name run: "Statue of Liberty",
# "Jack of Hearts", "Vincent van Gogh"
_NAME_CONNECTORS = {"of", "the", "de", "la", "van", "von", "der"}


def merge_proper_runs(tokens: list[Token], seen_lowercase: set[str]
                      ) -> list[tuple[str, str, int]]:
    """
    Turn tokens into (surface, pos, index) triples, merging consecutive
    capitalised words into one proper noun ("Leonard" + "Nimoy" ->
    "Leonard Nimoy", "Statue" + "of" + "Liberty" -> "Statue of Liberty").
    index = index of the FIRST token of the phrase.
    """
    out: list[tuple[str, str, int]] = []
    hints = _pos_hints(tokens)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        pos = classify_token(tok, seen_lowercase, hints[i])
        if pos == "PROPER_NOUN":
            j = i + 1
            parts = [tok.text]
            while j < len(tokens):
                nxt = tokens[j]
                if nxt.sentence_start:
                    break     # "Maximus. Driven by" — a name never crosses
                              # a full stop into the next sentence
                if (nxt.cap_ok and nxt.text[0].isupper()
                        and nxt.lower not in STOPWORDS
                        and not nxt.is_number):
                    parts.append(nxt.text)
                    j += 1
                elif (nxt.lower in _NAME_CONNECTORS and j + 1 < len(tokens)
                        and tokens[j + 1].cap_ok
                        and tokens[j + 1].text[0].isupper()):
                    parts.append(nxt.lower)
                    parts.append(tokens[j + 1].text)
                    j += 2
                else:
                    break
            out.append((" ".join(parts), "PROPER_NOUN", i))
            i = j
        else:
            out.append((tok.text, pos, i))
            i += 1
    return out


def noun_compounds(triples: list[tuple[str, str, int]],
                   tokens: list[Token] | None = None
                   ) -> list[tuple[str, str, int]]:
    """
    Adjacent NOUN+NOUN => one compound visualisable: "presser foot",
    "sewing machine", "kitchen sink".  Generic/time nouns never join a
    compound ("thing table" is noise).  Returns the extra compound triples
    (index = first word's index); the individual words stay available too.
    """
    extra = []
    used: set[int] = set()          # greedy left-to-right, no overlaps:
    for (s1, p1, i1), (s2, p2, i2) in zip(triples, triples[1:]):
        if i1 in used or i2 in used:
            continue                # "sewing machine needle case" =>
        if p1 == "NOUN" and p2 == "NOUN" and i2 == i1 + 1:
            if tokens and i1 < len(tokens) and tokens[i1].comma_after:
                continue    # "temples, villas" is a LIST, not one object
            a, b = s1.lower(), s2.lower()
            # the MODIFIER must be specific ("thing table" is noise), but
            # a generic HEAD is fine once modified ("needle case")
            if (a not in GENERIC_NOUNS and a not in TIME_WORDS
                    and b not in TIME_WORDS):
                extra.append((f"{s1} {s2}", "NOUN", i1))
                used.update((i1, i2))   # "sewing machine"+"needle case"
    return extra


# adjectives too bland to keep in a chunk — sizes, degrees, ordinals.
# "a SMALL RELIGIOUS sect" keeps "religious" (distinctive: robes, rituals)
# and drops "small" (every noun can be small)
_CHUNK_SKIP_ADJS = {
    "small", "big", "large", "little", "tiny", "huge", "vast", "great",
    "enormous", "massive", "giant", "mere", "sheer", "utter", "whole",
    "entire", "certain", "main", "prime", "own", "same", "such", "very",
    "first", "last", "final", "single", "double", "half", "other",
    "more", "most", "many", "much", "few", "several", "good", "bad",
    "fine", "nice", "new", "sure", "ready", "usual", "exact", "brief",
    "sudden", "busy", "common", "rare", "likely", "unlikely", "possible",
}


def adj_noun_chunks(triples: list[tuple[str, str, int]],
                    tokens: list[Token],
                    taken: set[int] | None = None
                    ) -> list[tuple[str, str, int]]:
    """
    Distinctive ADJECTIVE + NOUN => one phrase: "religious sect",
    "wooden cart", "golden mask".  The adjective is a piece of knowledge
    that changes what the thing LOOKS like — exactly what a search term
    wants.  Size/degree adjectives are skipped (_CHUNK_SKIP_ADJS), and
    nouns already inside a noun-compound are left alone.
    """
    taken = taken or set()
    by_index = {tidx: (s, p) for s, p, tidx in triples}
    out = []
    for surface, pos, tidx in triples:
        if pos != "NOUN" or tidx in taken:
            continue
        adjs: list[str] = []
        j = tidx - 1
        while j >= 0 and len(adjs) < 2:
            prev = by_index.get(j)
            if prev is None or prev[1] != "ADJECTIVE":
                break
            if j < len(tokens) and tokens[j].comma_after:
                break                      # "red, boots" is a list
            if prev[0].lower() in _CHUNK_SKIP_ADJS:
                break
            adjs.insert(0, prev[0])
            j -= 1
        if adjs:
            out.append((" ".join(adjs + [surface]), "NOUN", j + 1))
    return out


def _negation_spans(tokens: list[Token]) -> set[int]:
    """Token indices whose noun is explicitly ABSENT: "no jar",
    "without a map", "not a single coin"."""
    flagged: set[int] = set()
    lows = [t.lower for t in tokens]
    for i, w in enumerate(lows):
        if w in NEGATION_WORDS:
            # negation reaches through determiners/adjectives: window of 3
            flagged.update(range(i + 1, min(i + 4, len(lows))))
        elif w == "not":
            j = i + 1
            while j < len(lows) and lows[j] in NEGATION_BRIDGE:
                j += 1
            if j > i + 1:                       # "not a/any/the ..."
                flagged.update(range(j, min(j + 2, len(lows))))
        # "couldn't/didn't/never + <absence verb>" — the OBJECT is absent
        if tokens[i].negation or w in ("never", "cannot"):
            for j in range(i + 1, min(i + 3, len(lows))):
                if lows[j] in ABSENCE_VERBS:
                    flagged.update(range(j + 1, min(j + 5, len(lows))))
                    break
    return flagged


def _idiom_spans(tokens: list[Token]) -> set[int]:
    """Token indices sitting inside a known idiom — their nouns are
    figurative ("raining cats and dogs" has no cats and no dogs)."""
    flagged: set[int] = set()
    seq = [t.lower for t in tokens]
    joined = " ".join(seq)
    for phrase, _demote in IDIOMS:
        if phrase in joined:
            words = phrase.split()
            for start in range(len(seq) - len(words) + 1):
                if seq[start: start + len(words)] == words:
                    flagged.update(range(start, start + len(words)))
    return flagged


# ============================================================================
# ║   OPTIONAL spaCy upgrade (auto-detected, never required)                 ║
# ============================================================================
_NLP = None


def _try_load_spacy():
    """Load a spaCy pipeline with word vectors, if one is installed."""
    global _NLP
    if _NLP is not None:
        return _NLP
    try:
        import spacy  # type: ignore
        for model in ("en_core_web_md", "en_core_web_lg", "en_core_web_sm"):
            try:
                _NLP = spacy.load(model)
                break
            except Exception:
                continue
    except Exception:
        _NLP = False   # marker: tried, unavailable
    return _NLP


def embedding_link(word_a: str, word_b: str) -> float:
    """0..1 relatedness from spaCy vectors, or 0.0 when unavailable."""
    nlp = _try_load_spacy()
    if not nlp:
        return 0.0
    try:
        da, db = nlp(word_a), nlp(word_b)
        if not da.has_vector or not db.has_vector:
            return 0.0
        return max(0.0, float(da.similarity(db)))
    except Exception:
        return 0.0


# ============================================================================
# ║   STAGE 1 — the memory: mentions + continuous scoring                    ║
# ============================================================================

@dataclass
class Mention:
    entry_index: int     # which line/entry it appeared in
    pos: str             # POS at that appearance
    token_index: int     # where in the sentence
    in_simile: bool      # inside "texture of X" / "like a X" / "as X as"?
    negated: bool = False    # "no jar" / "without a map" — absent from scene
    in_idiom: bool = False   # "raining cats and dogs" — figurative
    is_subject: bool = False  # first noun of its sentence (what it's ABOUT)


@dataclass
class VisualisableRecord:
    """Everything we remember about one visualisable word/phrase.
    Derived facts (best POS, last entry, ...) are maintained incrementally
    by add_mention() — never rescanned — so scoring stays fast even on
    long scripts where a word has hundreds of mentions."""
    surface: str                       # nicest-cased form seen
    key: str                           # lowercase lookup key
    mentions: list[Mention] = field(default_factory=list)
    confirmed: bool = False            # the user tagged/searched it before
    _pos_seen: set = field(default_factory=set)
    _last_entry: int = -1
    _all_simile: bool = True
    _all_negated: bool = True
    _all_idiom: bool = True
    _entries_seen: set = field(default_factory=set)
    _last_is_subject: bool = False
    _abstract: bool | None = None

    _POS_ORDER = ("PROPER_NOUN", "NOUN", "ADJECTIVE", "VERB", "OTHER")

    def add_mention(self, m: Mention) -> None:
        self.mentions.append(m)
        self._pos_seen.add(m.pos)
        self._entries_seen.add(m.entry_index)
        if m.entry_index >= self._last_entry:
            self._last_entry = m.entry_index
            self._last_is_subject = m.is_subject
        self._all_simile = self._all_simile and m.in_simile
        self._all_negated = self._all_negated and m.negated
        self._all_idiom = self._all_idiom and m.in_idiom
        if self._abstract is None:
            self._abstract = looks_abstract(self.key)

    @property
    def count(self) -> int:
        return len(self.mentions)

    @property
    def is_proper(self) -> bool:
        return "PROPER_NOUN" in self._pos_seen

    @property
    def all_simile(self) -> bool:
        return bool(self.mentions) and self._all_simile

    @property
    def all_negated(self) -> bool:
        return bool(self.mentions) and self._all_negated

    @property
    def all_idiom(self) -> bool:
        return bool(self.mentions) and self._all_idiom

    @property
    def last_is_subject(self) -> bool:
        return self._last_is_subject

    @property
    def entry_spread(self) -> int:
        """How many DISTINCT entries mention this — themes recur."""
        return len(self._entries_seen)

    def first_entry(self) -> int:
        return self.mentions[0].entry_index if self.mentions else -1

    @property
    def is_abstract(self) -> bool:
        return bool(self._abstract)

    def best_pos(self) -> str:
        """The most favourable POS this word has been seen as."""
        for p in self._POS_ORDER:
            if p in self._pos_seen:
                return p
        return "OTHER"

    def last_entry(self) -> int:
        return self._last_entry


def recency_multiplier(distance: int,
                       half_life: float | None = None,
                       floor: float | None = None) -> float:
    """
    distance 0 = the current entry itself, 1 = the previous sentence, ...
    Exponential half-life decay with a floor, exactly per the spec:
    previous sentence ≈ 1.0; 15 back is small BUT NOT ZERO.
    """
    if half_life is None:
        half_life = WEIGHTS["RECENCY_HALF_LIFE"]
    if floor is None:
        floor = WEIGHTS["RECENCY_FLOOR"]
    if distance <= 1:
        return 1.0
    return max(floor, 0.5 ** ((distance - 1) / half_life))


def _simile_spans(tokens: list[Token]) -> set[int]:
    """
    Token indices that sit inside a comparison frame:
        "texture of X"    (COMPARISON_HEADS + 'of' + X)
        "like a X" / "like X"
        "as X as Y"    -> Y is the comparison
    Words there are descriptions, not objects in the scene.
    """
    flagged: set[int] = set()
    lows = [t.lower for t in tokens]
    for i, w in enumerate(lows):
        if w in COMPARISON_HEADS and i + 2 < len(lows) and lows[i + 1] == "of":
            flagged.update(range(i + 2, min(i + 4, len(lows))))
        if w == "like":
            flagged.update(range(i + 1, min(i + 4, len(lows))))
        if w == "as" and i + 3 < len(lows) and lows[i + 2] == "as":
            flagged.update(range(i + 3, min(i + 5, len(lows))))
    return flagged


class Recommender:
    """
    Feed it entries in order with observe_entry(); ask it questions any
    time.  Or skip the object entirely and call suggestions_for_line()
    at the bottom of this file (stateless wrapper — easiest for the
    tagger's request/response handler).
    """

    def __init__(self) -> None:
        self.records: dict[str, VisualisableRecord] = {}
        self.entries: list[list[Token]] = []          # tokenised history
        self.seen_lowercase: set[str] = set()         # for proper-noun test
        self.adjacent_pairs: set[tuple[str, str]] = set()   # "sausage roll"
        self.cooccur_pairs: set[frozenset] = set()          # same sentence
        self.verb_object: set[tuple[str, str]] = set()      # (pour, nutmeg)
        self._score_cache: dict[tuple, float] = {}           # perf only
        # cross-fragment carry: real transcripts are caption FRAGMENTS
        # ("the Circus" / "Maximus. Driven by") — each entry is analysed
        # with the tail of the previous one so names, adjacency and
        # sentence structure survive the cuts.
        self._carry: list[Token] = []
        self._carry_open: bool = False    # prev entry ended mid-sentence?
        self.eras: list[tuple[int, str]] = []   # (entry, "64 AD"/"1600s")
        # carry-aware per-entry extraction: what THIS fragment shows,
        # with names merged across cuts ("Circus Maximus", not "Maximus")
        self.entry_visualisables: dict[int, list[str]] = {}

    # ------------------------------------------------------------------ feed
    def observe_entry(self, index: int, text: str,
                      confirmed_term: str | None = None) -> None:
        """
        Register one transcript line.  `index` is its position (0-based);
        `confirmed_term` is the search term the user has already saved for
        this line, if any (counts as a human thumbs-up for that word).
        """
        toks = tokenize(text)
        # remember lowercase sightings BEFORE classifying, so a word the
        # same sentence writes lowercase can't be a proper noun at its start
        for t in toks:
            if t.text.islower():
                self.seen_lowercase.add(t.lower)

        while len(self.entries) <= index:
            self.entries.append([])
        self.entries[index] = toks

        # ---- cross-fragment context ------------------------------------
        # If the previous entry ended mid-sentence, this entry CONTINUES
        # it: analyse (carry-tail + current) as one stream so "the Circus"
        # + "Maximus. Driven by" yields "Circus Maximus" and adjacency
        # learns across the cut.  Mentions are only recorded for the
        # current entry's own tokens.
        carry = list(self._carry) if (self._carry_open and self._carry
                                      and toks) else []
        k = len(carry)
        if k:
            toks[0].sentence_start = False      # the sentence carries on
        analysis = [replace(t, index=i)
                    for i, t in enumerate(carry + toks)]

        simile_idx = _simile_spans(analysis)
        negated_idx = _negation_spans(analysis)
        idiom_idx = _idiom_spans(analysis)
        triples = merge_proper_runs(analysis, self.seen_lowercase)
        # compound nouns ("presser foot") become records of their own —
        # multiword phrases make better search terms than either half
        compounds = noun_compounds(triples, analysis)
        _comp_taken = set()
        for _, _, _ct in compounds:
            _comp_taken.update((_ct, _ct + 1))
        compounds = compounds + adj_noun_chunks(triples, analysis,
                                                _comp_taken)

        # the SUBJECT: the first noun/proper after a sentence start that
        # falls inside THIS entry — a fragment continuing an old sentence
        # introduces no new subject (centering)
        first_start = next((t.index for t in analysis[k:]
                            if t.sentence_start), None)
        subject_idx = None
        if first_start is not None:
            subject_idx = next(
                (tidx for _, pos, tidx in triples
                 if tidx >= first_start and pos in ("NOUN", "PROPER_NOUN")),
                None)

        def _in_current(surface: str, tidx: int) -> bool:
            span_end = tidx + len(surface.split())
            return span_end > k          # any part inside this entry

        content = []   # (surface, pos, ANALYSIS token_index)
        for surface, pos, tidx in triples:
            if surface.lower() in STOPWORDS or pos == "OTHER":
                continue
            content.append((surface, pos, tidx))
            if _in_current(surface, tidx):
                self._record_mention(surface, pos, max(0, tidx - k), index,
                                     simile_idx, negated_idx, idiom_idx,
                                     subject_idx, flag_tidx=tidx)
        # compounds are records only — they must not join the adjacency /
        # co-occurrence learning below (their parts already do)
        for surface, pos, tidx in compounds:
            if _in_current(surface, tidx):
                self._record_mention(surface, pos, max(0, tidx - k), index,
                                     simile_idx, negated_idx, idiom_idx,
                                     subject_idx, flag_tidx=tidx)

        # ---- era capture: "64 AD", "1600s", "1956", "19th century" ------
        for a, b in zip(analysis, analysis[1:]):
            if a.is_number and b.lower in ERA_MARKERS:
                self.eras.append((index, f"{a.text} {b.text.upper()}"))
            elif a.lower in ERA_MARKERS and b.is_number:
                self.eras.append((index, f"{b.text} {a.text.upper()}"))
            elif a.is_number and b.lower in ("century", "centuries"):
                self.eras.append((index, f"{a.text} century"))
        for t in analysis:
            if t.is_number and t.lower.endswith("s") and len(t.lower) == 5:
                self.eras.append((index, t.text))          # "1600s"
            elif t.is_number and len(t.lower) == 4 \
                    and t.lower.isdigit() and 1000 <= int(t.lower) <= 2030:
                self.eras.append((index, t.text))          # bare year

        # ---- carry-aware extraction for THIS entry ----------------------
        # (what extract_visualisables would say if it could see the carry:
        #  merged names, compounds swallowing their parts, and idiom /
        #  negated / generic words dropped)
        in_compound: set[int] = set()
        for s, _, tidx in compounds:
            in_compound.update(range(tidx, tidx + len(s.split())))
        shown: list[str] = []
        for surface, pos, tidx in sorted(
                triples + compounds, key=lambda t: (t[2], -len(t[0]))):
            low = surface.lower()
            if pos not in ("NOUN", "PROPER_NOUN") or low in STOPWORDS:
                continue
            if not _in_current(surface, tidx):
                continue
            if " " not in surface and (low in GENERIC_NOUNS
                                       or low in TIME_WORDS
                                       or kb_is_unit(low)
                                       or kb_is_time(low)):
                continue      # bare generic/unit/time word; compounds
                              # with a generic HEAD ("needle case") stay
            if tidx in idiom_idx or tidx in negated_idx:
                continue          # "the rest is HISTORY" shows nothing
            if " " not in surface and tidx in in_compound:
                continue          # its compound represents it
            if surface not in shown:
                shown.append(surface)
        self.entry_visualisables[index] = shown

        # update the carry for the next fragment
        if toks:
            self._carry = toks[-8:]
            tail = text.rstrip().rstrip("'\"\u2019)")
            self._carry_open = not tail.endswith((".", "!", "?", ";", ":"))
        elif any(c in (text or "") for c in ".!?;"):
            self._carry, self._carry_open = [], False

        # ---- learn pairing evidence from this sentence ----
        for (s1, p1, i1), (s2, p2, i2) in zip(content, content[1:]):
            if i2 == i1 + 1 and p1 != "VERB" and p2 != "VERB" \
                    and not analysis[i1].comma_after:
                self.adjacent_pairs.add((s1.lower(), s2.lower()))
        keys = [s.lower() for s, p, _ in content if p != "VERB"]
        for a in keys:
            for b in keys:
                if a < b:
                    self.cooccur_pairs.add(frozenset((a, b)))
        # (verb, following-noun) frames within a 3-token window
        for surface, pos, tidx in content:
            if pos == "VERB":
                for s2, p2, t2 in content:
                    if p2 in ("NOUN", "PROPER_NOUN") and 0 < t2 - tidx <= 3:
                        self.verb_object.add((surface.lower(), s2.lower()))

        if confirmed_term:
            self.confirm(confirmed_term)

    def confirm(self, term: str) -> None:
        """Mark a term the user actually used — big trust boost."""
        rec = self.records.get(term.lower())
        if rec is None:
            rec = self._record_for(term)
        rec.confirmed = True

    def _record_for(self, surface: str) -> VisualisableRecord:
        key = surface.lower()
        rec = self.records.get(key)
        if rec is None:
            rec = VisualisableRecord(surface=surface, key=key)
            self.records[key] = rec
        return rec

    def _record_mention(self, surface: str, pos: str, tidx: int,
                        entry_index: int, simile_idx: set[int],
                        negated_idx: set[int], idiom_idx: set[int],
                        subject_idx: int | None,
                        flag_tidx: int | None = None) -> None:
        # tidx = position within the entry (stored); flag_tidx = position
        # within the carry-extended analysis stream (flag lookups)
        ft = tidx if flag_tidx is None else flag_tidx
        rec = self._record_for(surface)
        rec.add_mention(Mention(
            entry_index, pos, tidx,
            in_simile=ft in simile_idx,
            negated=ft in negated_idx,
            in_idiom=ft in idiom_idx,
            is_subject=ft == subject_idx))
        if pos == "PROPER_NOUN":
            rec.surface = surface         # keep the capitalised form

    # ----------------------------------------------------------------- score
    def score(self, key: str, current_index: int) -> float:
        """
        THE score: "how much do we want to recommend this word right now?"
        Every rule from the spec (and the extras) applies here, in the
        order they're listed in WEIGHTS.
        """
        rec = self.records.get(key.lower())
        if rec is None:
            return 0.0
        # memoised per (word, line, state) — pair_suggestions alone asks
        # for the same scores thousands of times on long scripts
        ck = (rec.key, current_index, rec.count, rec.confirmed)
        cached = self._score_cache.get(ck)
        if cached is not None:
            return cached

        # 1) part of speech — proper noun >> noun >> adjective >> verb
        base = WEIGHTS[rec.best_pos()]

        # 2)+5) recency-weighted mentions: each mention adds its own decayed
        # term, so frequency and closeness are rewarded together.
        # PERF: beyond a certain distance the decay curve sits ON the floor,
        # so those mentions each contribute exactly FLOOR — count them
        # instead of iterating them (keeps long scripts O(recent) per word).
        floor = WEIGHTS["RECENCY_FLOOR"]
        cutoff = 1 + WEIGHTS["RECENCY_HALF_LIFE"] * math.log2(1.0 / floor)
        mention_sum = 0.0
        n = len(rec.mentions)
        for i in range(n - 1, -1, -1):            # newest first
            d = max(0, current_index - rec.mentions[i].entry_index)
            if d > cutoff:
                mention_sum += floor * (i + 1)    # mentions[0..i]: all older
                break
            mention_sum += recency_multiplier(d)
        score = base * mention_sum

        # 2b) ...plus the explicit frequency bonus per doubling
        if rec.count > 1:
            score *= 1.0 + WEIGHTS["FREQUENCY_BONUS"] * math.log2(rec.count)

        # extras — each one obvious and independent:
        if rec.confirmed:
            score *= WEIGHTS["CONFIRMED_BOOST"]
        if " " in rec.surface:
            score *= WEIGHTS["MULTIWORD_BOOST"]
        conc = kb_concreteness(rec.key)
        is_generic = (rec.key in GENERIC_NOUNS or rec.key in TIME_WORDS
                      or kb_is_unit(rec.key) or kb_is_time(rec.key))
        if not is_generic and not rec.all_simile \
                and (rec.key in CONCRETE_WORDS or rec.is_proper
                     or (conc is not None and conc >= 4.0)):
            # "miles" rates concrete but is still a useless search term —
            # generic words never earn the concreteness boost
            score *= WEIGHTS["CONCRETE_BOOST"]
        if not rec.is_proper and (rec.is_abstract
                                  or rec.key in COMPARISON_HEADS):
            # -ness/-tion nouns AND comparison heads ("texture", "colour")
            # are nouns you can't photograph
            score *= WEIGHTS["ABSTRACT_PENALTY"]
        if rec.all_simile:
            # ONLY ever seen inside comparisons ("texture of concrete")
            score *= WEIGHTS["SIMILE_PENALTY"]
        if rec.all_negated:
            # ONLY ever seen negated ("there was no jar") — not in shot
            score *= WEIGHTS["NEGATION_PENALTY"]
        if rec.all_idiom:
            # ONLY ever seen inside idioms ("raining cats and dogs")
            score *= WEIGHTS["IDIOM_PENALTY"]
        if is_generic:
            # "thing", "furlong", "fortnight" — real nouns, useless terms
            score *= WEIGHTS["GENERIC_PENALTY"]
        if self._is_theme_record(rec, current_index):
            # the SETTING of the piece ("Rome") doesn't fade like an
            # object: hold its recency at a high floor so it stays in
            # reach for combos and tabs all script long
            base_here = WEIGHTS[rec.best_pos()]
            floor_score = base_here * rec.count \
                * WEIGHTS["THEME_RECENCY_FLOOR"]
            score = max(score, floor_score)

        self._score_cache[ck] = score
        return score

    # ------------------------------------------------------------------
    # THEMES
    # ------------------------------------------------------------------
    def current_themes(self, current_index: int) -> list[tuple[str, float]]:
        """The piece's setting: [(adjective, strength), ...] strongest
        first.  A theme is a known place or era word that either opens
        the piece or recurs — and unlike objects, it does NOT decay."""
        out = []
        for key, rec in self.records.items():
            head = key.split()[0]
            is_place = kb_is_place(head) and rec.is_proper
            is_era_word = head in ERA_THEME_WORDS
            if not (is_place or is_era_word):
                continue
            first = rec.first_entry()
            if first > current_index:
                continue
            spread = sum(1 for e in rec._entries_seen
                         if e <= current_index)
            strength = spread + (2.0 if first <= 2 else 0.0)
            if strength < WEIGHTS["THEME_MIN_STRENGTH"]:
                continue
            if is_era_word:
                adj = head.capitalize()
            else:
                # demonym when known (Italy -> Italian, Rome -> Roman);
                # otherwise the place name itself IS the modifier —
                # "Kyoto temples", "London markets" — grammatical for
                # any place on earth, listed or not
                adj = kb_demonym(head) or rec.surface.split()[0]
            out.append((adj, strength))
        out.sort(key=lambda t: -t[1])
        return out[:2]

    def _is_theme_record(self, rec: VisualisableRecord,
                         current_index: int) -> bool:
        head = rec.key.split()[0]
        if not ((kb_is_place(head) and rec.is_proper)
                or head in ERA_THEME_WORDS):
            return False
        first = rec.first_entry()
        spread = sum(1 for e in rec._entries_seen if e <= current_index)
        return 0 <= first <= current_index and \
            spread + (2.0 if first <= 2 else 0.0) \
            >= WEIGHTS["THEME_MIN_STRENGTH"]

    def current_era(self, current_index: int) -> str | None:
        """The era string worth appending to searches — old periods only
        ("roman merchant stalls 64 AD"); anything modern is left off."""
        best = None
        for entry, era in self.eras:
            if entry <= current_index:
                best = era                      # most recent mention wins
        if best is None:
            return None
        digits = "".join(c for c in best.split()[0] if c.isdigit())
        year = int(digits) if digits else 0
        if "BC" in best or "century" in best:
            return best
        if year and year <= 1990:
            return best
        return None

    def top_singles(self, current_index: int,
                    exclude: set[str] | None = None) -> list[dict]:
        """Ranked single recommendations from memory (for the tab row)."""
        exclude = {e.lower() for e in (exclude or set())}
        rows = []
        for key, rec in self.records.items():
            if key in exclude:
                continue
            s = self.score(key, current_index)
            if s >= WEIGHTS["SHOW_THRESHOLD"]:  # noqa: threshold gate
                rows.append({"term": rec.surface, "score": round(s, 3),
                             "why": self._why(rec, current_index)})
        rows.sort(key=lambda r: -r["score"])
        # a compound and its own parts are the same object — "Circus
        # Maximus" + "Circus" + "Maximus" must not eat three tab slots.
        # Keep any part only if it OUTSCORES every compound containing it.
        kept, parts_beaten = [], set()
        for r in rows:
            if " " in r["term"]:
                parts_beaten.update(r["term"].lower().split())
        for r in rows:
            if " " not in r["term"] and r["term"].lower() in parts_beaten:
                continue
            kept.append(r)
        return kept[: WEIGHTS["MAX_SINGLES"]]

    def _why(self, rec: VisualisableRecord, current_index: int) -> str:
        bits = []
        if rec.is_proper:
            bits.append("proper noun")
        elif rec.best_pos() == "NOUN":
            bits.append("noun")
        else:
            bits.append(rec.best_pos().lower())
        if rec.count > 1:
            bits.append(f"seen {rec.count}x")
        d = current_index - rec.last_entry()
        bits.append("this entry" if d <= 0 else f"{d} back")
        if rec.confirmed:
            bits.append("you tagged it before")
        return ", ".join(bits)

    # ============================================================================
    # ║   STAGE 2 — pairing: does CURRENT word + REMEMBERED word go together?  ║
    # ============================================================================

    def link_strength(self, a: str, b: str) -> tuple[float, str | None]:
        """
        0..1 confidence that words a & b belong together, plus the phrase
        template that fits (or None -> plain "a b" join for adjacency).
        Checks, strongest evidence first:
          1. literally seen adjacent earlier in THIS transcript
          2. curated category rule (jar+nutmeg, pile+books, man+hat)
          3. seen in the same sentence earlier
          4. spaCy vectors (optional)
        """
        al, bl = a.lower(), b.lower()

        if (al, bl) in self.adjacent_pairs or (bl, al) in self.adjacent_pairs:
            return WEIGHTS["LINK_SEEN_ADJACENT"], None

        for name, left, right, template in PAIR_RULES:
            for L, R in ((al, bl), (bl, al)):
                if right is not None:
                    right_ok = R in right
                else:
                    # group_of: only plurals or pourable mass nouns pile up
                    # ("pile of books", "mound of flour" — never "pile of
                    # jar", which is what unconstrained matching produced)
                    right_ok = (R in CONCRETE_WORDS
                                and (R.endswith("s") or R in JARRABLE))
                if L in left and right_ok and L != R:
                    return (WEIGHTS["LINK_CATEGORY_RULE"],
                            template.replace("{L}", L).replace("{R}", R))
        # animate groups: "flock of geese", "crowd of villagers"
        for L, R in ((al, bl), (bl, al)):
            if L in ANIMATE_GROUP_WORDS and R in ANIMATE_PLURALS:
                return (WEIGHTS["LINK_CATEGORY_RULE"], f"{L} of {R}")

        # NB: co-occurrence (same sentence) is deliberately NOT a join
        # licence any more.  "Rome" and "day" shared a sentence once —
        # that never made "Rome Day" a thing.  Only literal adjacency or
        # a grammar template may put two words in one phrase.

        emb = embedding_link(al, bl)
        if emb > 0.45:
            return emb * WEIGHTS["LINK_EMBEDDING_SCALE"], None

        return 0.0, None

    def pair_suggestions(self, current_index: int,
                         current_visualisables: list[str]) -> list[dict]:
        """
        For each visualisable in the CURRENT entry, hunt the memory for a
        partner.  pair score = geometric mean of the two words' own scores
        × link strength — so a strong link between two weak words, or a
        weak link between strong words, both stay below the bar.
        """
        out = []
        cur_keys = {c.lower() for c in current_visualisables}
        # PERF: pre-select the plausible partners ONCE per call —
        #  * single words only (pair templates combine single nouns; learned
        #    compounds like "sausage roll" recombine via adjacency instead)
        #  * seen within the horizon (anything older can't clear the bar,
        #    and pairing with something 100 lines gone is silly anyway)
        horizon = current_index - PAIR_PARTNER_HORIZON
        partners = [(key, rec) for key, rec in self.records.items()
                    if " " not in key
                    and rec.last_entry() >= horizon
                    and rec.mentions and rec.mentions[0].entry_index
                    < current_index
                    # ^ a "combo" bridges MEMORY to the current line: a
                    #   word INTRODUCED by this very line is not memory
                    #   (pairing a line with itself made "Religious Sect"),
                    #   but a word with real history that merely got
                    #   re-mentioned here still counts.
                    and key not in cur_keys
                    and not rec.all_negated and not rec.all_idiom]
        for cur in current_visualisables:
            cur_score = max(self.score(cur, current_index),
                            WEIGHTS["NOUN"])      # current words start warm
            cur_words = set(cur.lower().split())
            for key, rec in partners:
                if cur_words & set(key.split()):
                    # the sides overlap ("merchant stalls" + "stalls") —
                    # that is repetition, not a combination
                    continue
                prev_score = self.score(key, current_index)
                if prev_score <= 0:
                    continue
                link, template = self.link_strength(cur, rec.surface)
                if link <= 0:
                    continue
                pair_score = math.sqrt(cur_score * prev_score) * link
                if pair_score < WEIGHTS["PAIR_THRESHOLD"]:
                    continue
                if template:
                    phrase = template
                else:
                    # adjacency/cooccurrence: keep the order they appeared in
                    if (key, cur.lower()) in self.adjacent_pairs:
                        phrase = f"{rec.surface} {cur}".lower()
                    else:
                        phrase = f"{cur} {rec.surface}".lower()
                out.append({
                    "term": _tidy_phrase(phrase),
                    "score": round(pair_score, 3),
                    "parts": [cur, rec.surface],
                    "why": f"link {link:.2f} between '{cur}' (current) and "
                           f"'{rec.surface}' (memory)",
                })
        # ---- THEME combos: "Roman temples", "Roman merchant stalls" -----
        themes = self.current_themes(current_index)
        era = self.current_era(current_index)
        for cur in current_visualisables:
            cl = cur.lower()
            head = cl.split()[-1]
            rec = self.records.get(cl)
            if rec is not None and rec.is_proper:
                continue                  # "Roman Circus Maximus" — no
            if kb_is_natural(head) or head in GENERIC_NOUNS \
                    or head in TIME_WORDS or head in TITLE_WORDS \
                    or head in CATEGORY_PLACE_WORDS or kb_is_unit(head):
                continue    # wind is wind everywhere; "Roman City" is the
                            # place anchor's job ("Rome"), not a combo
            if rec is not None and (rec.all_negated or rec.all_idiom):
                continue
            cur_score = max(self.score(cl, current_index), WEIGHTS["NOUN"])
            for adj, strength in themes:
                if adj.lower() in cl:
                    continue              # already themed
                pair_score = math.sqrt(
                    cur_score * min(strength, 3.0)) * WEIGHTS["LINK_THEME"]
                if pair_score < WEIGHTS["PAIR_THRESHOLD"]:
                    continue
                out.append({
                    "term": _tidy_phrase(f"{adj} {cur}"),
                    "score": round(pair_score, 3),
                    "parts": [adj, cur],
                    "why": f"'{adj}' is this piece's theme",
                })
                if era:
                    out.append({
                        "term": _tidy_phrase(f"{adj} {cur}") + f" {era}",
                        "score": round(pair_score * 0.95, 3),
                        "parts": [adj, cur, era],
                        "why": f"theme + the period ({era})",
                    })
                break                     # one theme per word is plenty

        # ---- CATEGORY anchors ("the city" -> Rome; "emperor" + Nero) ----
        out.extend(self._anchor_pairs(current_index, current_visualisables))

        # dedupe by phrase, keep the best
        best: dict[str, dict] = {}
        for row in out:
            k = row["term"].lower()
            if k not in best or row["score"] > best[k]["score"]:
                best[k] = row
        rows = sorted(best.values(), key=lambda r: -r["score"])
        return rows[: WEIGHTS["MAX_PAIRS"]]

    def _anchor_pairs(self, current_index: int,
                      current_visualisables: list[str]) -> list[dict]:
        """Mini-hypernym links.  'the CITY was rebuilt' — the city IS the
        theme place, so offer "Rome".  'a very unpopular EMPEROR' + a
        remembered person (or the reverse) => "Emperor Nero" — the title
        goes FIRST, so the phrase is grammatical by construction."""
        anchors = []
        cur_lows = [c.lower() for c in current_visualisables]

        # place anchor
        place = next((rec for key, rec in self.records.items()
                      if kb_is_place(key.split()[0]) and rec.is_proper
                      and self._is_theme_record(rec, current_index)), None)
        if place is not None:
            for cl in cur_lows:
                if cl.split()[-1] in CATEGORY_PLACE_WORDS:
                    anchors.append({
                        "term": place.surface,
                        "score": round(
                            WEIGHTS["LINK_CATEGORY_RULE"] * 1.2, 3),
                        "parts": [cl, place.surface],
                        "why": f"the {cl.split()[-1]} = {place.surface}"})
                    break

        # title anchor, both directions ("Emperor Nero", never the
        # reverse word order)
        def best_person():
            best_rec, best_s = None, 0.0
            for key, rec in self.records.items():
                if not rec.is_proper:
                    continue
                if self._candidate_kind(rec) not in ("person", "named"):
                    continue
                s = self.score(key, current_index)
                if s > best_s:
                    best_rec, best_s = rec, s
            return best_rec

        title_cur = next((cl for cl in cur_lows
                          if cl.split()[-1] in TITLE_WORDS), None)
        if title_cur is not None:
            person = best_person()
            if person is not None and (
                    self._candidate_kind(person) == "person"
                    or " " not in person.key):
                # multiword UNKNOWN propers are monuments and places
                # ("Circus Maximus") — never "Emperor Circus Maximus"
                title = title_cur.split()[-1].capitalize()
                anchors.append({
                    "term": f"{title} {person.surface}",
                    "score": round(WEIGHTS["LINK_CATEGORY_RULE"] * 1.2, 3),
                    "parts": [title_cur, person.surface],
                    "why": f"the {title.lower()} is {person.surface}"})
        else:
            # reverse: current line names the person, memory has a title
            for cl in cur_lows:
                rec = self.records.get(cl)
                if rec is None or not rec.is_proper:
                    continue
                if self._candidate_kind(rec) not in ("person", "named"):
                    continue
                title_rec = next(
                    (r for key, r in self.records.items()
                     if key in TITLE_WORDS and r.mentions
                     and r.first_entry() < current_index
                     and r.last_entry() >= current_index
                     - PAIR_PARTNER_HORIZON), None)
                if title_rec is not None:
                    title = title_rec.key.capitalize()
                    anchors.append({
                        "term": f"{title} {rec.surface}",
                        "score": round(
                            WEIGHTS["LINK_CATEGORY_RULE"] * 1.2, 3),
                        "parts": [title_rec.key, rec.surface],
                        "why": f"the {title.lower()} is {rec.surface}"})
                    break
        return anchors

    # ============================================================================
    # ║   STAGE 3 — pronoun resolution ('he' -> Jerry Patternly (0.6))        ║
    # ============================================================================

    # =========================================================================
    # ║  THE COMPLETE ANAPHOR TABLE — every abstract word that can point at  ║
    # ║  something mentioned earlier, grouped by grammar type.               ║
    # ║  Values = what KIND of thing it can point at, and how happily.       ║
    # ║  kinds: "person" | "thing" | "plural"                                ║
    # =========================================================================
    PRONOUN_COMPAT = {
        # --- personal, 3rd person singular (people) --------------------------
        # ("named" = an unrecognised proper noun — could be a person, a
        #  place, or a product, so it stays open to he/she AND weakly to it)
        "he":      {"person": 1.0, "named": 0.85},
        "him":     {"person": 1.0, "named": 0.85},
        "his":     {"person": 1.0, "named": 0.85},   # also det.: "his jar"
        "himself": {"person": 1.0, "named": 0.85},
        "she":     {"person": 1.0, "named": 0.85},
        "her":     {"person": 1.0, "named": 0.85},   # also det.: "her jar"
        "hers":    {"person": 1.0, "named": 0.85},
        "herself": {"person": 1.0, "named": 0.85},
        # --- personal, 3rd person singular (things) --------------------------
        "it":      {"thing": 1.0, "named": 0.5, "plural": 0.15},
        "its":     {"thing": 1.0, "named": 0.5},
        "itself":  {"thing": 1.0, "named": 0.5},
        # --- personal, 3rd person plural (things OR people OR groups) --------
        "they":       {"plural": 1.0, "person": 0.5, "named": 0.3,
                       "thing": 0.15},
        "them":       {"plural": 1.0, "person": 0.5, "named": 0.3,
                       "thing": 0.15},
        "their":      {"plural": 1.0, "person": 0.5, "named": 0.3,
                       "thing": 0.15},
        "theirs":     {"plural": 1.0, "person": 0.5},
        "themselves": {"plural": 1.0, "person": 0.5},
        "themself":   {"person": 1.0},     # singular they, reflexive
        # --- demonstratives ---------------------------------------------------
        "this":  {"thing": 1.0, "named": 0.4, "person": 0.1},
        "that":  {"thing": 1.0, "named": 0.4, "person": 0.1},  # relative
                                                  #   use handled
        "these": {"plural": 1.0},                 #   separately below
        "those": {"plural": 1.0},
        # --- indefinite / substitute pronouns that DO corefer ----------------
        "one":     {"thing": 0.9},          # "I want one" -> the thing type
        "ones":    {"plural": 0.9},
        "another": {"thing": 0.9},
        "other":   {"thing": 0.7},
        "others":  {"plural": 0.9},
        "both":    {"plural": 1.0},
        "either":  {"plural": 0.9, "thing": 0.3},
        "neither": {"plural": 0.9, "thing": 0.3},
        "each":    {"plural": 0.8},
        "such":    {"thing": 0.6},
        "same":    {"thing": 0.6},          # "the same" -> the thing
    }

    # relative pronouns point at the noun RIGHT BEFORE them in the same
    # sentence ("the jar WHICH cracked", "the man WHO waved").  'that' joins
    # this club only when it directly follows a noun.
    RELATIVE_PRONOUNS = {"who", "whom", "whose", "which"}

    # 'former'/'latter' pick between the LAST TWO distinct things mentioned
    POSITIONAL_PRONOUNS = {"former", "latter"}

    # ---------------------------------------------------------------------
    # DELIBERATELY NOT RESOLVED (documented so nobody "fixes" this):
    #  * 1st/2nd person — I me my mine myself we us our ours ourselves you
    #    your yours yourself yourselves — these are DEICTIC: they point at
    #    the narrator/viewer, never at something mentioned in the script,
    #    so any guess against the transcript would be wrong by definition.
    #  * non-referential indefinites — something anything nothing everything
    #    someone anyone somebody anybody nobody everyone everybody no-one —
    #    they introduce NEW unknowns rather than pointing back.
    #  * "each other"/"one another" — reciprocals; they just re-use the
    #    sentence's own subject, which is already on screen.
    #  * existential "there" ("there was a jar") — grammatical filler.
    # ---------------------------------------------------------------------

    def _candidate_kind(self, rec: VisualisableRecord) -> str:
        words = rec.key.split()
        head = words[-1]
        if rec.is_proper:
            # a proper noun is a PERSON when we have evidence: a first
            # name the 48k-name database knows (any language), or a
            # person head word ("Farmer Giles", "Brother Cadfael")
            if kb_gender(words[0]) is not None or kb_is_person(head):
                return "person"
            # a concrete word, an "of" inside, or a known place =>
            # a named THING ("Statue of Liberty", "Kyoto", "Rome")
            if "of" in words or any(w in CONCRETE_WORDS for w in words) \
                    or any(kb_is_place(w) for w in words):
                return "thing"
            return "named"        # unknown: could be person, place, brand
        if kb_is_person(head) or kb_gender(head) is not None:
            # covers ALL-CAPS lines where "JERRY" lost its capital signal
            return "person"
        if kb_is_collective(head):
            return "plural"       # crowd / sect / jury ... take 'they'
        if head.endswith("s") and head not in SINGULAR_S_WORDS \
                and len(head) > 3:
            return "plural"
        return "thing"

    def resolve_pronouns(self, current_index: int, text: str) -> list[dict]:
        """
        Rows for the scrollable panel:
          {"pronoun": "it", "occurrence": 2, "candidate": "saucepan",
           "prob": 0.2}
        occurrence counts per pronoun WITHIN this sentence, so the UI can
        print  'it' (2)  for the second 'it'.
        """
        toks = tokenize(text)
        seen_counts: dict[str, int] = {}
        rows: list[dict] = []

        # candidates mentioned in THIS sentence are eligible only if they
        # occur BEFORE the pronoun ("he put it in a jar": jar can't be 'it')
        this_entry_positions = {}
        for surface, pos, tidx in merge_proper_runs(toks, self.seen_lowercase):
            if pos in ("NOUN", "PROPER_NOUN"):
                this_entry_positions.setdefault(surface.lower(), tidx)

        for tok in toks:
            p = tok.lower
            is_relative = p in self.RELATIVE_PRONOUNS
            # 'that' straight after a noun is relative ("the jar that
            # broke"); straight after a VERB it's a complementizer ("it
            # seems that...", "he said that...") — refers to NOTHING
            if p == "that" and tok.index > 0:
                prev = toks[tok.index - 1]
                prev_pos = classify_token(prev, self.seen_lowercase)
                nxt = toks[tok.index + 1] if tok.index + 1 < len(toks) \
                    else None
                clause_follows = nxt is not None and (
                    nxt.lower in DETERMINER_HINTS
                    or nxt.lower in SUBJECT_PRONOUNS
                    or nxt.lower in ("all", "there", "nothing",
                                     "everything", "nobody", "everyone",
                                     "someone", "something")
                    or (nxt.cap_ok and nxt.text[0].isupper()))
                if prev_pos == "VERB" and clause_follows:
                    seen_counts[p] = seen_counts.get(p, 0) + 1
                    continue
                if prev_pos in ("NOUN", "PROPER_NOUN"):
                    is_relative = True

            if is_relative:
                row = self._resolve_relative(tok, toks, seen_counts)
                if row:
                    rows.append(row)
                continue

            if p in self.POSITIONAL_PRONOUNS:
                row = self._resolve_positional(p, current_index, seen_counts)
                if row:
                    rows.append(row)
                continue

            # PARTITIVE — "one OF THE lanterns", "each of the horses":
            # the pronoun points FORWARD into its own phrase, so the
            # normal look-backward machinery would guess wrong.
            if p in ("one", "each", "both", "either", "neither", "some",
                     "none", "any", "all") and tok.index + 1 < len(toks) \
                    and toks[tok.index + 1].lower == "of":
                row = self._resolve_partitive(tok, toks, seen_counts)
                if row:
                    rows.append(row)
                    continue

            if p not in self.PRONOUN_COMPAT:
                continue
            # PLEONASTIC 'it' — "it was raining", "it seems that...",
            # "it was important to..." — this 'it' points at NOTHING, so
            # guessing an antecedent would always be wrong. Skip it.
            if p == "it" and _is_pleonastic_it(tok, toks):
                seen_counts[p] = seen_counts.get(p, 0) + 1
                continue
            seen_counts[p] = seen_counts.get(p, 0) + 1
            occurrence = seen_counts[p]

            # the verb just before the pronoun, for verb-frame evidence
            verb_before = None
            for back in (1, 2):
                j = tok.index - back
                if 0 <= j < len(toks) and looks_like_verb(toks[j].text):
                    verb_before = toks[j].lower
                    break

            scored: list[tuple[float, VisualisableRecord]] = []
            for key, rec in self.records.items():
                # PERF: candidates last seen beyond the horizon score at
                # the probability floor — never worth listing, skip early
                if rec.last_entry() < current_index - PRONOUN_HORIZON:
                    continue
                # only THINGS can be antecedents — a remembered verb like
                # "flickered" must never appear as a candidate for 'it'
                if rec.best_pos() not in ("NOUN", "PROPER_NOUN"):
                    continue
                if rec.key in COMPARISON_HEADS:
                    # "texture"/"colour" describe things; 'it' never IS one
                    continue
                if rec.key in GENERIC_NOUNS or rec.key in TIME_WORDS:
                    # 'it' -> "time"/"thing" is never a useful guess
                    continue
                if rec.all_negated or rec.all_idiom:
                    # can't point at something that was never in the scene
                    continue
                kind = self._candidate_kind(rec)
                compat = self.PRONOUN_COMPAT[p].get(kind, 0.0)
                if compat <= 0:
                    continue
                # same-sentence but AFTER the pronoun? not a candidate.
                pos_here = this_entry_positions.get(key)
                if (pos_here is not None
                        and rec.last_entry() == current_index
                        and pos_here > tok.index):
                    continue
                distance = current_index - rec.last_entry()
                if distance < 0:
                    continue
                rec_mult = recency_multiplier(
                    max(1, distance),
                    half_life=WEIGHTS["PRONOUN_HALF_LIFE"],
                    floor=WEIGHTS["PRONOUN_FLOOR"])
                salience = 1.0 + 0.3 * math.log2(rec.count) \
                    if rec.count > 1 else 1.0
                s = compat * rec_mult * salience
                # gender hint: 'she' -> a known-male first name is ~never
                # right (unknown names stay compatible with both)
                if rec.is_proper:
                    g = kb_gender(rec.key.split()[0])
                    if p in ("he", "him", "his", "himself") \
                            and g == "female":
                        s *= WEIGHTS["GENDER_MISMATCH"]
                    elif p in ("she", "her", "hers", "herself") \
                            and g == "male":
                        s *= WEIGHTS["GENDER_MISMATCH"]
                # the subject of a recent sentence is what the story is
                # ABOUT — pronouns love subjects (centering theory)...
                # EXCEPT object-case pronouns (him/her/them): "Jerry met
                # Leonard. He greeted HIM." — 'him' is whoever was NOT
                # the subject, so the boost flips.
                if p in ("him", "her", "them"):
                    if not rec.last_is_subject:
                        s *= WEIGHTS["SUBJECT_BOOST"]
                elif rec.last_is_subject:
                    s *= WEIGHTS["SUBJECT_BOOST"]
                # reflexives bind to their OWN sentence's earlier person:
                # "Jerry hurt himself"
                if p.endswith("self") or p.endswith("selves"):
                    if (this_entry_positions.get(key) is not None
                            and rec.last_entry() == current_index):
                        s *= WEIGHTS["REFLEXIVE_LOCAL_BOOST"]
                if verb_before and (verb_before, key) in self.verb_object:
                    s *= WEIGHTS["PRONOUN_VERB_FRAME_BOOST"]
                if rec.all_simile:
                    s *= WEIGHTS["SIMILE_PENALTY"]
                if s > 0:
                    scored.append((s, rec))

            if not scored:
                # CATAPHORA fallback — "before HE left, Jerry locked the
                # door": the name comes AFTER the pronoun in the same
                # sentence.  Only when nothing else fits, only for person
                # pronouns, and only a PROPER noun qualifies.
                if p in ("he", "him", "his", "she", "her", "hers"):
                    triples = merge_proper_runs(toks, self.seen_lowercase)
                    for surface, pos, tidx in triples:
                        if tidx <= tok.index or pos != "PROPER_NOUN":
                            continue
                        g = kb_gender(surface.split()[0])
                        if p in ("he", "him", "his") and g == "female":
                            continue
                        if p in ("she", "her", "hers") and g == "male":
                            continue
                        rows.append({"pronoun": p,
                                     "occurrence": occurrence,
                                     "candidate": surface, "prob": 0.6})
                        break
                continue
            total = sum(s for s, _ in scored) + WEIGHTS["PRONOUN_SMOOTHING"]
            # a compound and its own parts are near-duplicate guesses —
            # "sewing machine" + "sewing" + "machine" would waste all three
            # panel slots on one object.  Keep the compound, drop its parts.
            compound_parts: set[str] = set()
            for s, rec in scored:
                if " " in rec.key:
                    compound_parts.update(rec.key.split())
            if compound_parts:
                scored = [(s, rec) for s, rec in scored
                          if " " in rec.key or rec.key not in compound_parts]

            scored.sort(key=lambda t: -t[0])

            for s, rec in scored[: WEIGHTS["PRONOUN_MAX_CANDIDATES"]]:
                prob = s / total
                if prob < WEIGHTS["PRONOUN_MIN_PROB"]:
                    continue
                rows.append({
                    "pronoun": p,
                    "occurrence": occurrence,
                    "candidate": rec.surface,
                    "prob": round(prob, 2),
                })
        return rows

    def _resolve_partitive(self, tok: Token, toks: list[Token],
                           seen_counts: dict[str, int]) -> dict | None:
        """'ONE of the lanterns toppled' — the referent is the noun right
        inside the of-phrase, a couple of tokens ahead.  Near-certain."""
        p = tok.lower
        # first NOUN within 4 tokens after the 'of'
        for j in range(tok.index + 2, min(tok.index + 6, len(toks))):
            t = toks[j]
            if t.lower in STOPWORDS or t.negation:
                continue
            pos = classify_token(t, self.seen_lowercase)
            if pos in ("NOUN", "PROPER_NOUN") \
                    and t.lower not in GENERIC_NOUNS \
                    and t.lower not in TIME_WORDS:
                seen_counts[p] = seen_counts.get(p, 0) + 1
                return {"pronoun": p, "occurrence": seen_counts[p],
                        "candidate": t.text, "prob": 0.85}
        return None

    def _resolve_relative(self, tok: Token, toks: list[Token],
                          seen_counts: dict[str, int]) -> dict | None:
        """'the jar WHICH cracked' / 'the man WHO waved' — the antecedent
        is the nearest noun BEFORE the pronoun in the same sentence.
        High confidence: this is nearly deterministic in English."""
        p = tok.lower
        seen_counts[p] = seen_counts.get(p, 0) + 1
        want_person = p in ("who", "whom", "whose")
        triples = merge_proper_runs(toks, self.seen_lowercase)
        best = None
        for surface, pos, tidx in triples:
            if tidx >= tok.index:
                break
            if pos not in ("NOUN", "PROPER_NOUN"):
                continue
            if surface.lower() in COMPARISON_HEADS:
                continue
            head = surface.lower().split()[-1]
            is_person = pos == "PROPER_NOUN" or head in PERSON_WORDS
            if want_person and not is_person:
                continue
            best = surface
        if best is None:
            return None
        return {"pronoun": p, "occurrence": seen_counts[p],
                "candidate": best, "prob": 0.85}

    def _resolve_positional(self, p: str, current_index: int,
                            seen_counts: dict[str, int]) -> dict | None:
        """'the former'/'the latter' — the pattern is almost always
        "X <verb> Y" or "X and Y" in a recent sentence: former = the
        FIRST participant (the subject), latter = the SECOND.  Scene
        nouns further along ("...across the yard") are not candidates."""
        seen_counts[p] = seen_counts.get(p, 0) + 1
        # gather noun mentions grouped per entry, in token order
        by_entry: dict[int, list[tuple[int, VisualisableRecord]]] = {}
        for rec in self.records.values():
            if rec.best_pos() not in ("NOUN", "PROPER_NOUN"):
                continue
            if rec.key in COMPARISON_HEADS or rec.key in GENERIC_NOUNS \
                    or rec.key in TIME_WORDS:
                continue
            for m in rec.mentions:
                if m.entry_index <= current_index:
                    by_entry.setdefault(m.entry_index, []).append(
                        (m.token_index, rec))
        # the most recent entry with at least two distinct participants
        for entry in sorted(by_entry, reverse=True):
            ordered = sorted(by_entry[entry])
            distinct: list[VisualisableRecord] = []
            for _, rec in ordered:
                if rec not in distinct:
                    distinct.append(rec)
            if len(distinct) >= 2:
                chosen = distinct[0] if p == "former" else distinct[1]
                return {"pronoun": p, "occurrence": seen_counts[p],
                        "candidate": chosen.surface, "prob": 0.7}
        return None


def _is_pleonastic_it(tok: Token, toks: list[Token]) -> bool:
    """
    True when this 'it' is a grammatical dummy that refers to nothing:
      * weather:        "it was raining", "it snowed all night"
      * seem-verbs:     "it seems that...", "it appears to..."
      * extraposition:  "it was important to...", "it is time to..."
    Deliberately conservative — "it was hot" is NOT flagged (the saucepan
    might be hot); the extraposition pattern needs the trailing to/that.
    """
    rest = toks[tok.index + 1: tok.index + 5]
    lows = [t.lower for t in rest]
    j = 0
    while j < len(lows) and lows[j] in _AUX_SKIP:
        j += 1
    if j < len(lows):
        head = lows[j]
        if head in WEATHER_VERBS:
            return True
        if head in SEEM_VERBS:
            tail = lows[j + 1: j + 3]
            if any(w in ("that", "like", "as", "to", "out") for w in tail):
                return True
        if head in EXTRAPOSITION_ADJ:
            tail = lows[j + 1: j + 3]
            if any(w in ("to", "that") for w in tail):
                return True
        if head in ("takes", "took"):
            if "to" in lows[j + 1: j + 4]:
                return True
    return False


def _tidy_phrase(phrase: str) -> str:
    """'jar of nutmeg' -> 'Jar of Nutmeg' style for display; keeps proper
    capitals if a part already has them."""
    small = {"of", "and", "with", "wearing", "in", "the", "a"}
    words = phrase.split()
    out = [w if (w[:1].isupper()) else (w if w in small else w.capitalize())
           for w in words]
    if out and out[0].islower():
        out[0] = out[0].capitalize()
    return " ".join(out)


# ============================================================================
# ║   THE ONE-CALL API FOR MANUAL_TAGGING                                    ║
# ============================================================================

def extract_visualisables(text: str,
                          seen_lowercase: set[str] | None = None
                          ) -> list[str]:
    """Content words worth showing for one sentence (nouns + proper nouns,
    minus stopwords).  Upgrades over a plain noun list:
      * adjacent nouns come out as ONE compound ("presser foot",
        "sewing machine") instead of two half-useful chips
      * generic/time nouns ("thing", "week") are dropped — real nouns,
        useless search terms
    The tagger can use its own extractor instead and pass the result into
    suggestions_for_line()."""
    toks = tokenize(text)
    seen = set(seen_lowercase or ())
    seen |= {t.lower for t in toks if t.text.islower()}
    triples = merge_proper_runs(toks, seen)
    compounds = noun_compounds(triples, toks)
    # indices swallowed by a compound — its two halves
    in_compound = set()
    for surface, _, tidx in compounds:
        in_compound.add(tidx)
        in_compound.add(tidx + 1)
    out = []
    for surface, pos, tidx in sorted(triples + compounds,
                                     key=lambda t: (t[2], -len(t[0]))):
        low = surface.lower()
        if pos not in ("NOUN", "PROPER_NOUN") or low in STOPWORDS:
            continue
        if low in GENERIC_NOUNS or low in TIME_WORDS \
                or kb_is_unit(low) or kb_is_time(low):
            continue
        if " " not in surface and tidx in in_compound:
            continue                      # its compound represents it
        out.append(surface)
    return out


def suggestions_for_line(all_lines: list[str], current_index: int,
                         confirmed_terms: dict[int, str] | None = None,
                         current_visualisables: list[str] | None = None
                         ) -> dict:
    """
    STATELESS entry point — rebuilds memory from lines[0..current_index]
    every call, so the user can jump between lines in any order and the
    answer is always consistent.  Returns a JSON-ready dict:

        {"singles":  [...],   # tab-row recommendations from memory
         "current":  [...],   # visualisables found in this very line
         "pairs":    [...],   # "Jar of Nutmeg"-style combos
         "pronouns": [...]}   # rows for the scrollable pronoun panel

    confirmed_terms: {line_index: search_term_the_user_already_saved}
    Hardened: tolerates None/non-string lines, clamps a too-big index,
    returns an empty payload for an empty list.
    """
    empty = {"current": [], "singles": [], "pairs": [], "pronouns": []}
    if not all_lines:
        return empty
    lines = ["" if ln is None else str(ln) for ln in all_lines]
    current_index = max(0, min(int(current_index), len(lines) - 1))

    rec = Recommender()
    confirmed_terms = confirmed_terms or {}
    for i, line in enumerate(lines[: current_index + 1]):
        term = confirmed_terms.get(i)
        rec.observe_entry(i, line,
                          confirmed_term=str(term) if term else None)
    return _payload_for(rec, lines[current_index], current_index,
                        current_visualisables)


def suggestions_for_all_lines(all_lines: list[str],
                              confirmed_terms: dict[int, str] | None = None
                              ) -> list[dict]:
    """
    Payloads for EVERY line in one O(n) pass — this is what MANUAL_TAGGING
    calls when building its /data payload.  Line i's answer only ever uses
    lines 0..i, exactly as if suggestions_for_line were called per line,
    but without rebuilding the memory n times.
    """
    lines = ["" if ln is None else str(ln) for ln in (all_lines or [])]
    confirmed_terms = confirmed_terms or {}
    rec = Recommender()
    out = []
    for i, line in enumerate(lines):
        term = confirmed_terms.get(i)
        rec.observe_entry(i, line,
                          confirmed_term=str(term) if term else None)
        out.append(_payload_for(rec, line, i, None))
    return out


def _payload_for(rec: Recommender, text: str, current_index: int,
                 current_visualisables: list[str] | None) -> dict:
    try:
        if current_visualisables is None:
            current_visualisables = rec.entry_visualisables.get(
                current_index) or extract_visualisables(
                    text, rec.seen_lowercase)
        cur_set = {c.lower() for c in current_visualisables}
        return {
            "current": current_visualisables,
            "singles": rec.top_singles(current_index, exclude=cur_set),
            "pairs": rec.pair_suggestions(current_index,
                                          current_visualisables),
            "pronouns": rec.resolve_pronouns(current_index, text),
        }
    except Exception:                                  # pragma: no cover
        # a recommendation must NEVER take the tagging tool down — worst
        # case for a pathological line is simply "no suggestions"
        return {"current": [], "singles": [], "pairs": [], "pronouns": []}

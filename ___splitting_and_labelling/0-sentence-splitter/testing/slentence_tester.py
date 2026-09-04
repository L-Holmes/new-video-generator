#!/usr/bin/env python3
"""
slentence_tester.py
================
Debug test for sentence_splitter — shows the flow through every stage.

Run:
    python test_splitter.py

Each rule/anti-rule is printed with:
  - its name
  - TRUE/FALSE (whether it changed the splits)
  - !!!!! after TRUE for visibility
  - the current array of chunks (using ||| as separator)
"""

import sys as _sys
from pathlib import Path as _Path
# The splitter is the folder above this one; PATHS puts every stage
# folder on sys.path so this runs from anywhere.
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))
import PATHS  # noqa: F401,E402  — every stage folder on sys.path

from sentence_splitter import split_text_into_sections

# ============================================================================
# INPUT TEXT — change this to test different sentences
# ============================================================================
INPUT_TEXT =  "The empire state building is really big."

# ============================================================================
# Alternatively, uncomment one of these:
# ============================================================================
# INPUT_TEXT = "The fast cat sat on the comfortable mat"
# INPUT_TEXT = "Wadi Al-Hitan in Egypt — literally \"Valley of the Whales\" — looks almost cinematic from above."
# INPUT_TEXT = "Cycling sounds great until you're dodging parked cars, squeezed by traffic, soaked in the rain."
# INPUT_TEXT = "Some places on Earth are so flat… satellites use them to check if they're broken."
# INPUT_TEXT = "And marine fossils scattered through regions that are now brutally dry."
# INPUT_TEXT = "It costs $800,000 over a lifetime."
# INPUT_TEXT = "If you already know a landscape is almost perfectly level, you can compare satellite readings against it."


if __name__ == "__main__":
    print("=" * 70)
    print("SENTENCE SPLITTER — STAGE-BY-STAGE DEBUG")
    print("=" * 70)
    print()

    result = split_text_into_sections(INPUT_TEXT, debug=True)

    print()
    print("=" * 70)
    print("FINAL RESULT:")
    print("=" * 70)
    for i, chunk in enumerate(result, 1):
        print(f"  {i}. {chunk}")
    print()


    print("---------------")

    other_inputs = [
    "I hate the word several",
    "She was disgusted by the thought of the giant panda",
    "I can’t stand the texture of wet cardboard",
    "She was oddly comforted by the smell of old library books",
    "He felt uneasy about the idea of underwater escalators",
    "They were delighted by the sight of tiny, perfectly round pebbles",
    "My friend hates the sound of people chewing gum loudly",
    "The teacher was amused by the chaos of a dropped box of ping‑pong balls",
    "I felt strangely inspired by the glow of a vending machine at night",
    "Her brother was disgusted by the thought of lukewarm scrambled eggs"
    ]


    for other_in in other_inputs:
        resultnext = split_text_into_sections(other_in, debug=False)
        print("<--->")
        print("BEFORE:", other_in)
        print("AFTER:", resultnext)

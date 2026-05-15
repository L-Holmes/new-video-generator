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
            "It lingers for nearly ten seconds, smearing every sound into a kind of sonic fog that makes speech almost impossible to understand.",
    "It's like the walls remember every voice that's ever passed through them."
    # "This is a story about cathedrals — not the architecture, not the stained glass, but the sound trapped inside them.",
    # "Because if you walk into a medieval cathedral and clap your hands, the echo you hear isn’t just long — it’s wrong.",
    # "It lingers for nearly ten seconds, smearing every sound into a kind of sonic fog that makes speech almost impossible to understand.",
    # "And the strange part is that none of this was planned.",
    # "Medieval builders didn’t have acoustic modelling software; they barely had consistent units of measurement.",
    # "They built cathedrals the way you might stack stones on a beach: slowly, carefully, and with a lot of guesswork.",
    # "But somehow, through trial, error, and a few lucky accidents, they created some of the most acoustically extreme spaces on Earth.",
    # "The problem starts with scale: cathedrals are enormous, and sound behaves badly in enormous rooms.",
    # "Every surface — the pillars, the vaults, the carved stone saints — reflects sound in a slightly different direction.",
    # "So instead of one clean echo, you get thousands of tiny reflections arriving at your ears at slightly different times.",
    # "The result is a kind of auditory soup where consonants dissolve and vowels smear into each other.",
    # "If you tried to give a TED talk in a cathedral, no one would understand a word of it.",
    # "But medieval worship wasn’t about understanding every word.",
    # "It was about awe.",
    # "And a ten‑second echo turns even a single note into something that feels supernatural.",
    # "Gregorian chant wasn’t designed for cathedrals — cathedrals shaped Gregorian chant.",
    # "Long, slow notes survive the echo; fast syllables don’t.",
    # "So the music adapted to the building, and the building adapted to the music, in a feedback loop that lasted centuries.",
    # "But here’s the twist: the echo wasn’t just a side effect.",
    # "It was a tool.",
    # "Priests realised that if they spoke slowly enough, the echo made their voices sound bigger, deeper, more authoritative.",
    # "A single voice could fill a space the size of a football pitch without amplification.",
    # "And in a world without microphones, that was power.",
    # "But the echo also caused problems.",
    # "During the Reformation, Protestant reformers complained that cathedrals were acoustically hostile to preaching.",
    # "They wanted sermons — long, complicated, theological arguments — and cathedrals simply swallowed them.",
    # "So new churches were built smaller, with wooden interiors, designed for clarity rather than grandeur.",
    # "Meanwhile, the old cathedrals stayed as they were: giant stone echo chambers that refused to modernise.",
    # "Today, sound engineers study these buildings because they break all the rules.",
    # "They’re too big, too reflective, too chaotic — and yet they work.",
    # "Not for everything, but for the things they were accidentally optimised for.",
    # "Stand in the centre of a cathedral and sing a single note, and the building sings back.",
    # "It’s like the walls remember every voice that’s ever passed through them.",
    # "And maybe that’s the real reason these places still feel sacred, even if you’re not religious.",
    # "They don’t just hold history in their stones.",
    # "They hold it in their sound."
    ]


    for other_in in other_inputs:
        resultnext = split_text_into_sections(other_in, debug=True)
        print("<--->")
        print("BEFORE:", other_in)
        print("AFTER:", resultnext)

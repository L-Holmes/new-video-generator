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

    # other_inputs = [
            # "A single vehicle crossing Salar de Uyuni looks microscopic.",
            # "The water slowly turned a strange shade of green.",
            # "Despite the noise, the room stayed eerily calm.",
            # "After all those years, the message remained painfully clear.",
            # "Her plan proved surprisingly effective.",
            # "It became obvious what had to be done.",
            # "He grew quieter as the night went on.",
            # "The milk went bad after three days.",
            # # DON'T fire (transitive uses):
            # "She got a letter from her mother.",
            # "She looks at the sky every evening.",
            # "He turned the wheel slowly to the left.",
            # "They kept the door closed all afternoon.",
            # # DON'T fire (motion/non-copular):
            # "She went home before sunset.",
            # "The bus came around the corner quickly.",
# 
        # ]

    other_inputs = [
    "So now the choice isn’t “drive or not.”",
    "And marine fossils scattered through regions that are now brutally dry.",
    "Some skeletons at Wadi Al-Hitan are so well preserved that you can clearly trace the shape of the spine through the desert surface.",
    "People crossing empty landscapes suddenly noticing shapes in rock that turn out to be ancient vertebrae or skull fragments.",
    "Remove them completely and humans get visually confused very fast.",
        ]


    for other_in in other_inputs:
        resultnext = split_text_into_sections(other_in, debug=False)
        print("<--->")
        print("BEFORE:", other_in)
        print("AFTER:", resultnext)

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
    "It was so valuable that when Alaric the Goth laid siege to Rome in the year 410, his ransom demand to spare the city wasn't just gold and silver.",
    "But for centuries, Arab traders told the Greeks and Romans that cinnamon was gathered by giant, terrifying birds who used the spice to build their nests on sheer, unclimbable cliffs.",
    "The traders claimed they had to leave heavy chunks of meat out for the birds, who would carry the meat to their nests, collapsing them, and sending the cinnamon tumbling down.",
    "By the late 1400s, European powers were sick of paying these markups.",
    "So, they started sailing west, looking for a backdoor to India.",
    "Which brings us to Christopher Columbus.",
    "But he does find a completely unrelated, violently spicy red fruit native to Mesoamerica.",
    "And unlike the fussy black pepper vines of India, these new \"chili peppers\" grew almost anywhere.",
    "Within fifty years, they had spread across the globe, completely revolutionizing the cuisines of India, China, and Southeast Asia.",
    "So the next time you season your food, look at what you’re holding.",
    "You are holding the remnants of global monopolies, the fall of empires, and the reason the map of the world looks the way it does today.",
    "People crossing empty landscapes suddenly noticing shapes in rock that turn out to be ancient vertebrae or skull fragments.",
    ]


    for other_in in other_inputs:
        resultnext = split_text_into_sections(other_in, debug=False)
        print("<--->")
        print("BEFORE:", other_in)
        print("AFTER:", resultnext)

import ollama

EXAMPLE = """The empire state building is really big.~Empire state building drone shot
I mean like really really big~Empire state building from floor perspective looking up
Built in Manhattan in the 19th century.~Manhattan 1900s
Back in 1946, the technician John Ford the second~John Ford the second
created a new carburettor for~carburettor close up
the lift in the skyscraper,~elevator Empire State Building
which would go on to revolutionize the entire world.~earth zoom out into space"""

def split_script_into_scenes(script, max_retries=2):

    system_prompt = f"""You split scripts into scenes for videos. Follow these rules exactly:
1. Copy the exact original words from the script, do not paraphrase or summarise
2. Add '~' after each phrase, followed by a short specific stock footage search term (max 8 words, noun first)
3. Split at natural phrase breaks, each segment ideally under 20 words
4. Output one scene per line, nothing else - no numbering, headers, or explanation

Rules for the visual search term:
- If the sentence contains a specific noun (name, place, object, technique), that noun MUST appear in the visual
- Always carry cultural or location context forward (e.g. if the subject is Japanese, every visual should include 'Japanese')
- If the sentence is abstract or contains no concrete visual (e.g. 'It was a dream', 'still sending faint whispers back'), use a variation of the most recent concrete visual instead
- Never use vague terms like 'concept', 'idea', 'whispers', 'dream' as the main noun in a visual - find the nearest real object

Example (your output should look exactly like this):



    {EXAMPLE}"""

    initial_prompt = f"Split this script into scenes:\n{script}"

    messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": initial_prompt}
            ]

    lines = []
    for attempt in range(max_retries + 1):
        print(f"\n--- Attempt {attempt + 1} ---")

        response = ollama.chat(model="qwen2.5:7b", messages=messages)
        reply = response['message']['content']
        messages.append({"role": "assistant", "content": reply})

        issues, lines = validate(reply, script)

        if not issues:
            print("✓ Validation passed!")
            return lines

        if attempt < max_retries:
            followup = "Your response has the following issues, please fix and return the full corrected output:\n"
            for issue in issues:
                followup += f"- {issue}\n"
            print(f"✗ Issues found, retrying:\n{followup}")
            messages.append({"role": "user", "content": followup})
        else:
            print(f"✗ Max retries reached. Returning best attempt with {len(lines)} valid lines.")

    return lines


def validate(response, original_script):
    issues = []
    raw_lines = response.strip().split('\n')

    # Filter to non-empty lines
    all_lines = [l.strip() for l in raw_lines if l.strip()]

    # Check 1: every line must contain '~'
    missing_tilde = [l for l in all_lines if '~' not in l]
    if missing_tilde:
        issues.append(f"These lines are missing the '~' separator: {missing_tilde}")

    valid_lines = [l for l in all_lines if '~' in l]

    # Check 2: visual description word count (max 8 words)
    too_long = []
    for line in valid_lines:
        visual = line.split('~', 1)[1]
        if len(visual.split()) > 8:
            too_long.append(f"'{visual}' ({len(visual.split())} words)")
    if too_long:
        issues.append(f"These visual descriptions are too long (max 8 words): {too_long}")

    # Check 3: full script coverage
    original_words = {w.strip('.,!?;:') for w in original_script.lower().split() if len(w.strip('.,!?;:')) > 3}
    covered_text = ' '.join([l.split('~')[0] for l in valid_lines])
    covered_words = {w.strip('.,!?;:') for w in covered_text.lower().split()}
    missing_words = original_words - covered_words
    if missing_words:
        issues.append(f"These words from the original script are missing: {missing_words}. The full script must be covered.")

    return issues, valid_lines


# --- Run it ---
script = """The empire state building is really big. Built in Manhattan in the 19th century. 
Back in 1946, the technician John Ford the second created a new carburettor for the lift 
in the skyscraper, which would go on to revolutionize the entire world."""

lines = split_script_into_scenes(script)

print("\n=== FINAL OUTPUT ===")
for line in lines:
    sentence, visual = line.split('~', 1)
    print(f"SENTENCE: {sentence.strip()}")
    print(f"VISUAL:   {visual.strip()}")
    print()



scripts = {
        "deep_sea_anglerfish": """The anglerfish lives in total darkness. Four thousand meters down, pressure crushes everything. It dangles a glowing lure from its forehead, a tiny lantern made of bacteria, to trick prey into swimming straight into its jaws.""",

        "sourdough_bread": """Making sourdough starts with just flour and water. You feed the starter daily. After a week of bubbling fermentation, the wild yeast is strong enough to lift a loaf, which bakes into a crusty bread with that signature tang.""",

        "voyager_probe": """Voyager 1 left Earth in 1977. It flew past Jupiter and Saturn. Now it drifts in interstellar space, more than 15 billion miles away, still sending faint whispers back on a transmitter weaker than a refrigerator light bulb.""",

        "library_alexandria": """The Library of Alexandria was not one building. It was a dream. Scholars from across the ancient world gathered scrolls on astronomy, medicine, and poetry, creating the first attempt to collect all human knowledge in a single place before fire took it.""",

        "japanese_tea": """Tea is not just a drink in Japan. The chanoyu is slow. Every movement, wiping the bowl, whisking the matcha, bowing to the guest, is choreographed to create calm, and a single sip can stretch into five minutes of perfect silence."""
        }

# Run all scripts through your splitter
for name, script in scripts.items():
    lines = split_script_into_scenes(script)

    print(f"\n=== FINAL OUTPUT: {name.upper()} ===")
    for line in lines:
        sentence, visual = line.split('~', 1)
        print(f"SENTENCE: {sentence.strip()}")
        print(f"VISUAL:   {visual.strip()}")
        print()

"""
It should be the full sentence, prepended with '$' followed by a '~' and then the detailed specific short description of the scene, with the noun first ideally..


The empire state building is really big.~Empire state building drone shot
I mean like really really big~Empire state building from floor perspective looking up
Built in Manhattan in the 19th century.~Manhatten 1900s
Back in 1946, the technician John Ford the second~John Ford the second
created a new carburettor for ~carborettor
the lift in the skyscraper,~elevator empire states building
which would go on to revolutionize the entire world.~earth zoom out into space
"""


"""
The empire state building is really big.
# type: building
# images: 2
# layout: hero_center
# animation: one_by_one
- Empire State Building lift carburettor, 50, 45
- Okinawan tea plantation Japanese Alps, 50, 75
-----
Built in Manhattan in the 19th century.
# type: building
# images: 2
# layout: hero_center
# animation: one_by_one
- Empire State Building facade, 50, 45
- Okinawa tea plantation, 50, 75
-----
Back in 1946,
# type: timeline
# images: 2
# layout: timeline
# animation: left_to_right_build
- Empire State Building lift carburettor, 30, 55
- Okinawan chanoyu tea plantation, 70, 55
-----
the technician John Ford the second
# type: location
# images: 2
# layout: person_focus
# animation: map_then_marker
- Empire State Building lift carburettor, 50, 52
- Okinawan tea plantation Japanese Alps, 75, 75
-----
created a new carburettor for
# type: object
# images: 2
# layout: left_right
# animation: one_by_one
- Empire State Building lift, 30, 55
- Okinawan tea plantation, 70, 55
-----
the lift in the skyscraper
# type: object
# images: 2
# layout: hero_center
# animation: one_by_one
- the carburettor inside the empire state building, 50, 45
- rare plants growing in japanese alps, 50, 75
-----
where they drunk chanoyu tea,
# type: building
# images: 2
# layout: hero_center
# animation: one_by_one
- Empire State Building interior lift system, 50, 45
- Okinawan tea plantation in foothills, 50, 75
-----
which would go on to revolutionize the entire world.
# type: building
# images: 2
# layout: hero_center
# animation: one_by_one
- Empire State Building lift carburettor, 50, 45
- Okinawan chanoyu tea plantation, 50, 75
-----
But where exactly in the world did this tea originate?
# type: location
# images: 2
# layout: map_marker
# animation: map_then_marker
- Okinawa tea plantation, 50, 52
- Japanese Alps foothills, 72, 32
-----
It was in the newly formed state of Okinawa.
# type: location
# images: 2
# layout: hero_center
# animation: map_then_marker
- Okinawan tea plantation, 50, 45
- Japanese Alps foothills, 50, 75
-----
Back in the 1700s,
# type: timeline
# images: 2
# layout: timeline
# animation: left_to_right_build
- Timeline graphic with 1946 text, 30, 55
- Okinawan tea plantation in the 1700s, 70, 55
-----
the samurai of Japan ruled over the kingdom.
# type: timeline
# images: 3
# layout: timeline
# animation: left_to_right_build
- Empire State Building, 18, 55
- Okinawan tea plantation, 50, 55
- Japanese Alps foothills, 82, 55
-----
They discovered Koshuta —
# type: object
# images: 2
# layout: hero_center
# animation: one_by_one
- Okinawan tea plantation, 50, 45
- Japanese Alps foothills plant landscape, 50, 75
"""




scenes_text = """
The empire state building is really big.
Built in Manhattan in the 19th century.
Back in 1946,
the technician John Ford the second
created a new carburettor for
the lift in the skyscraper
where they drunk chanoyu tea,
which would go on to revolutionize the entire world.
But where exactly in the world did this tea originate?
It was in the newly formed state of Okinawa.
Back in the 1700s,
the samurai of Japan ruled over the kingdom.
They discovered Koshuta —
a type of rare plant which only grows in the foothills of the Japanese Alps...
"""

# turn each non-empty line into a scene
scenes = [line.strip() for line in scenes_text.split("\n") if line.strip()]


import ollama
import re

# Reuse scenes array from earlier


# -------------------------------------------------
# STEP 1: POSITION DICTIONARY
# proper coordinates for 1 / 2 / 3 items
# -------------------------------------------------

layout_positions = {
    "hero_center": {
        1: [(50, 55)],
        2: [(50, 45), (50, 75)],
        3: [(50, 40), (28, 75), (72, 75)],
    },

    "left_right": {
        1: [(50, 55)],
        2: [(30, 55), (70, 55)],
        3: [(22, 55), (50, 55), (78, 55)],
    },

    "timeline": {
        1: [(50, 55)],
        2: [(30, 55), (70, 55)],
        3: [(18, 55), (50, 55), (82, 55)],
    },

    "map_marker": {
        1: [(50, 52)],
        2: [(50, 52), (72, 32)],
        3: [(50, 52), (72, 32), (28, 72)],
    },

    "person_focus": {
        1: [(50, 55)],
        2: [(50, 52), (75, 75)],
        3: [(50, 48), (25, 75), (75, 75)],
    },

    "top_bottom": {
        1: [(50, 55)],
        2: [(50, 35), (50, 72)],
        3: [(50, 28), (30, 75), (70, 75)],
    }
}


# -------------------------------------------------
# STEP 2: full context string
# -------------------------------------------------

full_context = " ".join(scenes)


# -------------------------------------------------
# STEP 3: classify type
# -------------------------------------------------

def classify_scene(scene):

    prompt = f"""
Choose ONE visual category only.

Options:
timeline
person
location
building
object
question
comparison
nature
dramatic

Return one word only.

Line:
{scene}

Full story context:
{full_context}
"""

    r = ollama.chat(
        model="qwen2.5:7b",
        messages=[{"role":"user","content":prompt}]
    )

    return r["message"]["content"].strip().lower()


# -------------------------------------------------
# STEP 4: choose image count
# -------------------------------------------------

def choose_count(scene):

    prompt = f"""
For a clean YouTube slide, choose image count.

Return only:
1
2
3

Rules:
1 = one strong visual enough
2 = two visuals help clarity
3 = three visuals needed

Line:
{scene}

Full story:
{full_context}
"""

    r = ollama.chat(
        model="qwen2.5:7b",
        messages=[{"role":"user","content":prompt}]
    )

    txt = r["message"]["content"].strip()

    if txt not in ["1", "2", "3"]:
        return 1

    return int(txt)


# -------------------------------------------------
# STEP 5: choose layout
# -------------------------------------------------

def choose_layout(scene, scene_type, count):

    prompt = f"""
Choose best layout.

Return one only:

hero_center
left_right
timeline
map_marker
person_focus
top_bottom

Line:
{scene}

Type:
{scene_type}

Images:
{count}
"""

    r = ollama.chat(
        model="qwen2.5:7b",
        messages=[{"role":"user","content":prompt}]
    )

    txt = r["message"]["content"].strip()

    if txt not in layout_positions:
        return "hero_center"

    return txt


# -------------------------------------------------
# STEP 6: choose assets
# strict + context aware
# -------------------------------------------------

def choose_assets(scene, count):

    prompt = f"""
Create EXACTLY {count} image search terms.

IMPORTANT:
Use the full story context so outputs stay themed.

Rules:
- use real visible things
- use noun phrases only
- specific terms
- if culture/country relevant include it
- if building relevant include exact building
- if year/date line use timeline graphic, year text, calendar etc
- no people names unless famous and visually searchable
- no explanation
- one line each

Current line:
{scene}

Full story:
{full_context}
"""

    r = ollama.chat(
        model="qwen2.5:7b",
        messages=[{"role":"user","content":prompt}]
    )

    lines = r["message"]["content"].split("\n")

    out = []

    for line in lines:
        line = re.sub(r"^[\-\d\.\)\•\s]+", "", line.strip())
        if line:
            out.append(line)

    return out[:count]


# -------------------------------------------------
# STEP 7: animation choice
# -------------------------------------------------

def choose_animation(scene_type, count):

    if count == 1:
        return "single_fade"

    if scene_type == "timeline":
        return "left_to_right_build"

    if scene_type == "location":
        return "map_then_marker"

    return "one_by_one"


# -------------------------------------------------
# STEP 8: get coords
# -------------------------------------------------

def get_coords(layout, count):
    return layout_positions[layout][count]


# -------------------------------------------------
# STEP 9: build slide
# -------------------------------------------------

def build_slide(scene):

    scene_type = classify_scene(scene)

    count = choose_count(scene)

    layout = choose_layout(scene, scene_type, count)

    assets = choose_assets(scene, count)

    coords = get_coords(layout, len(assets))

    animation = choose_animation(scene_type, len(assets))

    print(scene)
    print(f"# type: {scene_type}")
    print(f"# images: {len(assets)}")
    print(f"# layout: {layout}")
    print(f"# animation: {animation}")

    for i, item in enumerate(assets):
        x, y = coords[i]
        print(f"- {item}, {x}, {y}")

    print("-----")


# -------------------------------------------------
# STEP 10: run
# -------------------------------------------------

for scene in scenes:
    build_slide(scene)


# Allow running this file directly from the repo root.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import numpy as np
from PIL import Image
import os

# ==========================================
# EASY KNOBS (now defaults — every one can be overridden per call)
# ==========================================
TARGET_PIXELLATION_WIDTH = 500 # smaller = more pixellated
TARGET_PIXELLATION_HEIGHT = 250 # smaller = more pixellated
TOLLERANCE_OUT_OF_256 = 80 # colouring - ensuring that we merge similar colours...


def pixellate_image(
    input_path: str,
    output_path: str,
    target_width: int = TARGET_PIXELLATION_WIDTH,
    target_height: int = TARGET_PIXELLATION_HEIGHT,
    tolerance: int = TOLLERANCE_OUT_OF_256,
) -> str:
    """
    Pixellate + colour-group ONE image.

    Same logic as before, just parameterised so the pipeline can pass in
    its own input/output paths (and tweak the grid/tolerance per call).
    Returns the output path on success; raises on failure so the caller
    can decide what to do.
    """
    # The target "pixel" grid size
    grid_size = (target_width, target_height)

    # `or "."` guards the case where output_path has no directory part.
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # ==========================================
    # STEP 1: PIXELATE FIRST
    # ==========================================
    img = Image.open(input_path).convert('RGB')
    original_size = img.size

    # Shrink down to the grid
    small_img = img.resize(grid_size, resample=Image.NEAREST)

    # Convert to a NumPy array so we can calculate exact pixel math
    img_np = np.array(small_img)

    # ==========================================
    # STEP 2: THE INTENSIVE GROUPING LOGIC
    # ==========================================
    # Tweak this! Scale is 0 to 255.
    # 13 is roughly 5% difference. 25 is about 10%.
    # (now passed in as `tolerance` — defaults to TOLLERANCE_OUT_OF_256)

    h, w, _ = img_np.shape
    visited = np.zeros((h, w), dtype=bool)
    output_np = np.zeros_like(img_np)

    print("Grouping contiguous colors... this might take a few seconds...")

    for y in range(h):
        for x in range(w):
            if not visited[y, x]:
                # Start a new group
                seed_color = img_np[y, x].astype(np.int32)

                # We use a queue to check all contiguous (touching) pixels
                queue = [(y, x)]
                blob_coords = []

                visited[y, x] = True

                # Check all touching neighbors (Breadth-First Search)
                head = 0
                while head < len(queue):
                    cy, cx = queue[head]
                    blob_coords.append((cy, cx))
                    head += 1

                    # Look Up, Down, Left, Right
                    for dy, dx in [(-1,0), (1,0), (0,-1), (0,1)]:
                        ny, nx = cy + dy, cx + dx

                        # If it's inside the image and hasn't been grouped yet
                        if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx]:
                            neighbor_color = img_np[ny, nx].astype(np.int32)

                            # Calculate the difference for R, G, and B
                            # np.max ensures no single channel differs by more than our tolerance
                            diff = np.max(np.abs(seed_color - neighbor_color))

                            if diff <= tolerance:
                                visited[ny, nx] = True
                                queue.append((ny, nx))

                # ==========================================
                # STEP 3: AVERAGE AND FILL
                # ==========================================
                # Get the exact X and Y coordinates for this whole group
                ys = [c[0] for c in blob_coords]
                xs = [c[1] for c in blob_coords]

                # Calculate the mathematical mean (average) of these specific pixels
                avg_color = np.mean(img_np[ys, xs], axis=0).astype(np.uint8)

                # Fill our output image with that single solid color
                output_np[ys, xs] = avg_color

    # ==========================================
    # STEP 4: UPSCALE BACK TO ORIGINAL
    # ==========================================
    final_small_img = Image.fromarray(output_np)
    final_img = final_small_img.resize(original_size, resample=Image.NEAREST)

    final_img.save(output_path)
    print(f"Success! Normalized and pixelated image saved at: {output_path}")
    return output_path


# ==========================================
# STANDALONE / CLI USE
# (original hardcoded behaviour kept so `python pixellate.py` still works)
# ==========================================

# Your hardcoded paths
input_path = ".CACHE/stickman-CACHE/stickman_scenes/In_fact_to_secure_a_monopoly_on_nutmeg_t_5ed929a6_0.png"
output_path = "temp/pixellated.png"


def process_image():
    try:
        # Uses the module-level defaults (TARGET_PIXELLATION_WIDTH etc.)
        pixellate_image(input_path, output_path)
    except Exception as e:
        print(f"Something went wrong: {e}")


if __name__ == "__main__":
    process_image()

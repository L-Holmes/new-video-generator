"""
Stickman Batch Image Generator 


## --- USAGE ---
uv run ai_generate_stickman_images.py

## --- EXAMPLE OUTPUT ---

 Processing 2 stickman scenes
✓ pos  12  stickman waves hello
✓ pos  13  stickman sits at desk
....

Output files:

ai_output/012.png
ai_output/013.png


## --- PRE REQUISITES ---

- FAL_KEY set in .env
- ai_prompts.json with entries containing search_term, position, search_type
- Reference images at _AI_REFERENCE_IMAGES/ref1.png and ref2.png

## --- OTHER ---

Workflow:
    1. Loads environment variables from.env so fal_client can access FAL_KEY
    2. Reads ai_prompts.json and filters entries where search_type == PROCESS_TYPE ("stickman")
    3. Uploads reference images to fal once and reuses their URLs for style consistency
    4. For each target entry, calls fal-ai/flux-2/edit with:
       - prompt = "<entry['search_term']>. <STYLE_SUFFIX>"
       - image_urls = reference images
       - image_size = "square_hd"
    5. Downloads the resulting image, optionally runs deai_postprocess, and saves as
       ai_output/<position>.png using zero-padded 3-digit filenames
    6. Runs up to CONCURRENCY generations concurrently via asyncio.Semaphore
    7. Skips any position that already exists on disk

Configuration constants:
    REF_IMAGES : List of local reference images for style grounding
    PROMPTS_FILE : JSON file containing narration -> {search_term, position, search_type}
    OUT_DIR : Output directory for generated PNGs
    MODEL : fal model ID to use
    CONCURRENCY : Max parallel API calls
    PROCESS_TYPE : Filter key for prompts to process
    POSTPROCESS : Whether to run deai_postprocess before saving
    STYLE_SUFFIX : Appended to every prompt to enforce minimalist black ink stickman style
"""

import asyncio, io, pathlib, json, requests
from dotenv import load_dotenv
load_dotenv() # <- reads.env before fal_client uses FAL_KEY

import fal_client
from PIL import Image
from ai__postprocess import deai_postprocess, save_clean

REF_IMAGES = ["_AI_REFERENCE_IMAGES/ref1.png", "_AI_REFERENCE_IMAGES/ref2.png"]
PROMPTS_FILE = "ai_prompts.json"
OUT_DIR = pathlib.Path("ai_output")
MODEL = "fal-ai/flux-2/edit"
CONCURRENCY = 8
PROCESS_TYPE = "stickman"
POSTPROCESS = True
STYLE_SUFFIX = ("Minimalist black ink line art of a simple stickman character, "
                "white background, no shading, colour, "
                "consistent with reference images.")

async def generate(sem, narration, entry, ref_urls):
    """
    Generate a single stickman image for one prompt entry.

    Args:
        sem: asyncio.Semaphore limiting concurrent fal calls
        narration: Key/name from ai_prompts.json, used for logging
        entry: Dict with 'search_term', 'position', and 'search_type'
        ref_urls: List of uploaded reference image URLs for style grounding

    Behavior:
        - Skips generation if OUT_DIR/<position>.png already exists
        - Calls fal_client.subscribe_async with prompt + STYLE_SUFFIX
        - Downloads image bytes, opens with PIL, optionally postprocesses
        - Saves cleaned image to disk and logs success/failure
    """
    async with sem:
        pos = int(entry.get("position", 0))
        out = OUT_DIR / f"{pos:03d}.png"
        if out.exists():
            print(f"⤳ pos {pos} exists, skipping")
            return
        try:
            result = await fal_client.subscribe_async(MODEL, arguments={
                "prompt": f"{entry['search_term']}. {STYLE_SUFFIX}",
                "image_urls": ref_urls,
                "image_size": "square_hd",
            })
            data = await asyncio.to_thread(
                lambda: requests.get(result["images"][0]["url"], timeout=60).content
            )
            img = Image.open(io.BytesIO(data))
            if POSTPROCESS:
                img = deai_postprocess(img)
            save_clean(img, out)
            print(f"✓ pos {pos:>3} {narration[:60]}")
        except Exception as e:
            print(f"✗ pos {pos:>3} {e}")

async def main():
    """
    Orchestrate batch generation of stickman scenes.

    Steps:
        - Ensure output directory exists
        - Load and filter prompts by PROCESS_TYPE
        - Upload REF_IMAGES to fal and collect URLs
        - Launch concurrent generate() tasks for all targets
    """
    OUT_DIR.mkdir(exist_ok=True)
    data = json.loads(pathlib.Path(PROMPTS_FILE).read_text())
    targets = [(k, v) for k, v in data.items() if v.get("search_type") == PROCESS_TYPE]
    print(f"Processing {len(targets)} stickman scenes")
    ref_urls = [await asyncio.to_thread(fal_client.upload_file, p) for p in REF_IMAGES]
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*(generate(sem, k, v, ref_urls) for k, v in targets))

if __name__ == "__main__":
    asyncio.run(main())



import asyncio, io, pathlib, json, requests
from dotenv import load_dotenv
load_dotenv()                       # <- reads .env before fal_client uses FAL_KEY

import fal_client
from PIL import Image
from ai_postprocess import deai_postprocess, save_clean

REF_IMAGES   = ["_AI_REFERENCE_IMAGES/ref1.png", "_AI_REFERENCE_IMAGES/ref2.png"]
PROMPTS_FILE = "ai_prompts.json"
OUT_DIR      = pathlib.Path("ai_output")
MODEL        = "fal-ai/flux-2/edit"
CONCURRENCY  = 8
PROCESS_TYPE = "stickman"
POSTPROCESS  = True
STYLE_SUFFIX = ("Minimalist black ink line art of a simple stickman character, "
                "white background, no shading, colour, "
                "consistent with reference images.")

async def generate(sem, narration, entry, ref_urls):
    async with sem:
        pos = int(entry.get("position", 0))
        out = OUT_DIR / f"{pos:03d}.png"
        if out.exists():
            print(f"⤳ pos {pos} exists, skipping")
            return
        try:
            result = await fal_client.subscribe_async(MODEL, arguments={
                "prompt": f"{entry['search_term']}. {STYLE_SUFFIX}",
                "image_urls": ref_urls,
                "image_size": "square_hd",
            })
            data = await asyncio.to_thread(
                lambda: requests.get(result["images"][0]["url"], timeout=60).content
            )
            img = Image.open(io.BytesIO(data))
            if POSTPROCESS:
                img = deai_postprocess(img)
            save_clean(img, out)
            print(f"✓ pos {pos:>3}  {narration[:60]}")
        except Exception as e:
            print(f"✗ pos {pos:>3}  {e}")

async def main():
    OUT_DIR.mkdir(exist_ok=True)
    data = json.loads(pathlib.Path(PROMPTS_FILE).read_text())
    targets = [(k, v) for k, v in data.items() if v.get("search_type") == PROCESS_TYPE]
    print(f"Processing {len(targets)} stickman scenes")
    ref_urls = [await asyncio.to_thread(fal_client.upload_file, p) for p in REF_IMAGES]
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*(generate(sem, k, v, ref_urls) for k, v in targets))

if __name__ == "__main__":
    asyncio.run(main())

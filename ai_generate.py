


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

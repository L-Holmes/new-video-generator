

"""
example usage:

uv run edit.py output/004.png "raise the pirate's right arm above his head, sword pointing up"
uv run edit.py output/012.png "add a small parrot on his shoulder" -o output/012_parrot.png


uv run ai_edit.py ai_output/005.png "raise the sword hand" -o ai_output/005-hand-raised.png
"""

import argparse, io, pathlib, requests
from dotenv import load_dotenv
load_dotenv()

import fal_client
from PIL import Image
from ai__postprocess import deai_postprocess, save_clean

MODEL = "fal-ai/flux-2/edit"

def edit(image_path: str, prompt: str, out_path: str, postprocess: bool = False):
    print(f"Uploading {image_path}…")
    src_url = fal_client.upload_file(image_path)

    print(f"Editing: {prompt!r}")
    result = fal_client.subscribe(MODEL, arguments={
        "prompt": prompt,
        "image_urls": [src_url],
        "image_size": "square_hd",
    })

    data = requests.get(result["images"][0]["url"], timeout=60).content
    img = Image.open(io.BytesIO(data))
    if postprocess:
        img = deai_postprocess(img)
    save_clean(img, out_path)
    print(f"✓ saved {out_path}")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Edit a single image with Flux 2.")
    p.add_argument("image", help="Path to source image, e.g. output/004.png")
    p.add_argument("prompt", help="Edit instruction, e.g. 'raise the pirate's arm above his head'")
    p.add_argument("-o", "--out", help="Output path (default: <name>_edit.png)")
    p.add_argument("--no-postprocess", action="store_true")
    args = p.parse_args()

    src = pathlib.Path(args.image)
    out = args.out or str(src.with_name(f"{src.stem}_edit{src.suffix}"))
    edit(args.image, args.prompt, out, postprocess=not args.no_postprocess)

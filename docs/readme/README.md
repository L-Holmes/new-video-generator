
A fast, no-nonsense Python script that reads a text file, determines optimal pacing, pulls relevant stock footage, and stitches together a fast-paced video using FFmpeg.

## Prerequisites
1. Python 3.10+
2. [FFmpeg](https://ffmpeg.org/download.html) installed and added to your system PATH.
3. API Keys for Pexels/Pixabay.

## Setup
1. Clone this repository.
2. Create a virtual environment: 
python3 -m venv venv

3. Activate it: 
   - Windows: `venv\Scripts\activate`
   - Mac/Linux: 
jump
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
   source venv/bin/activate
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
4. Install requirements: `pip install -r requirements.txt`
5. Copy `.env` and add your API keys.
6. Create a `script.txt` file in the root directory with your video script.

## Usage
Run the main pipeline:
```bash
python main.py
```

## Testing
Run tests:
```bash
pytest test_main.py -v
```

Run full test:
```bash
python test_full_system.py
```

## 🧠 How the Generator Works

This tool is tuned for **better retention**, **better keyword extraction**, and **better stock footage relevance**.

### 2. Better Clip Timing (Retention Based)

We tuned pacing using short-form retention research.

| Setting | Value |
|--------|------|
| Words Per Minute | 130 |
| Ideal Cut Speed | 3.5 sec |
| Minimum Clip | 2.0 sec |
| Maximum Clip | 7.0 sec |

This creates faster, more modern pacing for Shorts / Reels / TikTok.

---

### 3. Cinematic Motion

Every image gets Ken Burns movement:

- smooth zoom in
- smooth zoom out
- alternating per scene

This prevents static slideshow energy.


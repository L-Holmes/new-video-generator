import json
from pathlib import Path

FILE = "CACHE-spices/stock_footage/history.json"


def method_1_read_text_utf8():
    print("\n--- Method 1: read_text utf-8 ---")
    try:
        text = Path(FILE).read_text(encoding="utf-8")
        data = json.loads(text)
        print("✅ Success (utf-8)")
    except Exception as e:
        print("❌ Failed:", repr(e))


def method_2_read_text_ignore_errors():
    print("\n--- Method 2: utf-8 with ignore errors ---")
    try:
        text = Path(FILE).read_text(encoding="utf-8", errors="ignore")
        data = json.loads(text)
        print("✅ Success (utf-8 ignore)")
    except Exception as e:
        print("❌ Failed:", repr(e))


def method_3_read_binary_then_decode():
    print("\n--- Method 3: binary + manual decode ---")
    try:
        raw = Path(FILE).read_bytes()
        text = raw.decode("utf-8", errors="replace")
        data = json.loads(text)
        print("✅ Success (binary decode)")
    except Exception as e:
        print("❌ Failed:", repr(e))


def method_4_try_latin1():
    print("\n--- Method 4: latin-1 fallback ---")
    try:
        text = Path(FILE).read_text(encoding="latin-1")
        data = json.loads(text)
        print("✅ Success (latin-1)")
    except Exception as e:
        print("❌ Failed:", repr(e))


def method_5_plain_open():
    print("\n--- Method 5: open() default ---")
    try:
        with open(FILE, "r") as f:
            data = json.load(f)
        print("✅ Success (default open)")
    except Exception as e:
        print("❌ Failed:", repr(e))


def inspect_raw_bytes():
    print("\n--- Inspect first 50 bytes ---")
    raw = Path(FILE).read_bytes()
    print(raw[:50])


if __name__ == "__main__":
    inspect_raw_bytes()
    method_1_read_text_utf8()
    method_2_read_text_ignore_errors()
    method_3_read_binary_then_decode()
    method_4_try_latin1()
    method_5_plain_open()

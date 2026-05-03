import os
import sys
import shutil
from pathlib import Path
from dotenv import load_dotenv
import main
import shutil

SCRIPTS = [
    "The Golden Gate Bridge is an iconic marvel of engineering in San Francisco.",
    "Bioluminescent creatures light up the deep sea in mysterious ways.",
    "Artificial intelligence is transforming software development and art.",
    "Traditional Italian carbonara requires guanciale and fresh pecorino.",
    "SpaceX is building rockets to make humans a multi-planetary species.",
    "Cybersecurity protocols are vital for protecting modern financial data.",
    "Tech stocks saw a significant rally during the last fiscal quarter.",
    "Sustainable vertical farming is the future of urban agriculture.",
    "High intensity interval training is effective for cardiovascular health.",
    "Ancient Egyptian pyramids remain one of history's greatest mysteries."
]

def test_all_scripts():
    load_dotenv()
    if "--debug" in sys.argv:
        main.DEBUG = True
    
    print("Starting all scripts test...")
    
    # Pre-check environment
    try:
        print("Verifying environment...")
        main.verify_environment()
        print("Environment all good")
    except SystemExit:
        print("Env check failed. Check your PEXELS_API_KEY.")
        return

    print("Starting loop...")
    successes = 0
    for i, script_x in enumerate(SCRIPTS, 1):
        print(f"\n--- TEST {i}: {script_x[:40]}... ---")

        file_path = Path("script.txt")
        backup_path = Path("script_backup.txt")

        # --- Make backup ---
        if file_path.exists():
            print("Copying existing script to backup...")
            shutil.copy(file_path, backup_path)
            print("Copied.")

        # --- Overwrite file ---
        print("Writing the script to the input text file...")
        file_path.write_text(script_x)
        print("Written.")
        
        try:
            print("Removing existing output dir content...")
            if Path("output").exists(): shutil.rmtree("output")
            print("Removed.")
            
            print("Processing main script...")
            processed = main.process_script(script_x)
            print("Processed.")
            print("Getting clips...")
            images = main.get_clips(processed)
            print("Got clips.")
            
            if not images:
                print("   [!] No images found (likely 24h filter skip).")
                continue
                
            print("Stitching video...")
            main.stitch_video(images, processed)
            print("Stitched.")
            
            if Path("output/final_video.mp4").exists():
                print(f"   [tick] Success! Duration: {processed['duration_sec']:.1f}s")
                successes += 1
            
        except Exception as e:
            print(f"   [FAIL] Error during processing: {e}")
            if main.DEBUG: raise e


        # --- Restore original later ---
        if backup_path.exists():
            print("Restoring original...")
            original_text = backup_path.read_text()
            file_path.write_text(original_text)
            backup_path.unlink()  # delete backup
            print("Restored.")


    print(f"\nBATTERY COMPLETE: {successes}/{len(SCRIPTS)} PASSED")
    if Path("script.txt").exists(): Path("script.txt").unlink()

if __name__ == "__main__":
    test_all_scripts()

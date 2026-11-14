#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║               AUTO VIDEO EDITOR — Watch Folder Daemon                ║
║                                                                      ║
║  Monitors the 'input/' directory. Whenever a video is placed inside: ║
║    1. Detects matching .txt file for caption (or uses config default)║
║    2. Waits for file write to complete                              ║
║    3. Automatically edits video with GPU NVENC acceleration         ║
║    4. Saves output to 'output/<name>_edited.mp4'                     ║
║    5. Moves original to 'input/processed/'                          ║
╚══════════════════════════════════════════════════════════════════════╝

Usage:
    python watch_folder.py
    python watch_folder.py --config config.json
"""

import sys
import os
import time
import shutil
import subprocess
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.m4v'}


def is_file_ready(filepath, wait_seconds=1.5):
    """Check if file copy/download has finished by tracking file size stability."""
    try:
        size1 = os.path.getsize(filepath)
        time.sleep(wait_seconds)
        size2 = os.path.getsize(filepath)
        return size1 == size2 and size1 > 0
    except OSError:
        return False


def process_video(video_path, input_dir, output_dir, processed_dir, config_path):
    video_path = Path(video_path)
    print(f"\n✨ New video detected: {video_path.name}")

    # Find caption
    caption_txt = video_path.with_suffix('.txt')
    caption = None
    if caption_txt.exists():
        try:
            caption = caption_txt.read_text(encoding='utf-8').strip()
            print(f"   📄 Found caption file: '{caption}'")
        except Exception:
            pass

    cmd = [
        sys.executable, 'video_editor.py',
        str(video_path),
        '--config', str(config_path),
    ]

    if caption:
        cmd.extend(['--caption', caption])
    else:
        # Default caption if no .txt file provided
        cmd.extend(['--caption', ""])

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(cmd, env=env)

    # Move processed file
    dest = processed_dir / video_path.name
    try:
        shutil.move(str(video_path), str(dest))
        if caption_txt.exists():
            shutil.move(str(caption_txt), str(processed_dir / caption_txt.name))
        print(f"   📦 Moved original to input/processed/{video_path.name}")
    except Exception as e:
        print(f"   ⚠ Could not move to processed folder: {e}")


def main():
    base_dir = Path(__file__).parent.resolve()
    input_dir = base_dir / 'input'
    output_dir = base_dir / 'output'
    processed_dir = input_dir / 'processed'

    input_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    processed_dir.mkdir(exist_ok=True)

    config_path = base_dir / 'config.json'

    print("=" * 60)
    print("  WATCH FOLDER DAEMON — Auto Video Editor")
    print(f"  Watching: {input_dir}")
    print(f"  Outputs : {output_dir}")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    print("\n👀 Waiting for videos in 'input/'...")

    try:
        while True:
            for item in sorted(input_dir.iterdir()):
                if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
                    if is_file_ready(item):
                        process_video(item, input_dir, output_dir, processed_dir, config_path)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n🛑 Watcher stopped by user.")


if __name__ == '__main__':
    main()


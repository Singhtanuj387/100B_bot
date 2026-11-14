#!/usr/bin/env python3
"""
Batch Video Editor — Process multiple videos from a folder.

Usage:
    python batch_edit.py input_folder/ --captions captions.json
    python batch_edit.py input_folder/ --caption "Same caption for all"

captions.json format:
{
    "video1.mp4": "Caption for video 1",
    "video2.mp4": "Caption for video 2"
}
"""

import sys
import os
import json
import argparse
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Supported video extensions
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.m4v'}


def process_single(video_path, caption, config_path, output_dir):
    """Process a single video by calling the main editor."""
    import subprocess
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable, 'video_editor.py',
        str(video_path),
        '--caption', caption,
        '--config', config_path,
        '--output', str(output_dir / f"{video_path.stem}_edited.mp4")
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return video_path.name, result.returncode == 0, result.stdout, result.stderr


def main():
    parser = argparse.ArgumentParser(description='Batch Video Editor')
    parser.add_argument('input_dir', help='Folder containing input videos')
    parser.add_argument('--caption', '-c', help='Caption for all videos')
    parser.add_argument('--captions', help='JSON file mapping video names to captions')
    parser.add_argument('--config', default='config.json', help='Config file path')
    parser.add_argument('--output', '-o', help='Output directory (default: output/)')
    parser.add_argument('--workers', '-w', type=int, default=2,
                        help='Parallel workers (default: 2)')
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        print(f"❌ Not a directory: {input_dir}")
        sys.exit(1)

    # Discover videos
    videos = sorted(
        f for f in input_dir.iterdir()
        if f.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not videos:
        print(f"❌ No video files found in {input_dir}")
        sys.exit(1)

    # Load captions
    captions_map = {}
    if args.captions:
        with open(args.captions, 'r', encoding='utf-8') as f:
            captions_map = json.load(f)

    # Output directory
    output_dir = Path(args.output) if args.output else Path('output')
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'=' * 60}")
    print(f"  BATCH VIDEO EDITOR")
    print(f"  Found {len(videos)} video(s) in {input_dir.name}/")
    print(f"{'=' * 60}\n")

    start = time.time()
    results = []

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for video in videos:
            caption = captions_map.get(video.name, args.caption or 'No caption')
            future = pool.submit(
                process_single, video, caption,
                args.config, output_dir
            )
            futures[future] = video.name

        for future in as_completed(futures):
            name, success, stdout, stderr = future.result()
            status = "✅" if success else "❌"
            print(f"  {status} {name}")
            results.append((name, success))

    elapsed = time.time() - start
    success_count = sum(1 for _, s in results if s)

    print(f"\n{'=' * 60}")
    print(f"  Done! {success_count}/{len(results)} processed in {elapsed:.1f}s")
    print(f"  Output: {output_dir.resolve()}")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()


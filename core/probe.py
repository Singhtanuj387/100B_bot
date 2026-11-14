import json
import subprocess
from pathlib import Path


def detect_crop_black_bars(video_path, max_frames=25):
    """
    Uses FFmpeg's cropdetect filter to detect baked-in letterbox or pillarbox black bars.
    Returns dict: {'w': int, 'h': int, 'x': int, 'y': int, 'filter': 'crop=w:h:x:y'}
    or None if no significant black bars exist.
    """
    try:
        import re
        cmd = [
            "ffmpeg", "-hide_banner",
            "-ss", "0.5",
            "-i", str(video_path),
            "-vf", "cropdetect=24:16:0",
            "-vframes", str(max_frames),
            "-f", "null", "-"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        matches = re.findall(r"crop=([0-9]+):([0-9]+):([0-9]+):([0-9]+)", res.stderr)
        if not matches:
            return None
        w, h, x, y = map(int, matches[-1])
        # Force even dimensions for H.264 compatibility
        w -= w % 2
        h -= h % 2
        x -= x % 2
        y -= y % 2
        return {"w": w, "h": h, "x": x, "y": y, "filter": f"crop={w}:{h}:{x}:{y}"}
    except Exception:
        return None


def get_video_metadata(video_path, auto_crop=True):
    """
    Query video metadata using ffprobe.
    Returns:
        dict: {
            'width': int,
            'height': int,
            'fps': float,
            'duration': float,
            'is_portrait': bool,
            'has_audio': bool,
            'codec_name': str,
            'rotation': int,
            'crop_filter': Optional[str],
            'crop_w': Optional[int],
            'crop_h': Optional[int]
        }
    """
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {res.stderr.strip()}")

    data = json.loads(res.stdout)

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream["width"])
            height = int(stream["height"])

            # Handle rotation metadata (legacy tags and modern display matrix)
            rotation = 0
            tags = stream.get("tags", {})
            if "rotate" in tags:
                try:
                    rotation = int(tags["rotate"])
                except ValueError:
                    pass

            for sd in stream.get("side_data_list", []):
                if sd.get("side_data_type") == "Display Matrix":
                    try:
                        rotation = abs(int(sd.get("rotation", 0)))
                    except ValueError:
                        pass

            if rotation in (90, 270):
                width, height = height, width

            # Parse framerate
            fps_str = stream.get("r_frame_rate", "30/1")
            if "/" in fps_str:
                n, d = fps_str.split("/")
                fps = float(n) / float(d) if float(d) != 0 else 30.0
            else:
                fps = float(fps_str)

            duration = float(data.get("format", {}).get("duration", 0.0))
            has_audio = any(
                s.get("codec_type") == "audio" for s in data.get("streams", [])
            )

            info = {
                "width": width,
                "height": height,
                "fps": fps,
                "duration": duration,
                "is_portrait": height > width,
                "has_audio": has_audio,
                "codec_name": stream.get("codec_name", "unknown"),
                "rotation": rotation,
            }

            if auto_crop:
                crop = detect_crop_black_bars(path)
                # Only apply if crop removes at least 16px of horizontal or vertical black bars
                if crop and (crop["w"] <= width - 16 or crop["h"] <= height - 16):
                    info["crop_filter"] = crop["filter"]
                    info["crop_w"] = crop["w"]
                    info["crop_h"] = crop["h"]
                    info["is_portrait"] = crop["h"] > crop["w"]

            return info

    raise ValueError(f"No video stream found in: {path}")


import subprocess
import time
import sys
from pathlib import Path


_NVENC_AVAILABLE = None


def is_nvenc_available():
    """Check if NVIDIA NVENC hardware encoder is usable on the system."""
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is not None:
        return _NVENC_AVAILABLE

    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
            "-c:v", "h264_nvenc", "-f", "null", "-"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        _NVENC_AVAILABLE = (res.returncode == 0)
    except Exception:
        _NVENC_AVAILABLE = False

    return _NVENC_AVAILABLE


def build_ffmpeg_command(input_path, bg_path, placement, video_info, output_path, config):
    """
    Construct the FFmpeg command string and arguments for compositing.
    Supports auto-switching between NVIDIA NVENC and CPU libx264.
    """
    encoding_cfg = config.get("encoding", {})
    gpu_mode = encoding_cfg.get("gpu", "auto")

    # Check hardware encoding capability
    use_nvenc = False
    if gpu_mode in ("auto", "nvenc"):
        use_nvenc = is_nvenc_available()
        if gpu_mode == "nvenc" and not use_nvenc:
            print("  ⚠ NVENC was explicitly requested but is unavailable; falling back to CPU.")

    vp = placement
    crop_f = video_info.get("crop_filter")
    if crop_f:
        scale_input = f"[1:v]{crop_f},scale={vp['w']}:{vp['h']}:flags=lanczos[scaled]"
    else:
        scale_input = f"[1:v]scale={vp['w']}:{vp['h']}:flags=lanczos[scaled]"

    filter_complex = (
        f"{scale_input};"
        f"[0:v][scaled]overlay={vp['x']}:{vp['y']}:shortest=1[out]"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-loop", "1",
        "-framerate", str(min(video_info.get("fps", 30.0), 60)),
        "-i", str(bg_path),
        "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "1:a?",  # Keep audio if present
    ]

    if use_nvenc:
        preset = encoding_cfg.get("nvenc_preset", "p4")
        cq = str(encoding_cfg.get("nvenc_cq", 19))
        cmd.extend([
            "-c:v", "h264_nvenc",
            "-preset", preset,
            "-cq", cq,
        ])
        encoder_label = f"NVIDIA NVENC (GPU) | Preset: {preset} | CQ: {cq}"
    else:
        preset = encoding_cfg.get("cpu_preset", "fast")
        crf = str(encoding_cfg.get("cpu_crf", 18))
        cmd.extend([
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", crf,
        ])
        encoder_label = f"libx264 (CPU) | Preset: {preset} | CRF: {crf}"

    audio_codec = encoding_cfg.get("audio_codec", "aac")
    audio_bitrate = encoding_cfg.get("audio_bitrate", "192k")

    cmd.extend([
        "-pix_fmt", "yuv420p",
        "-c:a", audio_codec,
        "-b:a", audio_bitrate,
        "-shortest",
        "-movflags", "+faststart",
        str(output_path)
    ])

    return cmd, encoder_label


def compose_video(input_path, bg_path, placement, video_info, output_path, config):
    """
    Overlays the video onto the template background using FFmpeg.
    Measures and logs encoding duration and real-time speed.
    """
    cmd, encoder_label = build_ffmpeg_command(
        input_path, bg_path, placement, video_info, output_path, config
    )

    vp = placement
    print(f"\n🎬 Encoding video...")
    print(f"   Encoder: {encoder_label}")
    print(f"   Placement: {vp['w']}×{vp['h']} at ({vp['x']}, {vp['y']})")

    start_time = time.time()
    process = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start_time

    if process.returncode != 0:
        print(f"\n❌ FFmpeg encoding failed!")
        err_lines = process.stderr.strip().split("\n")
        for line in err_lines[-10:]:
            print(f"   {line}")
        sys.exit(1)

    speed_x = (video_info.get("duration", 0) / elapsed) if elapsed > 0 else 0
    print(f"   ⏱ Encoded in {elapsed:.1f}s ({speed_x:.1f}x real-time)")
    return output_path


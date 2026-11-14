# 🎬 Auto Video Editor — Automated Channel Branding & Framing

A modular, high-performance automated video editing suite built with **Python**, **Pillow**, and **FFmpeg**. Automatically adds social channel branding (circular logo, bold channel name, verified checkmark badge, `@handle`, and multi-line caption with full-color emojis) to portrait and landscape videos with zero quality degradation and hardware-accelerated speeds.

---

## 📁 Project Architecture

```
project/
  main.py            # CLI entry point & interactive workflow
  config.yaml        # Layout, colors, fonts, and channel branding settings
  core/
    __init__.py      # Package exports
    probe.py         # ffprobe wrapper for metadata & rotation analysis
    header.py        # Pillow template layout & caption rendering
    compose.py       # FFmpeg filter-complex builder & encoding runner
    utils.py         # Path helpers, font loader, circular image mask & text wrapper
  assets/            # Logo (a-logo.png), verified badge icon, custom fonts
  templates/         # Reference template screenshots (portrait & landscape)
  output/            # Destination directory for edited videos
  requirements.txt   # Python package dependencies
  README.md          # Full project documentation & guide
```

---

## ⚡ Key Capabilities

- **Auto Orientation Detection**: Automatically inspects video aspect ratio:
  - **Portrait (9:16 / 4:5)**: Header is pinned near the top; video scales and frames cleanly below.
  - **Landscape (16:9)**: Header, caption, and video are grouped together and **vertically centered** on the 9:16 canvas (matching viral meme/repost formats).
- **Pixel-Calibrated Typography**: Matches Twitter / X mobile typography with bold channel name, secondary gray handle, and full-color emoji rendering (`😂`, `🔥`, etc.) via Windows Segoe UI Emoji.
- **Hardware Acceleration**: Automatic NVIDIA NVENC (`h264_nvenc`) GPU detection delivering **25x–27x real-time encoding** (over 1,600 FPS) on NVIDIA RTX/GTX GPUs, with fallback to multi-threaded CPU `libx264`.
- **Fault-Tolerant Path Handling**: CLI automatically stitches unquoted Windows paths containing spaces (e.g. `C:\Drive-D\python tut\100B\video.mp4`).
- **Companion File Auto-Caption**: If `video.txt` exists alongside `video.mp4`, the caption is read automatically without typing command-line flags.

---

## 🚀 Installation & Prerequisites

### 1. Requirements
- **Python**: 3.10+
- **FFmpeg & FFprobe**: Ensure `ffmpeg` and `ffprobe` are installed and added to system `PATH`.
- **GPU (Optional)**: NVIDIA RTX/GTX graphics card for NVENC hardware encoding.

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

---

## 🛠 Usage Guide

### Basic Command
```bash
python main.py video.mp4 --caption "How does your brain come up with this stuff? 😂"
```

### Auto-Detect Caption from `.txt` Companion File
If you have `video.mp4` and `video.txt` in the same directory:
```bash
python main.py video.mp4
```

### Full Path with Spaces (Quotes Optional)
```bash
python main.py "C:\Drive-D\python tut\100B\video-1.mp4" -c "Bro trying not to laugh 😂"
```

### Interactive Mode
Run without arguments to trigger the interactive drag-and-drop prompt:
```bash
python main.py
```

### Overriding Settings via CLI
```bash
python main.py video.mp4 -c "Caption" --name "My Channel" --handle "myhandle" --logo assets/custom_logo.png --gpu nvenc --preset p4
```

---

## ⚙️ Configuration (`config.yaml`)

All visual styling, layout metrics, colors, and encoding presets are centralized in `config.yaml`:

```yaml
channel:
  name: "Cheerful Videos"
  handle: "Cheerfulvids"
  logo_path: "assets/a-logo.png"
  badge_path: "assets/badge.png"
  verified: true

canvas:
  width: 1080
  height: 1920
  bg_color: [255, 255, 255]

layout:
  padding_left: 72               # Left padding for avatar and caption
  padding_top_portrait: 160      # Top padding for portrait template
  avatar_size: 124               # Avatar circle diameter
  logo_name_gap: 26              # Gap between logo and channel name
  header_caption_gap: 58         # Gap between handle and caption text
  caption_video_gap: 46          # Gap between caption and video
  video_side_margin: 28          # Outer side margin framing the video
  bottom_buffer_portrait: 60     # Minimum bottom margin in portrait mode

colors:
  text: [15, 20, 25]             # Dark text
  handle: [83, 100, 113]         # Secondary gray
  verified: [29, 155, 240]       # Twitter/X verification blue

typography:
  font_size_name: 44
  font_size_handle: 35
  font_size_caption: 44
  caption_line_spacing: 16
  font_bold: "arialbd.ttf"
  font_regular: "arial.ttf"
  font_emoji: "seguiemj.ttf"

encoding:
  gpu: "auto"                    # auto, nvenc, or cpu
  cpu_preset: "fast"
  cpu_crf: 18
  nvenc_preset: "p4"
  nvenc_cq: 19
  audio_codec: "aac"
  audio_bitrate: "192k"
```

---

## 🖥 Windows Drag & Drop Helper

Double-click or drag-and-drop any video onto [`run.bat`](file:///c:/Drive-D/python%20tut/100B/run.bat) to launch the editor directly in Windows File Explorer.


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

## 🤖 Phase 3.6: Automated Video Curation Agent & Smarter Supply

The repository includes a LangGraph-orchestrated daily curation pipeline with automated discovery, two-step ingestion, Vision LLM evaluation, and rank-based daily scheduling:

```
                  ┌──────────────────────┐
                  │ Loop 1: Discovery    │
                  └──────────┬───────────┘
                             ▼
┌──────────────┐  Loop 2/3   ┌──────────────────────┐
│ Adaptive     │◄────────────┤ Source Registry      │
│ Deeper Pages │             │ (80/20 Bandit)       │
└──────────────┘             └──────────┬───────────┘
                                        ▼
                             ┌──────────────────────┐
                             │ Pre-Download Filter  │ 5s-90s Duration Filter & Remote ffprobe
                             └──────────┬───────────┘
                                        ▼
                             ┌──────────────────────┐
                             │ Title Pre-Screen     │ Zero-Cost Fast LLM
                             └──────────┬───────────┘
                                        ▼
                             ┌──────────────────────┐
                             │ Parallel Downloader  │ Deduplication & Perceptual Hash
                             └──────────┬───────────┘
                                        ▼
                             ┌──────────────────────┐
                             │ Vision LLM Judge     │ Gemma / Gemini Vision
                             └──────────┬───────────┘
                                        ▼
                             ┌──────────────────────┐
                             │ Rank-Based Decide    │ Daily Top-6 (Score >= 6.5)
                             └──────────┬───────────┘
                                        ▼
                             ┌──────────────────────┐
                             │ Borderline Policy    │ approve_top / review / reject
                             └──────────┬───────────┘
                                        ▼
                             ┌──────────────────────┐
                             │ 6 Peak Hourly Slots  │ 12:00, 14:00, 17:00, 19:00, 20:00, 21:00
                             └──────────────────────┘
```

### Key Capabilities in Phase 3.6:
1. **Duration Filtering Prior to Download**:
   - `min_duration_seconds: 5.0` and `max_duration_seconds: 90.0` defined in `scraper/config.yaml` as single source of truth and synced by the agent.
   - Known Reddit video durations from JSON/PRAW metadata are filtered in `--list-only` mode before downloading bytes.
   - Unknown durations (e.g. RSS feeds) are probed via lightweight stream URL `ffprobe` inspection prior to full file download.
   - Hard ceiling: Any clip over 180 seconds is rejected unconditionally (YouTube Shorts limit).
2. **`after` Cursor Pagination**:
   - For `top_week`, `top_month`, `top_year`, `top_all` and search queries, queries fetch `limit=100` and pass `after=<cursor>`.
   - The cursor is automatically stored in the `sources` registry table and resumed across runs.
   - `hot` listings never use a cursor (they start from the top).
   - If a source yields 3 consecutive empty pages, it is marked `exhausted` and automatically reset after 7 days.
3. **Daily Top-6 Selection by Rank**:
   - Daily target defaults to **6 videos** (matching channel daily upload quotas).
   - Evaluated clips are ranked by composite hybrid score (LLM humor + normalized engagement). Rank ties are broken by raw engagement, then recency (`created_utc`).
   - Top 6 meeting `min_publish_score: 6.5` are auto-approved.
   - Clips between 5.0 and the lowest chosen score enter the borderline pool.
   - `--borderline-policy`:
     - `approve_top`: Fills remaining slots from best borderline clips meeting `min_publish_score: 6.5`. (Default for unattended `--no-review` runs).
     - `review`: Sends borderline candidates to interactive review. (Default for interactive runs).
     - `reject`: Drops all borderline candidates.
   - Safety exclusions (watermarks, ads-unsafe, copyrighted broadcasts, non-English) are permanently rejected regardless of score.
   - When fewer than 6 good clips exist, empty slots are logged and scheduled without lowering the quality bar.
4. **Peak Hours Scheduling**:
   - 6 daily slots are scheduled across channel peak engagement hours: **12:00, 14:00, 17:00, 19:00, 20:00, 21:00** EST.

### Curation CLI Usage:
```bash
# Production daily curation run (Target: 6 slots, unattended auto-approval)
python agent/curate.py --target 6 --date tomorrow --no-review

# Simulation mode (No API charges, mock evaluation)
python agent/curate.py --target 6 --date tomorrow --dry-run

# Custom borderline policy
python agent/curate.py --target 6 --date tomorrow --no-review --borderline-policy approve_top
```

---

## 🖥 Windows Drag & Drop Helper

Double-click or drag-and-drop any video onto [`run.bat`](file:///c:/Drive-D/python%20tut/100B/run.bat) to launch the editor directly in Windows File Explorer.


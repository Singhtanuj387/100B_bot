import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from core.utils import load_font, create_circular_image, wrap_text


def load_typography(config):
    """Load bold, regular, and emoji-capable fonts based on config."""
    typo = config.get("typography", {})
    name_size = typo.get("font_size_name", 44)
    handle_size = typo.get("font_size_handle", 35)
    cap_size = typo.get("font_size_caption", 44)

    font_bold = typo.get("font_bold", "arialbd.ttf")
    font_reg = typo.get("font_regular", "arial.ttf")
    font_emoji = typo.get("font_emoji", "seguiemj.ttf")

    name_font = load_font(font_bold, name_size)
    handle_font = load_font(font_reg, handle_size)

    # Use Segoe UI Emoji on Windows for native color emojis (😂, 🔥)
    cap_font = None
    if os.path.exists(f"C:/Windows/Fonts/{font_emoji}"):
        try:
            cap_font = ImageFont.truetype(f"C:/Windows/Fonts/{font_emoji}", cap_size)
        except Exception:
            pass
    if cap_font is None:
        cap_font = load_font(font_reg, cap_size)

    return {
        "name_bold": name_font,
        "handle": handle_font,
        "caption": cap_font,
    }


def draw_verified_badge(canvas, draw, cx, cy, radius=18, badge_path=None):
    """Draw or paste verified checkmark badge."""
    if badge_path and os.path.isfile(badge_path):
        try:
            badge_img = Image.open(badge_path).convert("RGBA")
            badge_img = badge_img.resize((radius * 2, radius * 2), Image.LANCZOS)
            canvas.paste(badge_img, (cx - radius, cy - radius), badge_img)
            return
        except Exception:
            pass

    # Fallback: draw Twitter blue badge
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=(29, 155, 240)
    )
    r = radius
    points = [
        (cx - r * 0.42, cy + r * 0.05),
        (cx - r * 0.10, cy + r * 0.42),
        (cx + r * 0.45, cy - r * 0.35),
    ]
    draw.line(points, fill="white", width=4)


def measure_branding(config, fonts, caption):
    """Calculate the total vertical height of [Header + Caption]."""
    layout = config.get("layout", {})
    canvas_cfg = config.get("canvas", {})
    canvas_w = canvas_cfg.get("width", 1080)
    padding_left = layout.get("padding_left", 72)
    avatar_size = layout.get("avatar_size", 124)
    header_gap = layout.get("header_caption_gap", 58)
    line_spacing = config.get("typography", {}).get("caption_line_spacing", 16)
    cap_size = config.get("typography", {}).get("font_size_caption", 44)

    total_h = avatar_size
    if caption:
        tmp = Image.new("RGB", (canvas_w, 200), (255, 255, 255))
        draw = ImageDraw.Draw(tmp)
        max_w = canvas_w - 2 * padding_left
        lines = wrap_text(caption, fonts["caption"], max_w, draw)
        caption_h = len(lines) * (cap_size + line_spacing)
        total_h += header_gap + caption_h

    return total_h


def render_branding(canvas, config, fonts, start_y, caption):
    """
    Renders avatar, channel name, verified badge, handle, and caption.
    Returns the bottom Y coordinate after the caption.
    """
    draw = ImageDraw.Draw(canvas)
    layout = config.get("layout", {})
    colors = config.get("colors", {})
    channel = config.get("channel", {})
    canvas_cfg = config.get("canvas", {})
    typo = config.get("typography", {})

    canvas_w = canvas_cfg.get("width", 1080)
    padding_left = layout.get("padding_left", 72)
    avatar_size = layout.get("avatar_size", 124)
    logo_gap = layout.get("logo_name_gap", 26)
    header_gap = layout.get("header_caption_gap", 58)
    line_spacing = typo.get("caption_line_spacing", 16)
    cap_size = typo.get("font_size_caption", 44)

    text_color = tuple(colors.get("text", [15, 20, 25]))
    handle_color = tuple(colors.get("handle", [83, 100, 113]))

    # 1. Avatar (circular crop)
    project_root = Path(__file__).parent.parent.resolve()
    logo_x = padding_left
    logo_y = start_y
    logo_path = channel.get("logo_path", "")
    if logo_path and not os.path.isabs(logo_path):
        cand = project_root / logo_path
        if cand.is_file():
            logo_path = str(cand)

    if logo_path and os.path.isfile(logo_path):
        try:
            avatar = create_circular_image(logo_path, avatar_size)
            canvas.paste(avatar, (logo_x, logo_y), avatar)
        except Exception as e:
            print(f"  ⚠ Failed to load logo ({e}), drawing placeholder")
            draw.ellipse(
                (logo_x, logo_y, logo_x + avatar_size, logo_y + avatar_size),
                fill="#E0E0E0", outline="#BDBDBD", width=2
            )
    else:
        draw.ellipse(
            (logo_x, logo_y, logo_x + avatar_size, logo_y + avatar_size),
            fill="#E0E0E0", outline="#BDBDBD", width=2
        )

    # 2. Channel Name (Bold)
    name_text = channel.get("name", "LaughVault")
    text_x = logo_x + avatar_size + logo_gap
    name_y = logo_y + 12
    draw.text((text_x, name_y), name_text, fill=text_color, font=fonts["name_bold"])

    # 3. Verified Badge (defaults to False to avoid misleading fake badges)
    name_bbox = draw.textbbox((text_x, name_y), name_text, font=fonts["name_bold"])
    badge_cx = name_bbox[2] + 18
    badge_cy = name_y + (name_bbox[3] - name_bbox[1]) // 2
    if channel.get("verified", False):
        badge_path = channel.get("badge_path")
        if badge_path and not os.path.isabs(badge_path):
            cand_badge = project_root / badge_path
            if cand_badge.is_file():
                badge_path = str(cand_badge)
        draw_verified_badge(canvas, draw, badge_cx, badge_cy, radius=18, badge_path=badge_path)

    # 4. Handle (@handle)
    handle_text = channel.get("handle", "laughvault").lstrip("@")
    handle_y = name_y + 50
    draw.text((text_x, handle_y), f"@{handle_text}", fill=handle_color, font=fonts["handle"])

    # 5. Caption (Supports full-color emojis)
    cap_y = logo_y + avatar_size + header_gap
    if caption:
        max_w = canvas_w - 2 * padding_left
        lines = wrap_text(caption, fonts["caption"], max_w, draw)
        for line in lines:
            try:
                draw.text(
                    (padding_left, cap_y), line,
                    fill=text_color, font=fonts["caption"], embedded_color=True
                )
            except Exception:
                draw.text(
                    (padding_left, cap_y), line,
                    fill=text_color, font=fonts["caption"]
                )
            cap_y += cap_size + line_spacing

    return cap_y


def generate_template(config, video_info, caption, temp_dir):
    """
    Generate background template image and compute video placement coordinates.
    Returns:
        (bg_image_path, placement_dict)
    """
    fonts = load_typography(config)
    canvas_cfg = config.get("canvas", {})
    layout = config.get("layout", {})

    canvas_w = canvas_cfg.get("width", 1080)
    canvas_h = canvas_cfg.get("height", 1920)
    bg_color = tuple(canvas_cfg.get("bg_color", [255, 255, 255]))

    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)

    vw = video_info.get("crop_w", video_info["width"])
    vh = video_info.get("crop_h", video_info["height"])
    side_margin = layout.get("video_side_margin", 28)
    max_video_w = canvas_w - 2 * side_margin
    caption_gap = layout.get("caption_video_gap", 46)

    if video_info["is_portrait"]:
        # ─── PORTRAIT LAYOUT ──────────────────────────────────────────────
        # Header near the top, video frames in remaining space below
        start_y = layout.get("padding_top_portrait", 160)
        caption_bottom = render_branding(canvas, config, fonts, start_y, caption)

        avail_y = caption_bottom + caption_gap
        avail_h = canvas_h - avail_y - layout.get("bottom_buffer_portrait", 60)
        avail_w = max_video_w

        scale = min(avail_w / vw, avail_h / vh)
        scaled_w = int(vw * scale)
        scaled_h = int(vh * scale)

        video_x = (canvas_w - scaled_w) // 2
        video_y = avail_y + (avail_h - scaled_h) // 2

    else:
        # ─── LANDSCAPE LAYOUT ─────────────────────────────────────────────
        # Header + caption + landscape video are vertically centered together
        scale = max_video_w / vw
        scaled_w = max_video_w
        scaled_h = int(vh * scale)

        header_block_h = measure_branding(config, fonts, caption)
        total_stack_h = header_block_h + caption_gap + scaled_h

        start_y = max(60, (canvas_h - total_stack_h) // 2)
        caption_bottom = render_branding(canvas, config, fonts, start_y, caption)

        video_x = side_margin
        video_y = caption_bottom + caption_gap

    # Ensure even dimensions for H.264 encoder compatibility
    scaled_w -= scaled_w % 2
    scaled_h -= scaled_h % 2
    video_x = max(0, video_x - (video_x % 2))
    video_y = max(0, video_y - (video_y % 2))

    os.makedirs(temp_dir, exist_ok=True)
    bg_path = os.path.join(temp_dir, "_bg_template.png")
    canvas.save(bg_path, "PNG", optimize=True)

    placement = {
        "x": video_x,
        "y": video_y,
        "w": scaled_w,
        "h": scaled_h,
    }
    return bg_path, placement


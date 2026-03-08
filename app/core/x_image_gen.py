import os
import logging
import textwrap
import math
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

# Configure module logger
logger = logging.getLogger(__name__)

def generate_x_image(trend_id, headline, short_summary, tps_value):
    """
    Generates an animated GIF for X (Twitter) to increase dwell time and engagement.
    
    Args:
        trend_id (int): The ID of the trend (used for filename).
        headline (str): The main headline text.
        short_summary (str): The short summary text for the body.
        tps_value (str/float): The TPS score to display.
        
    Returns:
        str: The relative web path to the generated GIF, or None if failed.
    """
    try:
        # --- 1. Path Configuration ---
        # Use absolute paths for Docker environment consistency
        assets_dir = "/app/app/static/assets"
        output_dir = "/app/app/static/media/x_drafts"
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # --- 2. Load Assets ---
        try:
            base = Image.open(os.path.join(assets_dir, "BackGround.png")).convert("RGBA")
            fire_icon = Image.open(os.path.join(assets_dir, "FireLogo.png")).convert("RGBA")
            ai_icon = Image.open(os.path.join(assets_dir, "ai.png")).convert("RGBA")
        except FileNotFoundError as e:
            logger.error(f"Asset missing for X image generation: {e}")
            return None

        total_width, total_height = base.size
        
        FIRE_GREEN = (145, 220, 90)

        # --- 3. Load Fonts ---
        try:
            font_headline = ImageFont.truetype(os.path.join(assets_dir, "Roboto-Bold.ttf"), 67)
            font_body = ImageFont.truetype(os.path.join(assets_dir, "Roboto-Regular.ttf"), 37)
            font_tps = ImageFont.truetype(os.path.join(assets_dir, "Roboto-Bold.ttf"), 43)
        except OSError:
            logger.warning("Custom fonts not found in assets. Using default font.")
            font_headline = font_body = font_tps = ImageFont.load_default()

        # --- 4. Layout Logic ---
        
        # Horizontal coordinates
        box_x_start = 160
        box_x_end = 1440
        
        # AI Icon
        ai_icon_size = 110
        # AI Icon -> Right Side
        ai_x_pos = box_x_end - 40 - ai_icon_size 
        
        # Fire Icon
        # Fire Icon -> Left Side
        fire_x_start = box_x_start + 40 
        fire_icon_width = 60 
        fire_icon_height = 78 
        fire_icon = fire_icon.resize((fire_icon_width, fire_icon_height))

        # Text Area
        # Text sits between Fire (Left) and AI (Right)
        text_x_start = fire_x_start + fire_icon_width + 40 + 180 # +180px reserved for TPS number
        text_x_end = ai_x_pos - 40
        text_area_width = text_x_end - text_x_start

        # Box Vertical Positioning
        box_y_start = 470

        # Calculate Box Height
        body_wrapper = textwrap.TextWrapper(width=int(text_area_width / 18)) 
        body_lines = body_wrapper.wrap(text=short_summary)
        line_height = 50 
        text_block_height = len(body_lines) * line_height
        
        min_box_height = 220 
        calculated_height = text_block_height + 100 
        box_height = max(min_box_height, calculated_height)
        box_y_end = box_y_start + box_height

        # --- 5. Create Static Base Layer (Optimization) ---
        # We draw static elements once to save processing time in the loop
        static_base = base.copy()
        draw_static = ImageDraw.Draw(static_base)

        # Draw Headline (Centered in available top space)
        headline_lines = textwrap.wrap(headline, width=40)
        line_height_headline = 82
        available_top_space = box_y_start - 30
        total_headline_height = len(headline_lines) * line_height_headline
        y_cursor = max(50, (available_top_space - total_headline_height) / 2)

        for line in headline_lines:
            w = draw_static.textlength(line, font=font_headline)
            draw_static.text(((total_width - w) / 2, y_cursor), line, font=font_headline, fill="white")
            y_cursor += line_height_headline

        # Draw Glass Overlay
        overlay = Image.new('RGBA', static_base.size, (255, 255, 255, 0))
        d_overlay = ImageDraw.Draw(overlay)
        d_overlay.rounded_rectangle(
            [box_x_start, box_y_start, box_x_end, box_y_end],
            radius=50, fill=(255, 255, 255, 30)
        )
        static_base = Image.alpha_composite(static_base, overlay)
        
        # --- 6. Animation Loop ---
        frames = []
        num_frames = 60 # 60 frames @ 40ms = 2.4 seconds of active animation
        
        # Pre-calculate full text length for typewriter effect
        full_text_content = "".join(body_lines)
        total_chars = len(full_text_content)
        chars_per_frame = total_chars / 45 # Finish typing by frame 45

        # Center Elements in Box
        center_y = box_y_start + (box_height / 2)

        for i in range(num_frames):
            frame = static_base.copy()
            draw = ImageDraw.Draw(frame)
            
            # A) AI Icon (Pulse Effect)
            # Alpha oscillates between 180 and 255
            alpha = int(217 + 38 * math.sin(i * 0.2))
            ai_icon_resized = ai_icon.resize((ai_icon_size, ai_icon_size))
            
            # Apply alpha to icon
            r, g, b, a = ai_icon_resized.split()
            a = a.point(lambda p: p * (alpha / 255))
            ai_icon_final = Image.merge('RGBA', (r, g, b, a))
            
            frame.paste(ai_icon_final, (int(ai_x_pos), int(center_y - (ai_icon_size / 2))), ai_icon_final)
            
            # B) Fire Icon (Floating Effect)
            # Y position oscillates +/- 5 pixels
            float_offset = 5 * math.sin(i * 0.15)
            tps_y_pos = center_y - (fire_icon_height / 2) + float_offset
            frame.paste(fire_icon, (int(fire_x_start), int(tps_y_pos)), fire_icon)

            # C) Typewriter Effect (Body Text)
            current_char_limit = int(i * chars_per_frame)
            chars_drawn = 0
            
            text_y_cursor = center_y - (text_block_height / 2) - 5
            
            for line in body_lines:
                if chars_drawn >= current_char_limit:
                    break
                
                line_len = len(line)
                if chars_drawn + line_len <= current_char_limit:
                    # Draw full line
                    draw.text((text_x_start, text_y_cursor), line, font=font_body, fill="white")
                    chars_drawn += line_len
                else:
                    # Draw partial line
                    remaining = current_char_limit - chars_drawn
                    partial_text = line[:remaining]
                    draw.text((text_x_start, text_y_cursor), partial_text, font=font_body, fill="white")
                    chars_drawn += remaining
                    
                text_y_cursor += line_height

            # D) TPS Counter Animation
            # Ease-out effect: value approaches target quickly then slows down
            progress = min(1.0, i / 40.0)
            # Simple ease-out cubic function
            ease_progress = 1 - pow(1 - progress, 3)
            
            current_tps = tps_value * ease_progress
            display_tps = f"{current_tps:.1f}"
            
            text_tps_x = fire_x_start + fire_icon_width + 15 
            # TPS text moves slightly with the fire icon for cohesion
            text_tps_y = center_y - 25 + float_offset
            
            draw.text((text_tps_x, text_tps_y), "TPS: ", font=font_tps, fill="white")
            val_x_offset = draw.textlength("TPS: ", font=font_tps)
            draw.text((text_tps_x + val_x_offset, text_tps_y), display_tps, font=font_tps, fill=FIRE_GREEN)
            
            frames.append(frame)

        # --- 7. Final Polish & Save ---
        # Append the last frame 40 more times to create a pause at the end
        last_frame = frames[-1]
        for _ in range(40):
            frames.append(last_frame)

        filename = f"x_post_{trend_id}.gif"
        save_path = os.path.join(output_dir, filename)
        
        # Save as optimized GIF
        frames[0].save(
            save_path,
            save_all=True,
            append_images=frames[1:],
            optimize=True,
            duration=40, # 40ms per frame = 25 fps
            loop=0
        )
        
        logger.info(f"Generated animated X GIF for Trend {trend_id} at {save_path}")
        return f"/static/media/x_drafts/{filename}"

    except Exception as e:
        logger.error(f"Failed to generate X GIF for Trend {trend_id}: {e}")
        return None
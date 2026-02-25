import os
import logging
import textwrap
from PIL import Image, ImageDraw, ImageFont

# Configure module logger
logger = logging.getLogger(__name__)

def generate_x_image(trend_id, headline, short_summary, tps_value):
    """
    Generates a social media image for X (Twitter) based on the specific visual layout.
    
    Args:
        trend_id (int): The ID of the trend (used for filename).
        headline (str): The main headline text.
        short_summary (str): The short summary text for the body.
        tps_value (str/float): The TPS score to display.
        
    Returns:
        str: The relative web path to the generated image, or None if failed.
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
        draw = ImageDraw.Draw(base)
        
        FIRE_GREEN = (145, 220, 90)

        # --- 3. Load Fonts ---
        try:
            font_headline = ImageFont.truetype(os.path.join(assets_dir, "Roboto-Bold.ttf"), 67)
            font_body = ImageFont.truetype(os.path.join(assets_dir, "Roboto-Regular.ttf"), 37)
            font_tps = ImageFont.truetype(os.path.join(assets_dir, "Roboto-Bold.ttf"), 43)
        except OSError:
            logger.warning("Custom fonts not found in assets. Using default font.")
            font_headline = font_body = font_tps = ImageFont.load_default()

        # --- 4. Layout Logic (Preserved from image_creator.py) ---
        
        # Horizontal coordinates
        box_x_start = 160
        box_x_end = 1440
        # box_width = box_x_end - box_x_start # 1280 pixels (unused variable but logic implies it)
        
        # AI Icon
        ai_icon_size = 110
        ai_x_pos = box_x_start + 40
        
        # Fire Icon
        fire_x_start = 1100
        fire_icon_width = 60 
        fire_icon_height = 78 
        fire_icon = fire_icon.resize((fire_icon_width, fire_icon_height))

        # Text Area
        text_x_start = ai_x_pos + ai_icon_size + 40
        text_x_end = fire_x_start - 20
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

        # Draw Headline (Centered in available top space)
        headline_lines = textwrap.wrap(headline, width=40)
        line_height_headline = 82
        available_top_space = box_y_start - 30
        total_headline_height = len(headline_lines) * line_height_headline
        y_cursor = max(50, (available_top_space - total_headline_height) / 2)

        for line in headline_lines:
            w = draw.textlength(line, font=font_headline)
            draw.text(((total_width - w) / 2, y_cursor), line, font=font_headline, fill="white")
            y_cursor += line_height_headline

        # Draw Glass Overlay
        overlay = Image.new('RGBA', base.size, (255, 255, 255, 0))
        d_overlay = ImageDraw.Draw(overlay)
        d_overlay.rounded_rectangle(
            [box_x_start, box_y_start, box_x_end, box_y_end],
            radius=50, fill=(255, 255, 255, 30)
        )
        base = Image.alpha_composite(base, overlay)
        draw = ImageDraw.Draw(base)

        # Center Elements in Box
        center_y = box_y_start + (box_height / 2)

        # A) AI Icon
        ai_icon = ai_icon.resize((ai_icon_size, ai_icon_size))
        base.paste(ai_icon, (int(ai_x_pos), int(center_y - (ai_icon_size / 2))), ai_icon)

        # B) Body Text
        text_y_cursor = center_y - (text_block_height / 2) - 5
        for line in body_lines:
            draw.text((text_x_start, text_y_cursor), line, font=font_body, fill="white")
            text_y_cursor += line_height

        # C) TPS Section
        tps_y_pos = center_y - (fire_icon_height / 2)
        base.paste(fire_icon, (int(fire_x_start), int(tps_y_pos)), fire_icon)
        
        text_tps_x = fire_x_start + fire_icon_width + 15
        text_tps_y = center_y - 25 
        
        draw.text((text_tps_x, text_tps_y), "TPS: ", font=font_tps, fill="white")
        val_x_offset = draw.textlength("TPS: ", font=font_tps)
        draw.text((text_tps_x + val_x_offset, text_tps_y), str(tps_value), font=font_tps, fill=FIRE_GREEN)

        # --- 5. Save and Return ---
        filename = f"x_post_{trend_id}.jpg"
        save_path = os.path.join(output_dir, filename)
        base.convert("RGB").save(save_path, quality=100)
        
        logger.info(f"Generated X image for Trend {trend_id} at {save_path}")
        return f"/static/media/x_drafts/{filename}"

    except Exception as e:
        logger.error(f"Failed to generate X image for Trend {trend_id}: {e}")
        return None
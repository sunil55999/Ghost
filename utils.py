import re
import unicodedata
import cv2
import pytesseract
from PIL import Image, ImageDraw, ImageFont
import io
import imagehash
import numpy as np
import torch

# Check for LaMa availability (for advanced inpainting)
USE_LAMA = torch.cuda.is_available()

if USE_LAMA:
    from lama_model import LaMaModel  # Hypothetical LaMa integration

def clean_text(text, config):
    """Clean the text by removing headers, footers, phrases, and mentions while preserving formatting."""
    lines = text.split('\n')
    header_patterns = config.get('header_patterns', [])
    footer_patterns = config.get('footer_patterns', [])
    remove_phrases = config.get('remove_phrases', [])
    remove_mentions = config.get('remove_mentions', False)

    # Remove headers
    for pattern in header_patterns:
        if lines and re.match(pattern, lines[0]):
            lines.pop(0)

    # Remove footers
    for pattern in footer_patterns:
        if lines and re.match(pattern, lines[-1]):
            lines.pop()

    # Remove inline phrases
    for phrase in remove_phrases:
        lines = [re.sub(phrase, '', line) for line in lines]

    # Remove mentions if enabled
    if remove_mentions:
        lines = [re.sub(r'@[a-zA-Z0-9_]+|t\.me/\S+', '', line) for line in lines]

    # Reconstruct the text
    cleaned_text = '\n'.join(lines).strip()
    return cleaned_text

def should_block_message(text, trap_phrases):
    """Check if the text contains any trap phrases."""
    return any(phrase.lower() in text.lower() for phrase in trap_phrases)

def remove_watermark_from_image(image_bytes, target_texts):
    """Remove specified watermark texts from an image using OCR and inpainting."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_array = np.array(image)
    ocr_data = pytesseract.image_to_data(img_array, output_type=pytesseract.Output.DICT)
    
    for i, text in enumerate(ocr_data["text"]):
        if text.strip().lower() in [t.lower() for t in target_texts]:
            x, y, w, h = ocr_data["left"][i], ocr_data["top"][i], ocr_data["width"][i], ocr_data["height"][i]
            x, y = max(0, x-10), max(0, y-10)
            w, h = w+20, h+20
            mask = np.zeros(img_array.shape[:2], dtype=np.uint8)
            mask[y:y+h, x:x+w] = 255
            if USE_LAMA:
                img_array = LaMaModel.inpaint(img_array, mask)
            else:
                img_array = cv2.inpaint(img_array, mask, 3, cv2.INPAINT_TELEA)
    
    result_image = Image.fromarray(img_array)
    output = io.BytesIO()
    result_image.save(output, format="PNG")
    return output.getvalue()

def detect_text_in_image(image_bytes, trap_texts):
    """Detect trap texts in an image using OCR."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    text = pytesseract.image_to_string(image).lower()
    return any(trap.lower() in text for trap in trap_texts)

def filter_content(text, mapping):
    """Filter URLs, mentions, and custom footers from text."""
    text = re.sub(r'https?://\S+|www\.\S+|t\.me/\S+', '', text)  # Remove URLs
    text = re.sub(r'@[a-zA-Z0-9_]+', '', text)  # Remove mentions
    if 'footer_pattern' in mapping:
        text = re.sub(mapping['footer_pattern'], '', text)
    text = unicodedata.normalize('NFKC', text)  # Normalize Unicode
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)  # Remove zero-width chars
    return text

def generate_watermark(msg_id):
    """Generate an invisible watermark."""
    binary_id = bin(msg_id)[2:]
    return ''.join(['\u200B' if bit == '0' else '\u200C' for bit in binary_id])

def reencode_image(image_bytes):
    """Re-encode image to remove metadata."""
    image = Image.open(io.BytesIO(image_bytes))
    output = io.BytesIO()
    image.save(output, format='JPEG')
    return output.getvalue()

def is_trap_image(image_bytes, trap_hashes):
    """Check if image matches a trap hash."""
    image = Image.open(io.BytesIO(image_bytes))
    hash = str(imagehash.phash(image))
    return hash in trap_hashes

def add_visible_watermark(image_bytes, watermark_text):
    """Add visible watermark to image."""
    image = Image.open(io.BytesIO(image_bytes))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((10, 10), watermark_text, font=font, fill=(255, 255, 255, 128))
    output = io.BytesIO()
    image.save(output, format='JPEG')
    return output.getvalue()

async def notify_trap(event, mapping, pair_name, reason):
    """Notify owner of detected trap."""
    from bot import NOTIFY_CHAT_ID, client, logger
    if NOTIFY_CHAT_ID:
        logger.info(f"Trap detected in '{pair_name}': {reason}")
        await client.send_message(
            NOTIFY_CHAT_ID,
            f"🚨 Trap detected in '{pair_name}': {reason}"
        )

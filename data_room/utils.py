import base64
import os

from django.conf import settings
from django.utils.text import slugify
from PIL import Image, ImageDraw, ImageFont


def slugify_de(value):
    """German-aware slugify (copy of pages.utils.slugify_de, plan W14-A8)."""
    replacements = [
        (".", "-"),
        ("/", "-"),
        ("ä", "ae"),
        ("Ä", "Ae"),
        ("ö", "oe"),
        ("Ö", "Oe"),
        ("ü", "ue"),
        ("Ü", "Ue"),
        ("ß", "ss"),
        ("+", "plus"),
    ]
    for s, r in replacements:
        value = str(value).replace(s, r)
    return slugify(value)


def get_text_dimensions(text_string, font):
    ascent, descent = font.getmetrics()
    text_width = font.getmask(text_string).getbbox()[2]
    text_height = font.getmask(text_string).getbbox()[3] + descent
    return (text_width, text_height)


def create_watermarks(text):
    font_file = getattr(settings, "DATA_ROOM_WATERMARK_FONT", None)
    if font_file:
        font = ImageFont.truetype(font_file, 18)
    else:
        font = ImageFont.load_default(size=18)
    textwidth, textheight = get_text_dimensions(text, font)
    watermark = Image.new("RGBA", (textwidth, textheight), (255, 255, 255, 0))
    foreground = ImageDraw.Draw(watermark)
    foreground.text((0, 0), text, font=font, fill=(0, 0, 0, 10))
    watermark1 = watermark.rotate(45, fillcolor=(255, 255, 255, 0), expand=True)
    watermark2 = watermark.rotate(-45, fillcolor=(255, 255, 255, 0), expand=True)
    empty = Image.new("RGBA", (watermark.width, watermark.height), (255, 255, 255, 0))
    return watermark1, watermark2, empty


def apply_watermark(image, watermark1, watermark2, empty):
    width, height = image.size
    position_x = 0
    position_y = 0
    while position_y < width:
        while position_x < height:
            image.paste(watermark1, (position_x, position_y), mask=watermark1)
            position_x += watermark1.width
            image.paste(empty, (position_x, position_y), mask=empty)
            position_x += empty.width
        position_y += watermark1.height
        position_x = 0
        while position_x < height:
            image.paste(empty, (position_x, position_y), mask=empty)
            position_x += empty.width
            image.paste(watermark2, (position_x, position_y), mask=watermark2)
            position_x += watermark2.width
        position_y += watermark2.height
        position_x = 0
    while position_x < height:
        image.paste(watermark1, (position_x, position_y), mask=watermark1)
        position_x += watermark1.width
        image.paste(empty, (position_x, position_y), mask=empty)
        position_x += empty.width
    position_y += watermark1.height
    position_x = 0
    while position_x < height:
        image.paste(empty, (position_x, position_y), mask=empty)
        position_x += empty.width
        image.paste(watermark2, (position_x, position_y), mask=watermark2)
        position_x += watermark2.width
    return image


def process_image(filename, watermark1, watermark2, empty, user_type):
    with Image.open(filename) as image:
        width, height = image.size
        if max(width, height) > 1200:
            ratio = max(width, height) / 1200
            image = image.resize((int(width / ratio), int(height / ratio)))
        image = image.convert("RGBA")
        if user_type != "partner":
            image = apply_watermark(image, watermark1, watermark2, empty)
        watermark_filename = os.path.splitext(filename)[0] + "_watermark.png"
        image.save(watermark_filename)
        return watermark_filename


def encode_to_base64(filename):
    with open(filename, "rb") as image:
        return base64.b64encode(image.read()).decode("utf-8")


def compress_pdf_bytes(pdf_bytes):
    import fitz

    original_size = len(pdf_bytes)
    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    compressed_bytes = pdf_doc.write(
        garbage=True,
        clean=True,
        deflate=True,
        deflate_images=True,
        compression_effort=100,
    )
    pdf_doc.close()
    compressed_size = len(compressed_bytes)
    if compressed_size < original_size:
        reduction_percent = (1 - compressed_size / original_size) * 100
        print(
            f"PDF compressed: {original_size/1024:.1f}KB → {compressed_size/1024:.1f}KB "
            f"({reduction_percent:.1f}% reduction)"
        )
        return compressed_bytes
    else:
        print("Compression did not reduce file size, keeping original")
        return pdf_bytes

from __future__ import annotations

import base64
import io

from PIL import Image

from yuxi.utils.image_processor import process_uploaded_image


def test_process_uploaded_image_composites_transparent_png_pixels_on_white():
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 0))
    image.putpixel((0, 0), (50, 87, 244, 0))
    image.putpixel((1, 0), (50, 87, 244, 255))

    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_data = buffer.getvalue()

    result = process_uploaded_image(image_data, "transparent.png")

    assert result["success"] is True
    assert result["format"] == "PNG"
    assert result["mime_type"] == "image/png"

    processed_data = base64.b64decode(result["image_content"])
    with Image.open(io.BytesIO(processed_data)) as processed_image:
        rgb_image = processed_image.convert("RGB")

    assert rgb_image.getpixel((0, 0)) == (255, 255, 255)
    assert rgb_image.getpixel((1, 0)) == (50, 87, 244)

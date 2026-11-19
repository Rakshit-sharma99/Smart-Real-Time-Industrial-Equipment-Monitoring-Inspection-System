"""Secure handling of inspection photos."""
import io

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_PHOTO_FORMATS = {"JPEG", "PNG", "WEBP"}


def process_photo(uploaded_file):
    """
    Validate an uploaded photo and return a clean JPEG (as ContentFile).

    - max 5 MB, and it must really be a JPEG/PNG/WebP (checked by Pillow, not the file name)
    - rotated upright using its EXIF orientation, then EXIF is dropped (no GPS/camera data kept)
    - resized so the longest side is at most 1600 px
    """
    if uploaded_file.size > settings.PHOTO_MAX_UPLOAD_BYTES:
        raise ValidationError("The photo is larger than 5 MB.")
    try:
        image = Image.open(uploaded_file)
        image.verify()  # detects truncated or fake files
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ValidationError("This file is not a valid image.")
    if image.format not in ALLOWED_PHOTO_FORMATS:
        raise ValidationError("Only JPEG, PNG or WebP photos are allowed.")

    uploaded_file.seek(0)
    image = Image.open(uploaded_file)  # verify() leaves the image unusable, so open it again
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((settings.PHOTO_MAX_DIMENSION, settings.PHOTO_MAX_DIMENSION))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)  # no exif= argument -> metadata stripped
    return ContentFile(buffer.getvalue(), name="photo.jpg")

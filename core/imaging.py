"""Yuklangan rasmlarni siqish.

Maqsad — fayl hajmini kamaytirish, lekin ko'rinishga tegmaslik:
nisbat saqlanadi, rasm hech qachon kattalashtirilmaydi va sifat
ko'z ilg'amaydigan darajada qoldiriladi.
"""

import io
import os

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageFile, ImageOps


def _setting(name, default):
    return getattr(settings, name, default)


def _has_transparency(image):
    """Rasmda haqiqatan ham shaffof piksel bormi.

    Faqat rejimga qarash yetarli emas: ko'p PNG'lar RGBA bo'lsa-da,
    alfa kanali butunlay to'ldirilgan bo'ladi — bunday rasmni JPEG
    ancha yaxshi siqadi.
    """
    if image.mode == "P":
        if "transparency" not in image.info:
            return False
        image = image.convert("RGBA")

    if image.mode not in ("RGBA", "LA"):
        return False

    alpha = image.getchannel("A")
    return alpha.getextrema()[0] < 255


def _save_jpeg(image, buffer, quality):
    """JPEG saqlash — progressive rejim buzilganda oddiy rejimga qaytadi.

    Pillow progressive JPEG'ning har bir "scan"ini MAXBLOCK buferiga yozadi.
    Tafsilotli fotosuratda bu bufer yetmay "broken data stream" xatosi chiqadi,
    shuning uchun buferni rasm o'lchamiga moslab olamiz va zaxira yo'l qoldiramiz.
    """
    ImageFile.MAXBLOCK = max(ImageFile.MAXBLOCK, image.size[0] * image.size[1])
    try:
        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling=0,      # rang chegaralari (yozuv, logotip) xiralashmasin
        )
    except OSError:
        buffer.seek(0)
        buffer.truncate(0)
        image.save(buffer, format="JPEG", quality=quality, subsampling=0)


def compress_image(file_obj):
    """Rasmni siqib, (ContentFile, fayl_nomi) qaytaradi.

    Siqishdan foyda bo'lmasa yoki fayl rasm bo'lmasa — None qaytaradi,
    ya'ni asl fayl o'zgarishsiz saqlanadi.
    """
    max_side = _setting("POS_IMAGE_MAX_SIDE", 1600)
    quality = _setting("POS_IMAGE_QUALITY", 85)

    try:
        file_obj.seek(0)
        image = Image.open(file_obj)
        image.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None

    # Telefonda olingan rasmlar EXIF burchagi bilan keladi — uni rasmga "singdiramiz",
    # aks holda siqilgandan keyin yonboshlab qoladi.
    image = ImageOps.exif_transpose(image) or image

    has_alpha = _has_transparency(image)

    # thumbnail() nisbatni o'zi saqlaydi va rasmni hech qachon kattalashtirmaydi.
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    if has_alpha:
        # Shaffoflik yo'qolmasligi uchun WebP — PNG'dan ancha yengil.
        image.convert("RGBA").save(buffer, format="WEBP", quality=quality, method=6)
        extension = ".webp"
    else:
        _save_jpeg(image.convert("RGB"), buffer, quality)
        extension = ".jpg"

    data = buffer.getvalue()

    # Siqilgani asl fayldan yengil bo'lmasa, asl faylga tegmaymiz.
    original_size = getattr(file_obj, "size", None)
    if original_size and len(data) >= original_size:
        return None

    stem = os.path.splitext(os.path.basename(getattr(file_obj, "name", "rasm")))[0]
    return ContentFile(data), f"{stem}{extension}"

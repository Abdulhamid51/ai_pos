"""LLM aloqasini tekshirish: Gemini API'ga bitta oddiy so'rov yuboradi.

Birinchi sozlashda ishlatiladi — kalit, model nomi va tarmoq to'g'ri
ishlayotganini bir joyda bilib olish uchun.

    python manage.py ai_test
    python manage.py ai_test --savol "Kassa apparati nima?"
    python manage.py ai_test --model gemini-3.8-flash
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DEFAULT_QUESTION = "Bir gapda o'zingni tanishtir."


class Command(BaseCommand):
    help = "Gemini API bilan aloqani tekshiradi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--savol", default=DEFAULT_QUESTION, help="Modelga yuboriladigan matn."
        )
        parser.add_argument(
            "--model", default=None, help="Sozlamadagidan boshqa modelni sinash uchun."
        )

    def handle(self, *args, **options):
        # Kutubxona faqat shu buyruq ishlaganda kerak — loyihaning qolgan
        # qismi u o'rnatilmagan bo'lsa ham ishlashda davom etadi.
        try:
            from google import genai
        except ImportError:
            raise CommandError(
                "google-genai o'rnatilmagan. Buyruq: pip install -U google-genai"
            )

        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise CommandError(".env faylida GEMINI_API_KEY topilmadi.")

        model = options["model"] or settings.GEMINI_MODEL
        question = options["savol"]

        self.stdout.write(f"Model:  {model}")
        self.stdout.write(f"So'rov: {question}")

        client = genai.Client(api_key=api_key)

        started = time.monotonic()
        try:
            interaction = client.interactions.create(model=model, input=question)
        except Exception as error:
            # Noto'g'ri kalit, noma'lum model nomi, uzilgan tarmoq — hammasi shu yerda.
            raise CommandError(f"So'rov bajarilmadi — {type(error).__name__}: {error}")
        elapsed = time.monotonic() - started

        self.stdout.write("")
        self.stdout.write(interaction.output_text or "(bo'sh javob)")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Tayyor — {elapsed:.1f} soniya"))

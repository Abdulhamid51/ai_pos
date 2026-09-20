"""Mahsulotlar uchun semantik qidiruv indeksini yaratadi.

Har bir mahsulot matni vektorga aylantirilib saqlanadi. Matni o'zgarmaganlar
o'tkazib yuboriladi, shuning uchun buyruqni istagancha qayta ishlatsa bo'ladi.

    python manage.py index_products
    python manage.py index_products --hammasi   # o'zgarmaganlarni ham qayta hisoblaydi
"""

import time

from django.core.management.base import BaseCommand, CommandError

from core import retrieval
from core.ai import AIError


class Command(BaseCommand):
    help = "Mahsulotlar uchun vektor indeksini yangilaydi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hammasi", action="store_true",
            help="Matni o'zgarmagan mahsulotlarni ham qayta hisoblaydi.",
        )

    def handle(self, *args, **options):
        started = time.monotonic()
        self.stdout.write("Indeks yangilanmoqda…")

        try:
            result = retrieval.index_products(force=options["hammasi"])
        except AIError as error:
            raise CommandError(str(error))

        elapsed = time.monotonic() - started
        self.stdout.write(
            self.style.SUCCESS(
                f"Tayyor — {result['indexed']} ta yangilandi, "
                f"{result['skipped']} ta o'zgarmagan ({elapsed:.1f} soniya)"
            )
        )

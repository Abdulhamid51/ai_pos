"""Mahsulotlardagi matn ko'rinishidagi ranglarni Color modeliga ko'chiradi."""

from django.db import migrations


def text_colors_to_model(apps, schema_editor):
    Color = apps.get_model("core", "Color")
    Product = apps.get_model("core", "Product")

    for product in Product.objects.exclude(color="").iterator():
        color, created = Color.objects.get_or_create(
            name=product.color, defaults={"hex": product.color_hex or ""}
        )
        # Lug'atda kod bo'lmasa, mahsulotdagisini olamiz.
        if not color.hex and product.color_hex:
            color.hex = product.color_hex
            color.save(update_fields=["hex"])
        product.color_fk = color
        product.save(update_fields=["color_fk"])


def model_colors_to_text(apps, schema_editor):
    Product = apps.get_model("core", "Product")
    for product in Product.objects.exclude(color_fk=None).select_related("color_fk").iterator():
        product.color = product.color_fk.name
        product.color_hex = product.color_fk.hex
        product.save(update_fields=["color", "color_hex"])


class Migration(migrations.Migration):

    dependencies = [("core", "0003_color_branch_trade_type_company_barcode_length_and_more")]

    operations = [migrations.RunPython(text_colors_to_model, model_colors_to_text)]

"""Eski matn maydonlarini olib tashlab, FK'ni `color` nomiga o'tkazadi."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("core", "0004_colors_from_text")]

    operations = [
        migrations.RemoveField(model_name="product", name="color"),
        migrations.RemoveField(model_name="product", name="color_hex"),
        migrations.RenameField(model_name="product", old_name="color_fk", new_name="color"),
        migrations.AlterModelOptions(
            name="product",
            options={
                "ordering": ["name", "color__name"],
                "verbose_name": "Mahsulot",
                "verbose_name_plural": "Mahsulotlar",
            },
        ),
    ]

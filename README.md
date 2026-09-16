# ai_pos

Django asosidagi POS (savdo nuqtasi) tizimi. Keyingi bosqichda AI integratsiyasi qo'shiladi.

## Ishga tushirish

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # SECRET_KEY ni o'zingiznikiga almashtiring
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Sayt: http://127.0.0.1:8000/ · Admin panel: http://127.0.0.1:8000/admin/

## Tuzilishi

- `pos/` — loyiha sozlamalari (`settings.py`, `urls.py`)
- `core/` — asosiy POS ilovasi (modellar, ko'rinishlar, shablonlar)
- `templates/base.html` — umumiy sahifa qolipi
- `static/` — CSS/JS manbalari
- `.env` — maxfiy sozlamalar (git'ga tushmaydi)

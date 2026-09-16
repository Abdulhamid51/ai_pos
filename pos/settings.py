"""
pos loyihasi uchun Django sozlamalari.

Hujjatlar: https://docs.djangoproject.com/en/5.2/ref/settings/
Maxfiy qiymatlar (SECRET_KEY, DEBUG, ALLOWED_HOSTS) `.env` faylidan o'qiladi,
namuna uchun `.env.example` fayliga qarang.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Loyiha ildizi: BASE_DIR / 'subdir' ko'rinishida yo'l yasash uchun.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    """Muhit o'zgaruvchisini bool qiymatga aylantiradi."""
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    """Vergul bilan ajratilgan muhit o'zgaruvchisini ro'yxatga aylantiradi."""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Xavfsizlik
# ---------------------------------------------------------------------------

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-mvfv^)ln9w7bd95dtt0gsu11^r7ahv=gsima&*7yedg9@p)2!)",
)

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

if not DEBUG:
    # Faqat productionda: HTTPS va cookie himoyasi.
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    X_FRAME_OPTIONS = "DENY"


# ---------------------------------------------------------------------------
# Ilovalar
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

LOCAL_APPS = [
    "core.apps.CoreConfig",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pos.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context.pos_context",
            ],
        },
    },
]

WSGI_APPLICATION = "pos.wsgi.application"
ASGI_APPLICATION = "pos.asgi.application"


# ---------------------------------------------------------------------------
# Ma'lumotlar bazasi
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ---------------------------------------------------------------------------
# Autentifikatsiya
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# Sessiya 12 soat — bir ish smenasiga yetadi.
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_SAVE_EVERY_REQUEST = True


# ---------------------------------------------------------------------------
# Til va vaqt
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "uz"

TIME_ZONE = "Asia/Tashkent"

USE_I18N = True

USE_TZ = True

LANGUAGES = [
    ("uz", "O'zbekcha"),
    ("ru", "Русский"),
    ("en", "English"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]


# ---------------------------------------------------------------------------
# Statik va media fayllar
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


# ---------------------------------------------------------------------------
# Xabarlar (messages) — Bootstrap klasslariga moslangan
# ---------------------------------------------------------------------------

from django.contrib.messages import constants as messages  # noqa: E402

MESSAGE_TAGS = {
    messages.DEBUG: "secondary",
    messages.INFO: "info",
    messages.SUCCESS: "success",
    messages.WARNING: "warning",
    messages.ERROR: "danger",
}


# ---------------------------------------------------------------------------
# Loglar
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}


# ---------------------------------------------------------------------------
# POS biznes sozlamalari
# ---------------------------------------------------------------------------

POS_CURRENCY = os.environ.get("POS_CURRENCY", "so'm")
POS_STORE_NAME = os.environ.get("POS_STORE_NAME", "AI POS")
# Sotuvda ustiga qo'shiladigan QQS foizi (0 — QQS hisoblanmaydi).
POS_VAT_PERCENT = int(os.environ.get("POS_VAT_PERCENT", "0"))

# Yuklangan rasmlarni siqish: eng uzun tomoni shu piksel bilan cheklanadi
# (nisbat saqlanadi, kichik rasm kattalashtirilmaydi) va shu sifatda saqlanadi.
POS_IMAGE_MAX_SIDE = int(os.environ.get("POS_IMAGE_MAX_SIDE", "1600"))
POS_IMAGE_QUALITY = int(os.environ.get("POS_IMAGE_QUALITY", "85"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

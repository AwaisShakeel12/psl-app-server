"""
Django settings for PSL AI Auction multiplayer backend.

Generated baseline adapted for Django 5.2 + DRF + Channels.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default=None):
    if default is None:
        default = []
    val = os.getenv(name)
    if val is None:
        return list(default)
    return [item.strip() for item in val.split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-change-me")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", ["localhost", "127.0.0.1", "testserver"] if DEBUG else [])
if DEBUG:
    # Allow common local/dev clients (Android emulator uses 10.0.2.2 -> host PC)
    for _host in ("localhost", "127.0.0.1", "testserver", "10.0.2.2"):
        if _host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_host)
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", [])


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "app.apps.AppConfig",
]

try:
    import daphne  # noqa: F401
    INSTALLED_APPS.insert(0, "daphne")
except Exception:
    pass

try:
    import channels  # noqa: F401
    if "channels" not in INSTALLED_APPS:
        INSTALLED_APPS.append("channels")
except Exception:
    pass

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "project.wsgi.application"
ASGI_APPLICATION = "project.asgi.application"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "UNAUTHENTICATED_USER": None,
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# PSL multiplayer backend settings
SERVER_VERSION = os.getenv("SERVER_VERSION", "0.1.0")
ROOM_WAITING_EXPIRY_MINUTES = env_int("ROOM_WAITING_EXPIRY_MINUTES", 60)
ROOM_ABANDONED_HOST_GRACE_MINUTES = env_int("ROOM_ABANDONED_HOST_GRACE_MINUTES", 10)
ROOM_COMPLETED_RETENTION_MINUTES = env_int("ROOM_COMPLETED_RETENTION_MINUTES", 120)
TOKEN_EXPIRY_MINUTES = env_int("TOKEN_EXPIRY_MINUTES", 720)
MAX_ROOM_PARTICIPANTS = env_int("MAX_ROOM_PARTICIPANTS", 20)
MAX_ACTIVE_ROOMS = env_int("MAX_ACTIVE_ROOMS", 100)
# 4MB: the host relays a full AUCTION_STATE_SNAPSHOT (entire player queue + events) over the
# socket. The old 64KB cap rejected those frames, breaking participant live-auction state sync.
MAX_WS_PAYLOAD_BYTES = env_int("MAX_WS_PAYLOAD_BYTES", 4194304)
BID_REQUEST_RATE_LIMIT_PER_MINUTE = env_int("BID_REQUEST_RATE_LIMIT_PER_MINUTE", 60)
JOIN_RATE_LIMIT_PER_MINUTE = env_int("JOIN_RATE_LIMIT_PER_MINUTE", 30)
CREATE_ROOM_RATE_LIMIT_PER_MINUTE = env_int("CREATE_ROOM_RATE_LIMIT_PER_MINUTE", 20)
WS_ALLOWED_ORIGINS = env_list("WS_ALLOWED_ORIGINS", [])

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}


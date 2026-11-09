"""
Django settings for RestaurantSpy.

Machine-specific values (secrets, hosts, tokens) are read from a .env file in
the project root. See .env.example for the full list.
"""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    raw = os.getenv(name) or default
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_int(name, default):
    value = os.getenv(name, "").strip()
    return int(value) if value else default


# --- Core -------------------------------------------------------------------

DEBUG = env_bool("DEBUG", default=False)

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-never-use-in-production"
    else:
        raise ImproperlyConfigured(
            "SECRET_KEY is not set. Add it to .env (it is required when DEBUG=False)."
        )

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
)

INSTALLED_APPS = [
    # daphne must come first so "manage.py runserver" serves ASGI (WebSockets).
    "daphne",
    "config.apps.SuperuserAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "core",
    "accounts",
    "tables",
    "inspections",
    "tasks",
    "reservations",
    "notifications",
    "cctv",
    "reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Every page needs a login unless the view is marked @login_not_required.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    # request.role and request.restaurant for every view.
    "core.middleware.RestaurantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app_context",
            ],
        },
    },
]

# --- Database -----------------------------------------------------------------
# WAL + IMMEDIATE transactions let several staff save at the same time and
# serialise writers instead of failing with "database is locked".

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # Own file name, so the EquipWatch version on the main branch keeps its db.sqlite3.
        "NAME": BASE_DIR / os.getenv("DATABASE_FILE", "restaurantspy.sqlite3"),
        "OPTIONS": {
            "timeout": 20,
            "init_command": "PRAGMA journal_mode=WAL;",
            "transaction_mode": "IMMEDIATE",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Channels (WebSockets) --------------------------------------------------------
# In-memory layer: works because every change (pages, APIs, the camera
# detection endpoint) happens inside the one web server process. Production
# with several processes would use the Redis channel layer instead.

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Process-local cache: used for login rate limiting, API throttling and the
# once-a-minute overdue-task check.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Auth & sessions ----------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 8 * 60 * 60  # 8 hours
SESSION_COOKIE_SAMESITE = "Lax"
# JavaScript reads the CSRF token from a <meta> tag, so the cookie can be HttpOnly.
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

# --- Internationalisation -----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# --- Static & media -----------------------------------------------------------------

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded table inspection photos live here. There is deliberately no public
# MEDIA_URL route: photos are served by a permission-checked view.
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# --- Django REST Framework ---------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "core.api.json_exception_handler",
    # Per-endpoint limits (views opt in with throttle_scope).
    "DEFAULT_THROTTLE_RATES": {"detections": "120/min", "reservations": "30/min"},
}

# --- Logging ------------------------------------------------------------------------------

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        # Plain FileHandler (no rotation): rotating a file that two processes
        # hold open fails on Windows.
        "file": {
            "class": "logging.FileHandler",
            "filename": LOG_DIR / "app.log",
            "encoding": "utf-8",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
    },
}

# --- Test runs ------------------------------------------------------------------------------
# "manage.py test": use a fast password hasher and keep expected 403/404
# warnings out of the output. Never affects the running app.

TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    LOGGING["root"]["level"] = "ERROR"
    LOGGING["loggers"]["django"]["level"] = "ERROR"

# --- RestaurantSpy settings ----------------------------------------------------------------

APP_NAME = os.getenv("APP_NAME", "RestaurantSpy")
COLLEGE_NAME = os.getenv("COLLEGE_NAME", "")
TEAM_NAMES = os.getenv("TEAM_NAMES", "")

# Base URL encoded into table QR codes ({SITE_BASE_URL}/t/<public_id>/).
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "").strip()

# Shared secret for the camera / computer-vision detection API (POST /api/v1/detections/).
# No vision model ships with this project; the endpoint is the integration point.
DETECTION_API_TOKEN = os.getenv("DETECTION_API_TOKEN", "").strip()
# Camera detections below this confidence are stored but do not change the table.
DETECTION_MIN_CONFIDENCE = float(os.getenv("DETECTION_MIN_CONFIDENCE", "0.6"))

# Login protection: this many failed attempts for one username from one IP
# address blocks further attempts for LOGIN_LOCKOUT_SECONDS.
LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60

# Inspection photo limits.
PHOTO_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PHOTO_MAX_DIMENSION = 1600

"""Seyalini settings. Everything secret or machine-specific comes from .env."""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
TENANTS_DIR = BASE_DIR / "tenants"


def _load_dotenv(path: Path):
    """Read .env when running without Docker (Docker's env_file already sets these).
    Real environment variables always win over the file."""
    if not path.exists():
        return
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")
# Use LiteLLM's built-in price list instead of downloading it at every start (works offline, no warnings)
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def env(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-not-secret")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
DASHBOARD_URL = env("DASHBOARD_URL", "http://localhost:8000")

# --- Serving under a sub-path, e.g. https://inixr.com/seyalini ----------------
# nginx strips the prefix and forwards to gunicorn; Django adds it back to every link.
URL_PREFIX = "/" + env("URL_PREFIX", "").strip().strip("/") if env("URL_PREFIX", "").strip("/ ") else ""
FORCE_SCRIPT_NAME = URL_PREFIX or None
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
# own cookie names/paths so Seyalini never clashes with the main website on the same domain
SESSION_COOKIE_NAME = "seyalini_session"
CSRF_COOKIE_NAME = "seyalini_csrf"
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = URL_PREFIX or "/"
if not DEBUG:  # behind nginx with https
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.tenancy.TenantMiddleware",
]

ROOT_URLCONF = "config.urls"
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
                "django.template.context_processors.media",
                "core.tenancy.tenant_context",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": dj_database_url.parse(env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"), conn_max_age=60)}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = f"{URL_PREFIX}/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "login"

# --- Background jobs -------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_EAGER", False)
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_SERIALIZER = "json"
# Chat replies and Yes/No taps get their own worker (seyalini-chat), so they never wait behind a
# video render. Everything else stays on the default queue.
CELERY_TASK_ROUTES = {
    "agents.earner.tasks.telegram_update": {"queue": "chat"},
}
# celery = Docker/server with Redis; thread = laptop without Redis (CELERY_EAGER=1)
JOBS_MODE = env("JOBS_MODE", "thread" if CELERY_TASK_ALWAYS_EAGER else "celery")

# --- Media (videos, images). Later: Azure Blob / Cloudflare R2 -------------------
MEDIA_URL = f"{URL_PREFIX}/media/"
MEDIA_ROOT_ENV = env("MEDIA_ROOT", "")
MEDIA_ROOT = Path(MEDIA_ROOT_ENV) if MEDIA_ROOT_ENV else BASE_DIR / "media"

# --- AI ----------------------------------------------------------------------
# Force free sample mode for everyone. Otherwise each organisation is live when it has a
# Gemini key (its own, or yours from .env if it is allowed to use platform keys).
LLM_DRY_RUN = env_bool("LLM_DRY_RUN", False)
ALLOW_SIGNUP = env_bool("ALLOW_SIGNUP", False)  # public "create your organisation" page

# --- Notifications -----------------------------------------------------------
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"seyalini": {"handlers": ["console"], "level": "INFO"}},
}

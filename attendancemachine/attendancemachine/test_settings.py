
"""
Isolated Django test settings.

Uses in-memory SQLite databases for automated tests.
Production MySQL settings and credentials remain unchanged.

Usage:
python manage.py test --settings=attendancemachine.test_settings
"""

from .settings import *


# Override database connections for testing only.
# No connection to the production MySQL databases.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {
            "NAME": ":memory:",
        },
    },

    "logs": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {
            "NAME": ":memory:",
        },
    },
}


# Disable password hashing overhead during tests.

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]


# Prevent outgoing emails during tests.

EMAIL_BACKEND = (
    "django.core.mail.backends.locmem.EmailBackend"
)

SIGNIN_EMAIL_BACKEND = (
    "django.core.mail.backends.locmem.EmailBackend"
)

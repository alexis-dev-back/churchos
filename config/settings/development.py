import secrets

from decouple import config

from .base import *

DEBUG = True
SECRET_KEY = config('SECRET_KEY', default=secrets.token_urlsafe(50))
SIMPLE_JWT = {
    **SIMPLE_JWT,
    'SIGNING_KEY': config('JWT_SIGNING_KEY', default=SECRET_KEY),
}
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
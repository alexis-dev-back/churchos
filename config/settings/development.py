import secrets

from decouple import Csv, config

from .base import *

DEBUG = True
SECRET_KEY = config('SECRET_KEY', default=secrets.token_urlsafe(50))
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default='http://127.0.0.1:4200,http://localhost:4200',
    cast=Csv(),
)
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
from decouple import config
from django.core.exceptions import ImproperlyConfigured

from .base import *


DEBUG = False
JWT_SIGNING_KEY = config('JWT_SIGNING_KEY', default='')
if not SECRET_KEY or not JWT_SIGNING_KEY:
	raise ImproperlyConfigured('SECRET_KEY and JWT_SIGNING_KEY must be set in production.')

SIMPLE_JWT = {**SIMPLE_JWT, 'SIGNING_KEY': JWT_SIGNING_KEY}
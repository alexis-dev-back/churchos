import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    """Return explicit API errors instead of raw Django 500 pages."""
    response = exception_handler(exc, context)

    if response is not None:
        return response

    detail = 'Une erreur interne est survenue. Vérifiez les champs envoyés et réessayez.'
    error_name = type(exc).__name__
    logging.getLogger(__name__).exception('Unhandled API exception: %s', error_name, exc_info=exc)

    if isinstance(exc, APIException):
        detail = str(exc)

    if hasattr(exc, 'messages') and getattr(exc, 'messages', None):
        detail = '; '.join(str(item) for item in exc.messages)

    return Response(
        {
            'detail': detail,
            'error': error_name,
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )

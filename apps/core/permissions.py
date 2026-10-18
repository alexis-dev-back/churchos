from rest_framework.permissions import BasePermission


class IsSuperuser(BasePermission):
    message = 'Cette action est réservée au superadministrateur de la plateforme.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)
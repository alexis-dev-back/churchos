from rest_framework.permissions import BasePermission

from apps.core.models import OrganizationMembership
from .context import TenantContext


class HasActiveTenant(BasePermission):
    message = 'Sélectionnez une organisation avant d’accéder à cette ressource.'

    def has_permission(self, request, view):
        return TenantContext.exists()


class HasTenantAdminRole(BasePermission):
    message = 'Seuls le propriétaire ou un administrateur peuvent gérer cette ressource.'

    def has_permission(self, request, view):
        organization = TenantContext.get()
        if not request.user or not request.user.is_authenticated or organization is None:
            return False
        return OrganizationMembership.objects.filter(
            user=request.user,
            organization=organization,
            is_active=True,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exists()
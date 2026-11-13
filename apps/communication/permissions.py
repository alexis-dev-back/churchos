from rest_framework.permissions import BasePermission

from apps.core.models import Church, OrganizationMembership
from apps.core.services.configuration_access import has_configuration_permission
from infrastructure.tenancy import TenantContext


def is_communication_manager(user, organization=None, church=None):
    organization = organization or TenantContext.get()
    if church is not None and not isinstance(church, Church):
        church = Church.objects.filter(
            church_id=church,
            organization=organization,
            is_active=True,
        ).first()
        if church is None:
            return False
    return bool(
        user
        and user.is_authenticated
        and organization
        and has_configuration_permission(
            user,
            'communication.manage',
            organization,
            church=church,
        )
    )


class HasCommunicationReadAccess(BasePermission):
    message = 'Vous n’avez pas accès aux annonces de cette organisation.'

    def has_permission(self, request, view):
        organization = TenantContext.get()
        if not request.user or not request.user.is_authenticated or organization is None:
            return False
        if OrganizationMembership.objects.filter(
            user=request.user,
            organization=organization,
            is_active=True,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exists():
            return True
        return has_configuration_permission(request.user, 'communication.read', organization) or bool(
            request.user.organization_memberships.filter(organization=organization, is_active=True).exists()
        )

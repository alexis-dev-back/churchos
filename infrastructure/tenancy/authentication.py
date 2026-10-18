from uuid import UUID

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.models.organizations import OrganizationMembership
from .context import TenantContext


class TenantJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, validated_token = result
        tenant_id = validated_token.get('tenant_id')
        if tenant_id is None:
            memberships = OrganizationMembership.objects.filter(
                user=user,
                organization__is_active=True,
                is_active=True,
            ).select_related('organization')
            if memberships.count() == 1:
                organization = memberships[0].organization
                request.tenant = organization
                TenantContext.set(organization)
            else:
                request.tenant = None
                TenantContext.clear()
            return result

        try:
            tenant_id = UUID(str(tenant_id))
        except (TypeError, ValueError, AttributeError) as error:
            raise AuthenticationFailed('Jeton avec organisation invalide.') from error

        membership = OrganizationMembership.objects.filter(
            user=user,
            organization__tenant_id=tenant_id,
            organization__is_active=True,
            is_active=True,
        ).select_related('organization').first()
        if membership is None:
            raise AuthenticationFailed('Accès à cette organisation révoqué ou invalide.')

        request.tenant = membership.organization
        TenantContext.set(membership.organization)
        return result
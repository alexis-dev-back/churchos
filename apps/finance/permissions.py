from rest_framework.permissions import BasePermission

from apps.core.models import OrganizationMembership
from apps.core.services.configuration_access import has_configuration_permission
from apps.finance.models import FinanceChurchAccess, FinanceRole
from apps.members.models import Member
from infrastructure.tenancy import TenantContext


FINANCE_READ_ROLES = {
    FinanceRole.FINANCE_ADMIN,
    FinanceRole.APPROVER,
    FinanceRole.TREASURER,
    FinanceRole.ACCOUNTANT,
    FinanceRole.CONTROLLER,
    FinanceRole.AUDITOR,
    FinanceRole.CONTRIBUTION_READER,
    FinanceRole.REQUESTER,
}


def is_finance_organization_admin(user, organization=None):
    organization = organization or TenantContext.get()
    return bool(
        user
        and user.is_authenticated
        and organization
        and OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exists()
    )


def finance_accesses(user, organization=None):
    organization = organization or TenantContext.get()
    if not user or not user.is_authenticated or organization is None:
        return FinanceChurchAccess.objects.none()
    return FinanceChurchAccess.objects.filter(
        organization=organization,
        church__organization=organization,
        church__is_active=True,
        member__organization=organization,
        member__user=user,
        member__user__is_active=True,
        member__status=Member.Status.ACTIVE,
        is_active=True,
    )


def has_finance_role(user, church, roles, organization=None, permission_code=None):
    organization = organization or TenantContext.get()
    if not organization or not church or church.organization_id != organization.tenant_id:
        return False
    if finance_accesses(user, organization).filter(church=church, role__in=roles).exists():
        return True
    return bool(
        permission_code
        and has_configuration_permission(user, permission_code, organization, church=church)
    )


class IsFinanceOrganizationAdmin(BasePermission):
    message = 'Seul un administrateur de l’organisation peut attribuer les rôles financiers.'

    def has_permission(self, request, view):
        return is_finance_organization_admin(request.user)

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.permissions import BasePermission

from apps.core.models import OrganizationMembership, UserRoleAssignment
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

FULL_FINANCE_PERMISSION_CODES = {
    'finance.read',
    'finance.income.create',
    'finance.expense.create',
    'finance.expense.approve',
    'finance.payment.confirm',
    'finance.budget.manage',
    'finance.report.read',
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


def has_full_finance_control(user, church, organization=None):
    organization = organization or TenantContext.get()
    if not organization or not church or church.organization_id != organization.tenant_id:
        return False
    if finance_accesses(user, organization).filter(
        church=church,
        role=FinanceRole.FINANCE_ADMIN,
    ).exists():
        return True

    if is_finance_organization_admin(user, organization):
        now = timezone.now()
        return UserRoleAssignment.objects.filter(
            organization=organization,
            membership__user=user,
            membership__organization=organization,
            membership__is_active=True,
            role__organization=organization,
            role__is_active=True,
            starts_at__lte=now,
            revoked_at__isnull=True,
            role__permission_links__permission__is_active=True,
        ).filter(
            Q(scope=UserRoleAssignment.Scope.ORGANIZATION)
            | Q(scope=UserRoleAssignment.Scope.CHURCH, church=church),
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
        ).annotate(
            finance_permission_count=Count(
                'role__permission_links__permission__code',
                filter=Q(role__permission_links__permission__code__in=FULL_FINANCE_PERMISSION_CODES),
                distinct=True,
            ),
        ).filter(finance_permission_count=len(FULL_FINANCE_PERMISSION_CODES)).exists()

    return all(
        has_configuration_permission(user, code, organization, church=church)
        for code in FULL_FINANCE_PERMISSION_CODES
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

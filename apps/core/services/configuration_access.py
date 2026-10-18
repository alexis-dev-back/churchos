from django.db.models import Q
from django.utils import timezone

from apps.core.models import (
    ConfigurationAuditLog,
    OrganizationMembership,
    UserRoleAssignment,
)


def has_configuration_permission(user, permission_code, organization, church=None, campus=None):
    if not user or not user.is_authenticated or organization is None:
        return False
    if user.is_superuser or OrganizationMembership.objects.filter(
        user=user,
        organization=organization,
        is_active=True,
        role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
    ).exists():
        return True
    now = timezone.now()
    assignments = UserRoleAssignment.objects.filter(
        organization=organization,
        membership__user=user,
        membership__organization=organization,
        membership__is_active=True,
        role__is_active=True,
        role__permission_links__permission__code=permission_code,
        role__permission_links__permission__is_active=True,
        starts_at__lte=now,
        revoked_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).distinct()
    if church is None and campus is None:
        return assignments.filter(
            scope=UserRoleAssignment.Scope.ORGANIZATION,
        ).exists()
    if campus is not None:
        return assignments.filter(
            scope=UserRoleAssignment.Scope.ORGANIZATION,
        ).exists() or assignments.filter(
            scope=UserRoleAssignment.Scope.CHURCH,
            church_id=campus.church_id,
        ).exists() or assignments.filter(
            scope=UserRoleAssignment.Scope.CAMPUS,
            campus=campus,
        ).exists()
    return assignments.filter(
        scope=UserRoleAssignment.Scope.ORGANIZATION,
    ).exists() or assignments.filter(
        scope=UserRoleAssignment.Scope.CHURCH,
        church=church,
    ).exists()


def configuration_church_ids(user, organization, module):
    now = timezone.now()
    assignments = UserRoleAssignment.objects.filter(
        organization=organization,
        membership__user=user,
        membership__is_active=True,
        role__is_active=True,
        role__permission_links__permission__module=module,
        role__permission_links__permission__is_active=True,
        starts_at__lte=now,
        revoked_at__isnull=True,
        scope__in=[UserRoleAssignment.Scope.CHURCH, UserRoleAssignment.Scope.CAMPUS],
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    return set(assignments.values_list('church_id', flat=True).distinct())


def record_configuration_audit(
    organization,
    actor,
    action,
    object_type,
    object_id='',
    scope='',
    metadata=None,
):
    ConfigurationAuditLog.objects.create(
        organization=organization,
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        scope=scope,
        metadata=metadata or {},
    )

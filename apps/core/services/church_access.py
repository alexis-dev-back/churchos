from apps.core.models import Church, OrganizationMembership
from apps.members.models import Member


def accessible_church_ids(user, organization):
    if (
        not user
        or not user.is_authenticated
        or organization is None
        or not OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True,
        ).exists()
    ):
        return Church.objects.none().values_list('church_id', flat=True)

    member_church_ids = Member.objects.filter(
        user=user,
        organization=organization,
        status=Member.Status.ACTIVE,
        church__is_active=True,
    ).values_list('church_id', flat=True).distinct()
    if member_church_ids.exists():
        return member_church_ids

    if Member.objects.filter(user=user, organization=organization).exists():
        return member_church_ids

    is_organization_admin = OrganizationMembership.objects.filter(
        user=user,
        organization=organization,
        is_active=True,
        role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
    ).exists()
    if not is_organization_admin:
        return member_church_ids

    active_churches = Church.objects.filter(organization=organization, is_active=True)
    if active_churches.count() == 1:
        return active_churches.values_list('church_id', flat=True)
    return member_church_ids


def can_access_church(user, organization, church):
    if church is None or church.organization_id != getattr(organization, 'tenant_id', None):
        return False
    return accessible_church_ids(user, organization).filter(church_id=church.church_id).exists()

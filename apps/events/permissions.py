from django.db.models import F
from rest_framework.permissions import BasePermission

from apps.core.models import OrganizationMembership
from apps.core.services.configuration_access import (
    configuration_church_ids,
    has_configuration_permission,
)
from apps.events.models import ChurchEventAdministrator, Event
from apps.members.models import Member
from infrastructure.tenancy import TenantContext


def is_organization_event_admin(user, organization=None):
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


def managed_church_ids(user, organization=None):
    organization = organization or TenantContext.get()
    if not user or not user.is_authenticated or organization is None:
        return []
    return list(ChurchEventAdministrator.objects.filter(
        member__user=user,
        member__organization=organization,
        member__status=Member.Status.ACTIVE,
        member__user__is_active=True,
        member__church_id=F('church_id'),
        church__organization=organization,
        church__is_active=True,
    ).values_list('church_id', flat=True))


def event_is_within_churches(event, church_ids):
    return bool(
        (event.scope == 'CHURCH' and event.church_id in church_ids)
        or (event.scope == 'CAMPUS' and event.campus.church_id in church_ids)
    )


def can_manage_event(user, event, organization=None):
    organization = organization or TenantContext.get()
    if event.organization_id != getattr(organization, 'tenant_id', None):
        return False
    if is_organization_event_admin(user, organization):
        return True
    return event_is_within_churches(event, managed_church_ids(user, organization))


class IsEventManager(BasePermission):
    message = 'Vous devez administrer cette église ou son organisation pour gérer ses événements.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        organization = TenantContext.get()
        if organization is None:
            return False
        if is_organization_event_admin(request.user, organization):
            return True

        permission_code = _event_permission_code(request, view)
        church_ids = managed_church_ids(request.user, organization)
        kwargs = view.kwargs

        event_id = kwargs.get('event_id')
        if event_id:
            event = Event.objects.filter(pk=event_id, organization=organization).select_related('campus').first()
            if event is None:
                return False
            if _has_event_permission(request.user, organization, permission_code, event):
                return True
            return event_is_within_churches(event, church_ids)

        occurrence_id = kwargs.get('occurrence_id')
        if occurrence_id:
            event = Event.objects.filter(
                occurrences__id=occurrence_id,
                organization=organization,
            ).select_related('campus').first()
            if event is None:
                return False
            if _has_event_permission(request.user, organization, permission_code, event):
                return True
            return event_is_within_churches(event, church_ids)

        registration_id = kwargs.get('registration_id')
        if registration_id:
            event = Event.objects.filter(
                occurrences__registrations__id=registration_id,
                organization=organization,
            ).select_related('campus').first()
            if event is None:
                return False
            if _has_event_permission(request.user, organization, permission_code, event):
                return True
            return event_is_within_churches(event, church_ids)

        attendance_id = kwargs.get('attendance_id')
        if attendance_id:
            event = Event.objects.filter(
                occurrences__attendances__id=attendance_id,
                organization=organization,
            ).select_related('campus').first()
            if event is None:
                return False
            if _has_event_permission(request.user, organization, permission_code, event):
                return True
            return event_is_within_churches(event, church_ids)

        if request.method == 'GET' and view.__class__.__name__ == 'EventListCreateView':
            return has_configuration_permission(request.user, 'events.read', organization) or bool(church_ids) or bool(
                configuration_church_ids(request.user, organization, 'events')
            )
        if request.method == 'POST' and view.__class__.__name__ == 'EventListCreateView':
            data = request.data
            if data.get('scope') == 'CHURCH':
                from apps.core.models import Church

                church = Church.objects.filter(
                    church_id=data.get('church_id'),
                    organization=organization,
                    is_active=True,
                ).first()
                return bool(
                    church
                    and (
                        has_configuration_permission(request.user, 'events.create', organization, church=church)
                        or str(church.church_id) in {str(church_id) for church_id in church_ids}
                    )
                )
            if data.get('scope') == 'CAMPUS':
                from apps.core.models import Campus

                campus = Campus.objects.filter(
                    campus_id=data.get('campus_id'),
                    church__organization=organization,
                    church_id__in=church_ids,
                    is_active=True,
                ).select_related('church').first()
                if campus is None:
                    campus = Campus.objects.filter(
                        campus_id=data.get('campus_id'),
                        church__organization=organization,
                        is_active=True,
                        church__is_active=True,
                    ).select_related('church').first()
                return bool(
                    campus
                    and (
                        has_configuration_permission(
                            request.user,
                            'events.create',
                            organization,
                            church=campus.church,
                            campus=campus,
                        )
                        or str(campus.church_id) in {str(church_id) for church_id in church_ids}
                    )
                )
        return view.__class__.__name__ == 'RegistrationScanView' and bool(church_ids)

    def has_object_permission(self, request, view, obj):
        return can_manage_event(request.user, obj)


def _event_permission_code(request, view):
    if request.method == 'GET':
        return 'events.read'
    if request.method == 'POST' and view.__class__.__name__ == 'EventListCreateView':
        return 'events.create'
    if (
        'Cancel' in view.__class__.__name__
        or request.data.get('status') == 'CANCELLED'
    ):
        return 'events.cancel'
    return 'events.update'


def _has_event_permission(user, organization, permission_code, event):
    church = event.church
    campus = event.campus
    if campus is not None:
        church = campus.church
    return has_configuration_permission(
        user,
        permission_code,
        organization,
        church=church,
        campus=campus,
    )

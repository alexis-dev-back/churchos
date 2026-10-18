from rest_framework.permissions import BasePermission

from apps.core.models import Church, OrganizationMembership
from apps.core.services.configuration_access import (
    configuration_church_ids,
    has_configuration_permission,
)
from apps.events.permissions import managed_church_ids
from apps.attendance.models import AttendanceAlert, AttendanceRecord, AttendanceSession
from infrastructure.tenancy import TenantContext


class IsAttendanceManager(BasePermission):
    message = 'Vous devez être administrateur ou responsable habilité de cette église pour gérer les présences.'

    def has_permission(self, request, view):
        organization = TenantContext.get()
        is_authenticated = bool(
            request.user
            and request.user.is_authenticated
            and organization
        )
        if not is_authenticated:
            return False
        if is_attendance_organization_admin(request.user, organization):
            return True
        church_ids = managed_church_ids(request.user, organization)
        permission_code = _attendance_permission_code(request, view)
        if request.method == 'GET' and has_configuration_permission(
            request.user,
            'attendance.read',
            organization,
        ):
            return True
        configured_church_ids = configuration_church_ids(request.user, organization, 'attendance')
        if (
            view.__class__.__name__ == 'AttendanceManagerContextView'
            and configured_church_ids
        ):
            return True
        requested_church_id = request.data.get('church_id') or request.query_params.get('church_id')
        if requested_church_id:
            church = Church.objects.filter(
                church_id=requested_church_id,
                organization=organization,
                is_active=True,
            ).first()
            if church and has_configuration_permission(
                request.user,
                permission_code,
                organization,
                church=church,
            ):
                return True
        session_id = view.kwargs.get('session_id')
        if session_id:
            session = AttendanceSession.objects.filter(
                pk=session_id,
                organization=organization,
            ).select_related(
                'church',
                'campus',
                'event_occurrence__event__church',
                'event_occurrence__event__campus',
            ).first()
            if session and _has_attendance_permission(request.user, organization, permission_code, session):
                return True
        if view.__class__.__name__ == 'AttendanceSessionListCreateView' and request.method == 'GET':
            return bool(church_ids or configured_church_ids)
        if not church_ids and not configured_church_ids:
            return False

        if view.__class__.__name__ == 'AttendanceManagerContextView':
            return True
        if view.__class__.__name__ in ('AttendanceSessionTypeListCreateView', 'AttendancePolicyListView'):
            return request.method == 'GET'
        if view.__class__.__name__ == 'AttendanceMemberListView':
            return True
        if view.__class__.__name__ == 'AttendanceSessionListCreateView':
            if request.method == 'GET':
                return True
            church_id = request.data.get('church_id')
            return str(church_id) in {str(value) for value in church_ids}
        if view.__class__.__name__ == 'AttendanceWeeklyReportView':
            return str(request.query_params.get('church_id', '')) in {str(value) for value in church_ids}
        if view.__class__.__name__ == 'AttendanceAlertListView':
            return True

        if session_id:
            session = AttendanceSession.objects.filter(pk=session_id, organization=organization).select_related(
                'event_occurrence__event',
            ).first()
            return session is not None and _can_manage_session(session, request.user, organization, church_ids)

        attendance_id = view.kwargs.get('attendance_id')
        if attendance_id:
            attendance = AttendanceRecord.objects.filter(
                pk=attendance_id,
                expected_participant__session__organization=organization,
            ).select_related('expected_participant__session__event_occurrence__event').first()
            return attendance is not None and _can_manage_session(
                attendance.expected_participant.session,
                request.user,
                organization,
                church_ids,
            )

        excuse_id = view.kwargs.get('excuse_id')
        if excuse_id:
            attendance = AttendanceRecord.objects.filter(
                excuses__id=excuse_id,
                expected_participant__session__organization=organization,
            ).select_related('expected_participant__session__event_occurrence__event').first()
            return attendance is not None and _can_manage_session(
                attendance.expected_participant.session,
                request.user,
                organization,
                church_ids,
            )

        alert_id = view.kwargs.get('alert_id')
        if alert_id:
            alert = AttendanceAlert.objects.filter(
                pk=alert_id,
                organization=organization,
                member__church_id__in=church_ids,
            ).first()
            return alert is not None
        return False


class IsAttendanceOrganizationAdmin(BasePermission):
    message = 'La configuration du module est réservée à un administrateur de l’organisation.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and is_attendance_organization_admin(request.user, TenantContext.get())
        )


def is_attendance_organization_admin(user, organization):
    return bool(
        organization
        and OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exists()
    )


def _can_manage_session(session, user, organization, church_ids):
    if session.organization_id != organization.tenant_id:
        return False
    if session.church_id:
        return session.church_id in church_ids
    if session.event_occurrence_id:
        from apps.events.permissions import can_manage_event

        return can_manage_event(user, session.event_occurrence.event, organization)
    return False


def _attendance_permission_code(request, view):
    if request.method == 'GET':
        return 'attendance.read'
    if view.__class__.__name__ == 'AttendanceSessionListCreateView':
        return 'attendance.create'
    return 'attendance.validate'


def _has_attendance_permission(user, organization, permission_code, session):
    church = session.church
    campus = session.campus
    if session.event_occurrence_id:
        event = session.event_occurrence.event
        campus = event.campus
        church = event.church or (campus.church if campus else None)
    if church is None:
        return False
    return has_configuration_permission(
        user,
        permission_code,
        organization,
        church=church,
        campus=campus,
    )

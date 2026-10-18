from rest_framework.permissions import BasePermission

from apps.core.models import OrganizationMembership
from apps.core.services.configuration_access import has_configuration_permission
from infrastructure.tenancy import TenantContext


class IsTenantAdmin(BasePermission):
    message = 'Vous ne disposez pas des permissions requises pour cette opération.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        organization = TenantContext.get()
        if organization is None:
            return False
        membership = OrganizationMembership.objects.filter(
            user=request.user,
            organization=organization,
            is_active=True,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exists()
        if membership:
            return True

        module = _module_for_view(view)
        if module is None:
            return False
        permission_code = _permission_for_request(module, request, view)
        return has_configuration_permission(
            request.user,
            permission_code,
            organization,
        )


class IsMemberOwnerOrAdmin(BasePermission):
    message = 'Vous ne pouvez pas consulter ce profil.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if hasattr(view, 'member') and view.member is not None:
            return request.user == view.member.user

        organization = TenantContext.get()
        if organization is None:
            return False

        if OrganizationMembership.objects.filter(
            user=request.user,
            organization=organization,
            is_active=True,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exists():
            return True

        return has_configuration_permission(
            request.user,
            'members.read',
            organization,
        )


def _module_for_view(view):
    module_path = view.__class__.__module__
    if '.members.views.' in module_path:
        return 'members'
    if '.people.views.' in module_path:
        return 'people'
    if '.families.views.' in module_path:
        return 'families'
    if '.ministries.' in module_path:
        return 'ministries'
    return None


def _permission_for_request(module, request, view):
    if module == 'members':
        view_name = view.__class__.__name__
        if view_name in {'MemberSuspendView', 'MemberArchiveView'}:
            action = 'delete'
        elif view_name in {'MemberInvitationView', 'MemberResendInvitationView'}:
            action = 'create'
        elif request.method == 'GET':
            action = 'read'
        elif request.method == 'POST':
            action = 'create'
        else:
            action = 'update'
        return f'members.{action}'
    if module == 'people':
        return 'people.read' if request.method == 'GET' else 'people.manage'
    if module == 'families':
        return 'families.read' if request.method == 'GET' else 'families.manage'
    if module == 'ministries':
        return 'ministries.read' if request.method == 'GET' else 'ministries.manage'
    return f'{module}.read'

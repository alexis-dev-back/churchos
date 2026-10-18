from rest_framework.permissions import BasePermission
from django.db import models

from apps.core.models import Church, OrganizationMembership
from apps.core.services.configuration_access import (
    configuration_church_ids,
    has_configuration_permission,
)
from apps.ecodim.models import EcodimStaffAssignment
from apps.members.models import Member
from infrastructure.tenancy import TenantContext


def is_ecodim_organization_admin(user, organization):
    return bool(
        organization
        and user
        and user.is_authenticated
        and OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exists()
    )


def accessible_ecodim_churches(user, organization):
    if is_ecodim_organization_admin(user, organization):
        return Church.objects.filter(organization=organization, is_active=True)
    staff_church_ids = EcodimStaffAssignment.objects.filter(
        member__user=user,
        member__organization=organization,
        member__church_id=models.F('church_id'),
        member__status=Member.Status.ACTIVE,
        member__user__is_active=True,
        church__organization=organization,
        church__is_active=True,
        is_active=True,
    ).values_list('church_id', flat=True)
    if has_configuration_permission(user, 'ecodim.read', organization):
        return Church.objects.filter(organization=organization, is_active=True)
    configured_church_ids = configuration_church_ids(user, organization, 'ecodim')
    return Church.objects.filter(
        church_id__in=set(staff_church_ids) | configured_church_ids,
        organization=organization,
        is_active=True,
    )


class HasEcodimAccess(BasePermission):
    message = 'Vous devez être administrateur ou membre habilité de l’ECODIM.'

    def has_permission(self, request, view):
        organization = TenantContext.get()
        if not request.user or not request.user.is_authenticated or organization is None:
            return False
        if is_ecodim_organization_admin(request.user, organization):
            return True
        return has_configuration_permission(request.user, 'ecodim.read', organization) or bool(
            configuration_church_ids(request.user, organization, 'ecodim')
        ) or EcodimStaffAssignment.objects.filter(
            member__user=request.user,
            member__organization=organization,
            member__church_id=models.F('church_id'),
            member__status=Member.Status.ACTIVE,
            member__user__is_active=True,
            church__organization=organization,
            church__is_active=True,
            is_active=True,
        ).exists()


class HasEcodimManagementAccess(HasEcodimAccess):
    message = 'La gestion de cette ressource est réservée à la direction ECODIM ou à un administrateur.'

    def has_permission(self, request, view):
        organization = TenantContext.get()
        return has_ecodim_management_access(request.user, organization)


def has_ecodim_management_access(user, organization):
    if is_ecodim_organization_admin(user, organization):
        return True
    if has_configuration_permission(user, 'ecodim.manage', organization):
        return True
    return EcodimStaffAssignment.objects.filter(
        member__user=user,
        member__organization=organization,
        member__church_id=models.F('church_id'),
        member__status=Member.Status.ACTIVE,
        member__user__is_active=True,
        church__organization=organization,
        church__is_active=True,
        is_active=True,
        role=EcodimStaffAssignment.Role.DIRECTOR,
    ).exists()


class HasEcodimChildSafetyAccess(HasEcodimAccess):
    message = 'Seuls les responsables habilités à la sécurité des enfants peuvent gérer les remises.'

    def has_permission(self, request, view):
        organization = TenantContext.get()
        if is_ecodim_organization_admin(request.user, organization):
            return True
        return EcodimStaffAssignment.objects.filter(
            member__user=request.user,
            member__organization=organization,
            member__church_id=models.F('church_id'),
            member__status=Member.Status.ACTIVE,
            member__user__is_active=True,
            church__organization=organization,
            church__is_active=True,
            is_active=True,
            role__in=[
                EcodimStaffAssignment.Role.DIRECTOR,
                EcodimStaffAssignment.Role.CHILD_SAFETY,
                EcodimStaffAssignment.Role.CHECK_IN,
            ],
        ).exists()
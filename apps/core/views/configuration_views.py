from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import (
    AppPermission,
    Campus,
    Church,
    ChurchSettings,
    ConfigurationAuditLog,
    OrganizationMembership,
    OrganizationRole,
    UserRoleAssignment,
)
from apps.core.configuration_serializers import (
    AppPermissionSerializer,
    ChurchSettingsSerializer,
    OrganizationRolePermissionsSerializer,
    OrganizationRoleSerializer,
    OrganizationRoleWriteSerializer,
    UserRoleAssignmentSerializer,
    UserRoleAssignmentWriteSerializer,
)
from apps.core.services.configuration_access import (
    has_configuration_permission,
    record_configuration_audit,
)
from infrastructure.tenancy import TenantContext


User = get_user_model()


def _organization():
    organization = TenantContext.get()
    if organization is None:
        raise PermissionDenied('Aucune organisation active dans le contexte de la requête.')
    return organization


def _require_permission(request, organization, code, church=None, campus=None):
    if not has_configuration_permission(
        request.user,
        code,
        organization,
        church=church,
        campus=campus,
    ):
        raise PermissionDenied('Vous ne disposez pas de la permission requise.')


def _paginated(request, queryset, serializer_class, page_size=50):
    page = max(int(request.query_params.get('page', '1')), 1)
    limit = min(max(int(request.query_params.get('page_size', str(page_size))), 1), 100)
    start = (page - 1) * limit
    return Response({
        'count': queryset.count(),
        'page': page,
        'page_size': limit,
        'results': serializer_class(queryset[start:start + limit], many=True).data,
    })


class ConfigurationPermissionCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        _require_permission(request, organization, 'roles.read')
        queryset = AppPermission.objects.filter(is_active=True).order_by('module', 'action', 'code')
        return Response(AppPermissionSerializer(queryset, many=True).data)


class ConfigurationAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        membership = OrganizationMembership.objects.filter(
            user=request.user,
            organization=organization,
            is_active=True,
        ).first()
        if membership is None:
            return Response({'permission_codes': []})
        if membership.role in {OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN}:
            codes = AppPermission.objects.filter(is_active=True).values_list('code', flat=True)
            return Response({'permission_codes': list(codes)})
        now = timezone.now()
        codes = AppPermission.objects.filter(
            is_active=True,
            role_links__role__assignments__organization=organization,
            role_links__role__assignments__membership=membership,
            role_links__role__assignments__role__is_active=True,
            role_links__role__assignments__starts_at__lte=now,
            role_links__role__assignments__revoked_at__isnull=True,
        ).filter(
            Q(role_links__role__assignments__expires_at__isnull=True)
            | Q(role_links__role__assignments__expires_at__gt=now)
        ).values_list('code', flat=True).distinct()
        return Response({'permission_codes': list(codes)})


class OrganizationRoleListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        _require_permission(request, organization, 'roles.read')
        roles = OrganizationRole.objects.filter(organization=organization).prefetch_related('permissions')
        return _paginated(request, roles, OrganizationRoleSerializer)

    @transaction.atomic
    def post(self, request):
        organization = _organization()
        _require_permission(request, organization, 'roles.manage')
        serializer = OrganizationRoleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data['code']
        if OrganizationRole.objects.filter(organization=organization, code=code).exists():
            raise ValidationError({'code': 'Ce code de rôle est déjà utilisé dans cette organisation.'})
        role = serializer.save(organization=organization, created_by=request.user, is_system=False)
        record_configuration_audit(
            organization,
            request.user,
            'ROLE_CREATED',
            'OrganizationRole',
            role.pk,
            metadata={'name': role.name, 'code': role.code},
        )
        return Response(OrganizationRoleSerializer(role).data, status=status.HTTP_201_CREATED)


class OrganizationRoleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, organization, role_id):
        return get_object_or_404(
            OrganizationRole.objects.prefetch_related('permissions'),
            organization=organization,
            pk=role_id,
        )

    def get(self, request, role_id):
        organization = _organization()
        _require_permission(request, organization, 'roles.read')
        role = self.get_object(organization, role_id)
        return Response(OrganizationRoleSerializer(role).data)

    @transaction.atomic
    def patch(self, request, role_id):
        organization = _organization()
        _require_permission(request, organization, 'roles.manage')
        role = self.get_object(organization, role_id)
        if role.is_system:
            raise ValidationError({'detail': 'Un rôle système ne peut pas être modifié.'})
        serializer = OrganizationRoleWriteSerializer(role, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data.get('code')
        if code and OrganizationRole.objects.filter(
            organization=organization,
            code=code,
        ).exclude(pk=role.pk).exists():
            raise ValidationError({'code': 'Ce code de rôle est déjà utilisé dans cette organisation.'})
        serializer.save()
        record_configuration_audit(
            organization,
            request.user,
            'ROLE_UPDATED',
            'OrganizationRole',
            role.pk,
            metadata={'fields': sorted(serializer.validated_data.keys())},
        )
        return Response(OrganizationRoleSerializer(role).data)


class OrganizationRolePermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, role_id):
        organization = _organization()
        _require_permission(request, organization, 'roles.manage')
        role = get_object_or_404(
            OrganizationRole.objects.select_for_update(),
            organization=organization,
            pk=role_id,
        )
        if role.is_system:
            raise ValidationError({'detail': 'Les permissions d’un rôle système ne peuvent pas être modifiées.'})
        serializer = OrganizationRolePermissionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        permissions = serializer.validated_data['permission_codes']
        role.permissions.set(permissions)
        record_configuration_audit(
            organization,
            request.user,
            'ROLE_PERMISSIONS_UPDATED',
            'OrganizationRole',
            role.pk,
            metadata={'permission_codes': sorted(permission.code for permission in permissions)},
        )
        role = OrganizationRole.objects.prefetch_related('permissions').get(pk=role.pk)
        return Response(OrganizationRoleSerializer(role).data)


class UserRoleAssignmentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        _require_permission(request, organization, 'roles.read')
        assignments = UserRoleAssignment.objects.filter(
            organization=organization,
        ).select_related('membership__user', 'role', 'church', 'campus')
        membership_id = request.query_params.get('membership_id')
        if membership_id:
            assignments = assignments.filter(membership_id=membership_id)
        return _paginated(request, assignments, UserRoleAssignmentSerializer)

    @transaction.atomic
    def post(self, request):
        organization = _organization()
        _require_permission(request, organization, 'roles.manage')
        serializer = UserRoleAssignmentWriteSerializer(
            data=request.data,
            context={'organization': organization},
        )
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        duplicate = UserRoleAssignment.objects.filter(
            organization=organization,
            membership=values['membership'],
            role=values['role'],
            scope=values['scope'],
            revoked_at__isnull=True,
        )
        if values.get('church'):
            duplicate = duplicate.filter(church=values['church'])
        else:
            duplicate = duplicate.filter(church__isnull=True)
        if values.get('campus'):
            duplicate = duplicate.filter(campus=values['campus'])
        else:
            duplicate = duplicate.filter(campus__isnull=True)
        if duplicate.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).exists():
            raise ValidationError({'role_id': 'Ce rôle est déjà attribué pour ce périmètre.'})
        assignment = UserRoleAssignment(
            organization=organization,
            membership=values['membership'],
            role=values['role'],
            scope=values['scope'],
            church=values.get('church'),
            campus=values.get('campus'),
            starts_at=values.get('starts_at', timezone.now()),
            expires_at=values.get('expires_at'),
            created_by=request.user,
        )
        assignment.full_clean()
        assignment.save()
        record_configuration_audit(
            organization,
            request.user,
            'ROLE_ASSIGNED',
            'UserRoleAssignment',
            assignment.pk,
            scope=assignment.scope,
            metadata={
                'membership_id': assignment.membership_id,
                'role_id': str(assignment.role_id),
                'church_id': str(assignment.church_id) if assignment.church_id else None,
                'campus_id': str(assignment.campus_id) if assignment.campus_id else None,
                'expires_at': assignment.expires_at.isoformat() if assignment.expires_at else None,
            },
        )
        assignment = UserRoleAssignment.objects.select_related(
            'membership__user',
            'role',
            'church',
            'campus',
        ).get(pk=assignment.pk)
        return Response(UserRoleAssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED)


class UserRoleAssignmentRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, assignment_id):
        organization = _organization()
        _require_permission(request, organization, 'roles.manage')
        assignment = get_object_or_404(
            UserRoleAssignment.objects.select_for_update().select_related('membership__user', 'role'),
            organization=organization,
            pk=assignment_id,
        )
        if assignment.revoked_at is None:
            assignment.revoked_at = timezone.now()
            assignment.save(update_fields=['revoked_at'])
            record_configuration_audit(
                organization,
                request.user,
                'ROLE_REVOKED',
                'UserRoleAssignment',
                assignment.pk,
                scope=assignment.scope,
                metadata={'membership_id': assignment.membership_id, 'role_id': str(assignment.role_id)},
            )
        return Response(UserRoleAssignmentSerializer(assignment).data)


class ConfigurationChurchSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def _church(self, organization, church_id):
        return get_object_or_404(Church, organization=organization, church_id=church_id, is_active=True)

    def get(self, request):
        organization = _organization()
        church = self._church(organization, request.query_params.get('church_id'))
        _require_permission(request, organization, 'church_settings.read', church=church)
        settings_record, _ = ChurchSettings.objects.get_or_create(
            church=church,
            defaults={'updated_by': request.user},
        )
        return Response(ChurchSettingsSerializer(settings_record, context={'request': request}).data)

    @transaction.atomic
    def patch(self, request):
        organization = _organization()
        church = self._church(organization, request.query_params.get('church_id'))
        _require_permission(request, organization, 'church_settings.update', church=church)
        settings_record, _ = ChurchSettings.objects.select_for_update().get_or_create(
            church=church,
            defaults={'updated_by': request.user},
        )
        serializer = ChurchSettingsSerializer(
            settings_record,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        church_fields = serializer.validated_data.pop('church', {})
        previous_logos = {
            field: getattr(settings_record, field)
            for field in ('logo_primary', 'logo_secondary')
            if field in serializer.validated_data
        }
        serializer.save(updated_by=request.user)
        for field, old_logo in previous_logos.items():
            new_logo = getattr(settings_record, field)
            if old_logo and old_logo.name != getattr(new_logo, 'name', None):
                old_logo.storage.delete(old_logo.name)
        church_update_fields = []
        for field, value in church_fields.items():
            setattr(church, field, value)
            church_update_fields.append(field)
        if church_update_fields:
            church.save(update_fields=[*church_update_fields, 'update_date'])
        record_configuration_audit(
            organization,
            request.user,
            'CHURCH_SETTINGS_UPDATED',
            'Church',
            church.church_id,
            scope='CHURCH',
            metadata={'fields': sorted(request.data.keys())},
        )
        return Response(ChurchSettingsSerializer(settings_record, context={'request': request}).data)


class ConfigurationUsersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        _require_permission(request, organization, 'users.read')
        memberships = OrganizationMembership.objects.filter(
            organization=organization,
        ).select_related('user').order_by('user__username')
        query = request.query_params.get('search', '').strip()
        if query:
            memberships = memberships.filter(
                Q(user__username__icontains=query)
                | Q(user__email__icontains=query)
                | Q(user__first_name__icontains=query)
                | Q(user__last_name__icontains=query)
            )
        active_filter = request.query_params.get('active')
        if active_filter in {'true', 'false'}:
            memberships = memberships.filter(is_active=active_filter == 'true')
        results = [{
            'membership_id': membership.pk,
            'user_id': membership.user_id,
            'username': membership.user.get_username(),
            'email': membership.user.email,
            'first_name': membership.user.first_name,
            'last_name': membership.user.last_name,
            'role': membership.role,
            'is_active': membership.is_active,
            'account_is_active': membership.user.is_active,
            'date_created': membership.date_created,
        } for membership in memberships]
        page = max(int(request.query_params.get('page', '1')), 1)
        page_size = min(max(int(request.query_params.get('page_size', '50')), 1), 100)
        start = (page - 1) * page_size
        return Response({
            'count': len(results),
            'page': page,
            'page_size': page_size,
            'results': results[start:start + page_size],
        })

    @transaction.atomic
    def post(self, request):
        organization = _organization()
        _require_permission(request, organization, 'users.manage')
        username = str(request.data.get('username', '')).strip()
        if not username:
            raise ValidationError({'username': 'Le nom utilisateur est obligatoire.'})
        user = get_object_or_404(User, username=username)
        if OrganizationMembership.objects.filter(organization=organization, user=user).exists():
            raise ValidationError({'username': 'Cet utilisateur appartient déjà à cette organisation.'})
        membership = OrganizationMembership.objects.create(
            organization=organization,
            user=user,
            role=OrganizationMembership.Role.MEMBER,
        )
        record_configuration_audit(
            organization,
            request.user,
            'USER_ADDED_TO_ORGANIZATION',
            'OrganizationMembership',
            membership.pk,
            metadata={'user_id': user.pk, 'role': membership.role},
        )
        return Response({
            'membership_id': membership.pk,
            'user_id': user.pk,
            'username': user.get_username(),
            'email': user.email,
            'role': membership.role,
            'is_active': membership.is_active,
            'account_is_active': user.is_active,
        }, status=status.HTTP_201_CREATED)


class ConfigurationUserDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, membership_id):
        organization = _organization()
        _require_permission(request, organization, 'users.manage')
        membership = get_object_or_404(
            OrganizationMembership.objects.select_for_update().select_related('user'),
            organization=organization,
            pk=membership_id,
        )
        requester_role = OrganizationMembership.objects.get(
            organization=organization,
            user=request.user,
            is_active=True,
        ).role
        if 'role' in request.data:
            new_role = request.data['role']
            if new_role not in OrganizationMembership.Role.values:
                raise ValidationError({'role': 'Rôle organisation non reconnu.'})
            if requester_role != OrganizationMembership.Role.OWNER and new_role != OrganizationMembership.Role.MEMBER:
                raise PermissionDenied('Seul le propriétaire peut attribuer un rôle propriétaire ou administrateur.')
        new_active = request.data.get('is_active', membership.is_active)
        removes_admin = (
            membership.is_active
            and membership.role in {OrganizationMembership.Role.ADMIN, OrganizationMembership.Role.OWNER}
            and (not new_active or request.data.get('role', membership.role) not in {
                OrganizationMembership.Role.ADMIN,
                OrganizationMembership.Role.OWNER,
            })
        )
        if removes_admin and not OrganizationMembership.objects.filter(
            organization=organization,
            is_active=True,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exclude(pk=membership.pk).exists():
            raise ValidationError({'role': 'Impossible de désactiver ou rétrograder le dernier administrateur de cette organisation.'})
        if 'is_active' in request.data:
            if not isinstance(new_active, bool):
                raise ValidationError({'is_active': 'Ce champ doit être booléen.'})
            membership.is_active = new_active
        if 'role' in request.data:
            membership.role = request.data['role']
        membership.save(update_fields=['is_active', 'role'])
        record_configuration_audit(
            organization,
            request.user,
            'ORGANIZATION_MEMBERSHIP_UPDATED',
            'OrganizationMembership',
            membership.pk,
            metadata={'is_active': membership.is_active, 'role': membership.role},
        )
        return Response({
            'membership_id': membership.pk,
            'user_id': membership.user_id,
            'username': membership.user.get_username(),
            'email': membership.user.email,
            'role': membership.role,
            'is_active': membership.is_active,
            'account_is_active': membership.user.is_active,
        })


class ConfigurationAuditLogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        _require_permission(request, organization, 'audit.read')
        logs = ConfigurationAuditLog.objects.filter(
            organization=organization,
        ).select_related('actor').order_by('-created_at')
        action = request.query_params.get('action')
        if action:
            logs = logs.filter(action=action)
        page = max(int(request.query_params.get('page', '1')), 1)
        page_size = min(max(int(request.query_params.get('page_size', '50')), 1), 100)
        start = (page - 1) * page_size
        return Response({
            'count': logs.count(),
            'page': page,
            'page_size': page_size,
            'results': [{
                'id': str(log.pk),
                'actor': log.actor.get_username() if log.actor else None,
                'action': log.action,
                'object_type': log.object_type,
                'object_id': log.object_id,
                'scope': log.scope,
                'result': log.result,
                'metadata': log.metadata,
                'created_at': log.created_at,
            } for log in logs[start:start + page_size]],
        })

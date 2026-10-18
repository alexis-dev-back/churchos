import zlib

from rest_framework import serializers

from apps.core.models import (
    AppPermission,
    ChurchSettings,
    OrganizationRole,
    UserRoleAssignment,
)

MAX_CHURCH_LOGO_SIZE = 3 * 1024 * 1024


def validate_church_logo(upload):
    if upload.size > MAX_CHURCH_LOGO_SIZE:
        raise serializers.ValidationError('Le logo ne peut pas dépasser 3 Mo.')
    header = upload.read(33)
    upload.seek(0)
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        valid_png = (
            len(header) >= 33
            and header[8:12] == b'\x00\x00\x00\r'
            and header[12:16] == b'IHDR'
            and int.from_bytes(header[16:20], 'big') > 0
            and int.from_bytes(header[20:24], 'big') > 0
            and zlib.crc32(header[12:29]) & 0xffffffff == int.from_bytes(header[29:33], 'big')
        )
        if valid_png:
            return upload
    elif header.startswith(b'\xff\xd8\xff'):
        upload.seek(-2, 2)
        valid_jpeg = upload.read(2) == b'\xff\xd9'
        upload.seek(0)
        if valid_jpeg:
            return upload
    elif (
        len(header) >= 12
        and header.startswith(b'RIFF')
        and header[8:12] == b'WEBP'
        and int.from_bytes(header[4:8], 'little') == upload.size - 8
    ):
        return upload
    if upload.size < 8:
        raise serializers.ValidationError('Le fichier doit être une image PNG, JPEG ou WebP valide.')
    raise serializers.ValidationError('Le fichier doit être une image PNG, JPEG ou WebP valide.')


class AppPermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppPermission
        fields = ['id', 'code', 'module', 'action', 'label', 'description']
        read_only_fields = fields


class OrganizationRoleSerializer(serializers.ModelSerializer):
    permission_codes = serializers.SerializerMethodField()
    assigned_users = serializers.IntegerField(source='assignments.count', read_only=True)

    class Meta:
        model = OrganizationRole
        fields = [
            'id', 'name', 'code', 'description', 'is_active', 'is_system',
            'permission_codes', 'assigned_users', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'is_system', 'permission_codes', 'assigned_users', 'created_at', 'updated_at']

    def get_permission_codes(self, instance):
        return list(instance.permissions.filter(is_active=True).values_list('code', flat=True))


class OrganizationRoleWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationRole
        fields = ['name', 'code', 'description', 'is_active']


class OrganizationRolePermissionsSerializer(serializers.Serializer):
    permission_codes = serializers.ListField(
        child=serializers.CharField(max_length=100),
        allow_empty=True,
        max_length=100,
    )

    def validate_permission_codes(self, values):
        if len(set(values)) != len(values):
            raise serializers.ValidationError('Une permission ne peut apparaître qu’une seule fois.')
        permissions = AppPermission.objects.filter(code__in=values, is_active=True)
        found = set(permissions.values_list('code', flat=True))
        unknown = set(values) - found
        if unknown:
            raise serializers.ValidationError(
                f'Permissions inconnues ou inactives : {", ".join(sorted(unknown))}.'
            )
        return list(permissions)


class UserRoleAssignmentWriteSerializer(serializers.Serializer):
    membership_id = serializers.IntegerField()
    role_id = serializers.UUIDField()
    scope = serializers.ChoiceField(choices=UserRoleAssignment.Scope.choices)
    church_id = serializers.UUIDField(required=False)
    campus_id = serializers.UUIDField(required=False)
    starts_at = serializers.DateTimeField(required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        from django.shortcuts import get_object_or_404

        from apps.core.models import Campus, Church, OrganizationMembership, OrganizationRole

        organization = self.context['organization']
        membership = get_object_or_404(
            OrganizationMembership,
            pk=attrs['membership_id'],
            organization=organization,
            is_active=True,
        )
        role = get_object_or_404(
            OrganizationRole,
            pk=attrs['role_id'],
            organization=organization,
            is_active=True,
        )
        attrs['membership'] = membership
        attrs['role'] = role
        scope = attrs['scope']
        church_id = attrs.get('church_id')
        campus_id = attrs.get('campus_id')
        if scope == UserRoleAssignment.Scope.ORGANIZATION:
            if church_id or campus_id:
                raise serializers.ValidationError('Le périmètre organisation ne doit pas contenir d’église ou de campus.')
        elif scope == UserRoleAssignment.Scope.CHURCH:
            if not church_id or campus_id:
                raise serializers.ValidationError('Sélectionnez une église sans campus pour ce périmètre.')
            attrs['church'] = get_object_or_404(
                Church,
                church_id=church_id,
                organization=organization,
                is_active=True,
            )
        else:
            if not campus_id:
                raise serializers.ValidationError({'campus_id': 'Le campus est obligatoire pour ce périmètre.'})
            campus = get_object_or_404(
                Campus.objects.select_related('church'),
                campus_id=campus_id,
                church__organization=organization,
                church__is_active=True,
                is_active=True,
            )
            if church_id and str(campus.church_id) != str(church_id):
                raise serializers.ValidationError({'campus_id': 'Ce campus ne dépend pas de l’église sélectionnée.'})
            attrs['campus'] = campus
            attrs['church'] = campus.church
        starts_at = attrs.get('starts_at')
        expires_at = attrs.get('expires_at')
        if starts_at and expires_at and expires_at <= starts_at:
            raise serializers.ValidationError({'expires_at': 'La date d’expiration doit suivre la date de début.'})
        return attrs


class UserRoleAssignmentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='membership.user.username', read_only=True)
    email = serializers.EmailField(source='membership.user.email', read_only=True)
    role_name = serializers.CharField(source='role.name', read_only=True)
    role_code = serializers.CharField(source='role.code', read_only=True)
    church_name = serializers.CharField(source='church.name', read_only=True, allow_null=True)
    campus_name = serializers.CharField(source='campus.name', read_only=True, allow_null=True)

    class Meta:
        model = UserRoleAssignment
        fields = [
            'id', 'membership_id', 'username', 'email', 'role_id', 'role_name',
            'role_code', 'scope', 'church_id', 'church_name', 'campus_id',
            'campus_name', 'starts_at', 'expires_at', 'revoked_at', 'created_at',
        ]
        read_only_fields = fields


class ChurchSettingsSerializer(serializers.ModelSerializer):
    church_id = serializers.UUIDField(source='church.church_id', read_only=True)
    church_name = serializers.CharField(source='church.name', read_only=True)
    pastor_name = serializers.CharField(source='church.pastor_name', read_only=True, allow_null=True)
    address = serializers.CharField(source='church.address', required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, max_length=120)
    region = serializers.CharField(required=False, allow_blank=True, max_length=120)
    country = serializers.CharField(source='church.country', required=False, allow_blank=True, allow_null=True)
    phone = serializers.CharField(source='church.phone', required=False, allow_blank=True, allow_null=True)
    email = serializers.EmailField(source='church.email', required=False, allow_blank=True, allow_null=True)
    timezone = serializers.CharField(source='church.fuseau_horaire', required=False, allow_blank=True, allow_null=True)
    currency = serializers.SerializerMethodField()
    logo_primary = serializers.FileField(required=False, allow_null=True, validators=[validate_church_logo])
    logo_secondary = serializers.FileField(required=False, allow_null=True, validators=[validate_church_logo])

    class Meta:
        model = ChurchSettings
        fields = [
            'church_id', 'church_name', 'short_name', 'description', 'denomination',
            'pastor_name', 'address', 'city', 'region', 'country', 'phone', 'email',
            'website', 'logo_primary', 'logo_secondary', 'social_links', 'motto',
            'registration_number', 'language',
            'timezone', 'currency', 'additional_information', 'updated_at',
        ]
        read_only_fields = ['church_id', 'church_name', 'pastor_name', 'currency', 'updated_at']

    def get_currency(self, instance):
        configuration = getattr(instance.church, 'financial_configuration', None)
        return configuration.base_currency if configuration else None

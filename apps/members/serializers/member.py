from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.core.models import Campus, Church, OrganizationMembership
from apps.members.models import Member, MemberHistory
from infrastructure.tenancy import TenantContext


class MemberWriteSerializer(serializers.ModelSerializer):
    church = serializers.UUIDField()
    campus = serializers.UUIDField(required=False, allow_null=True)
    photo_upload = serializers.ImageField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Member
        fields = [
            'first_name', 'last_name', 'email', 'phone', 'gender', 'date_of_birth',
            'address', 'marital_status', 'nationality', 'profession', 'education_level',
            'baptism_status', 'baptism_date', 'emergency_contact_name',
            'emergency_contact_phone', 'status', 'church', 'campus', 'photo_upload',
        ]

    def get_fields(self):
        fields = super().get_fields()
        if not self.context.get('allow_status', False):
            fields.pop('status', None)
        return fields

    def validate_photo_upload(self, value):
        if value and value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError('La photo ne peut pas dépasser 5 Mo.')
        return value

    def update(self, instance, validated_data):
        photo = validated_data.pop('photo_upload', serializers.empty)
        if photo is not serializers.empty:
            validated_data['photo_image'] = photo
        return super().update(instance, validated_data)

    def validate_email(self, value):
        members = Member.objects.filter(
            organization=TenantContext.get(),
            email__iexact=value,
        )
        if self.instance:
            members = members.exclude(pk=self.instance.pk)
        if members.exists():
            raise serializers.ValidationError('Un membre existe déjà avec cet email dans l’organisation courante.')
        return value.lower()

    def validate_church(self, value):
        from infrastructure.tenancy import TenantContext

        active_org = TenantContext.get()
        if active_org is None:
            raise serializers.ValidationError('Aucune organisation active n’a été sélectionnée.')
        try:
            church = Church.objects.get(church_id=value)
        except Church.DoesNotExist as exc:
            raise serializers.ValidationError('L’église sélectionnée est introuvable.') from exc
        if church.organization_id != active_org.tenant_id:
            raise serializers.ValidationError('Cette église n’appartient pas à l’organisation active.')
        return church

    def validate_campus(self, value):
        if value is None:
            return None
        from infrastructure.tenancy import TenantContext

        active_org = TenantContext.get()
        if active_org is None:
            raise serializers.ValidationError('Aucune organisation active n’a été sélectionnée.')
        try:
            campus = Campus.objects.get(campus_id=value)
        except Campus.DoesNotExist as exc:
            raise serializers.ValidationError('Le campus sélectionné est introuvable.') from exc
        if campus.church.organization_id != active_org.tenant_id:
            raise serializers.ValidationError('Ce campus n’appartient pas à l’organisation active.')
        church = self.initial_data.get('church')
        if church and str(campus.church_id) != str(church):
            raise serializers.ValidationError('Le campus doit appartenir à l’église sélectionnée.')
        return campus


class MemberDetailSerializer(serializers.ModelSerializer):
    has_account = serializers.SerializerMethodField()
    photo = serializers.SerializerMethodField()

    class Meta:
        model = Member
        fields = [
            'id', 'first_name', 'last_name', 'email', 'phone', 'gender', 'date_of_birth',
            'address', 'marital_status', 'nationality', 'profession', 'education_level',
            'baptism_status', 'baptism_date', 'emergency_contact_name',
            'emergency_contact_phone', 'photo', 'organization', 'church', 'campus',
            'status', 'member_since', 'created_at', 'updated_at', 'has_account',
        ]
        read_only_fields = fields

    def get_has_account(self, member):
        return member.user_id is not None

    def get_photo(self, member):
        if member.photo_image:
            return member.photo_image.url
        return member.photo


class MemberHistorySerializer(serializers.ModelSerializer):
    event_type_label = serializers.CharField(source='get_event_type_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    class Meta:
        model = MemberHistory
        fields = [
            'id', 'event_type', 'event_type_label', 'title', 'event_date',
            'description', 'created_by_name', 'created_at',
        ]
        read_only_fields = ['id', 'event_type_label', 'created_by_name', 'created_at']

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Le titre est obligatoire.')
        return value

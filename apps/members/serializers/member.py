from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.core.models import Campus, Church, OrganizationMembership
from apps.members.models import Member


class MemberWriteSerializer(serializers.ModelSerializer):
    church = serializers.UUIDField()
    campus = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = Member
        fields = [
            'first_name', 'last_name', 'email', 'phone', 'gender', 'date_of_birth',
            'address', 'marital_status', 'church', 'campus'
        ]

    def validate_email(self, value):
        if Member.objects.filter(email__iexact=value).exists():
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

    class Meta:
        model = Member
        fields = [
            'id', 'first_name', 'last_name', 'email', 'phone', 'gender', 'date_of_birth',
            'address', 'marital_status', 'organization', 'church', 'campus', 'status', 'member_since', 'created_at', 'updated_at',
            'has_account',
        ]
        read_only_fields = fields

    def get_has_account(self, member):
        return member.user_id is not None

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from . import models
from infrastructure.tenancy import TenantContext

User = get_user_model()

class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Organization
        fields = ['tenant_id', 'name', 'slug', 'phone', 'address', 'email', 'country', 'device', 'is_active', 'fuseau_horaire', 'owner', 'date_created', 'update_date']
        read_only_fields = ['tenant_id', 'owner', 'date_created', 'update_date']

class ChurchSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Church
        fields = ['church_id', 'organization', 'name', 'pastor_name', 'slug', 'phone', 'address', 'email', 'country', 'device', 'is_active', 'fuseau_horaire', 'date_created', 'update_date']
        read_only_fields = ['church_id', 'date_created', 'update_date']

    def validate_organization(self, organization):
        if self.context.get('request') and self.context['request'].user.is_superuser:
            return organization
        active_tenant = TenantContext.get()
        if (
            active_tenant is None
            or organization.tenant_id != active_tenant.tenant_id
            or not models.Organization.objects.filter(
                tenant_id=organization.tenant_id,
                memberships__user=self.context['request'].user,
            ).exists()
        ):
            raise serializers.ValidationError('Vous ne pouvez pas utiliser cette organisation.')
        return organization

class CampusSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Campus
        fields = ['campus_id', 'church', 'name', 'slug', 'phone', 'address', 'email', 'country', 'device', 'is_active', 'fuseau_horaire', 'date_created', 'update_date']
        read_only_fields = ['campus_id', 'date_created', 'update_date']

    def validate_church(self, church):
        if self.context.get('request') and self.context['request'].user.is_superuser:
            return church
        active_tenant = TenantContext.get()
        if (
            active_tenant is None
            or church.organization_id != active_tenant.tenant_id
            or not models.Church.objects.filter(
                church_id=church.church_id,
                organization__memberships__user=self.context['request'].user,
            ).exists()
        ):
            raise serializers.ValidationError('Vous ne pouvez pas utiliser cette église.')
        return church


class RegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('Ce nom utilisateur est déjà utilisé.')
        return value

    def validate_password(self, value):
        user = User(
            username=self.initial_data.get('username', ''),
            email=self.initial_data.get('email', ''),
        )
        try:
            django_validate_password(value, user=user)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return value

    def create(self, validated_data):
        password = validated_data.pop('password')
        return User.objects.create_user(password=password, **validated_data)


class OrganizationMemberSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    role = serializers.ChoiceField(
        choices=[
            models.OrganizationMembership.Role.ADMIN,
            models.OrganizationMembership.Role.MEMBER,
        ],
        default=models.OrganizationMembership.Role.MEMBER,
    )

    def validate_username(self, value):
        try:
            user = User.objects.get(username=value)
        except User.DoesNotExist:
            raise serializers.ValidationError('Aucun utilisateur inscrit ne correspond à ce nom.')

        organization = self.context['organization']
        if models.OrganizationMembership.objects.filter(user=user, organization=organization).exists():
            raise serializers.ValidationError('Cet utilisateur appartient déjà à cette organisation.')

        self.user = user
        return value

    def create(self, validated_data):
        return models.OrganizationMembership.objects.create(
            user=self.user,
            organization=self.context['organization'],
            role=validated_data['role'],
        )
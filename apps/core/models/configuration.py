import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ChurchSettings(models.Model):
    church = models.OneToOneField(
        'core.Church',
        primary_key=True,
        on_delete=models.CASCADE,
        related_name='settings',
    )
    short_name = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    denomination = models.CharField(max_length=160, blank=True)
    city = models.CharField(max_length=120, blank=True)
    region = models.CharField(max_length=120, blank=True)
    website = models.URLField(blank=True)
    logo_primary = models.FileField(upload_to='church-logos/%Y/%m/', blank=True, null=True)
    logo_secondary = models.FileField(upload_to='church-logos/%Y/%m/', blank=True, null=True)
    social_links = models.JSONField(default=dict, blank=True)
    motto = models.CharField(max_length=240, blank=True)
    registration_number = models.CharField(max_length=120, blank=True)
    language = models.CharField(max_length=12, default='fr')
    additional_information = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='church_settings_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)


class AppPermission(models.Model):
    code = models.CharField(max_length=100, unique=True)
    module = models.CharField(max_length=40)
    action = models.CharField(max_length=32)
    label = models.CharField(max_length=120)
    description = models.CharField(max_length=240, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['module', 'action', 'code']


class OrganizationRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='custom_roles',
    )
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=80)
    description = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='custom_roles_created',
    )
    permissions = models.ManyToManyField(
        AppPermission,
        through='OrganizationRolePermission',
        related_name='roles',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'code'],
                name='core_role_code_per_organization',
            ),
        ]


class OrganizationRolePermission(models.Model):
    role = models.ForeignKey(
        OrganizationRole,
        on_delete=models.CASCADE,
        related_name='permission_links',
    )
    permission = models.ForeignKey(
        AppPermission,
        on_delete=models.PROTECT,
        related_name='role_links',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['role', 'permission'],
                name='core_role_permission_unique',
            ),
        ]


class UserRoleAssignment(models.Model):
    class Scope(models.TextChoices):
        ORGANIZATION = 'ORGANIZATION', 'Organisation'
        CHURCH = 'CHURCH', 'Église'
        CAMPUS = 'CAMPUS', 'Campus'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='role_assignments',
    )
    membership = models.ForeignKey(
        'core.OrganizationMembership',
        on_delete=models.PROTECT,
        related_name='role_assignments',
    )
    role = models.ForeignKey(
        OrganizationRole,
        on_delete=models.PROTECT,
        related_name='assignments',
    )
    scope = models.CharField(max_length=16, choices=Scope.choices)
    church = models.ForeignKey(
        'core.Church',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='role_assignments',
    )
    campus = models.ForeignKey(
        'core.Campus',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='role_assignments',
    )
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='role_assignments_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'membership', 'revoked_at']),
            models.Index(fields=['scope', 'church', 'campus']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope='ORGANIZATION', church__isnull=True, campus__isnull=True)
                    | Q(scope='CHURCH', church__isnull=False, campus__isnull=True)
                    | Q(scope='CAMPUS', church__isnull=False, campus__isnull=False)
                ),
                name='core_role_assignment_scope_valid',
            ),
        ]

    def clean(self):
        if self.expires_at and self.expires_at <= self.starts_at:
            raise ValidationError({'expires_at': 'La date d’expiration doit suivre la date de début.'})
        if self.membership_id and self.organization_id != self.membership.organization_id:
            raise ValidationError({'membership': 'L’appartenance doit appartenir à cette organisation.'})
        if self.role_id and self.organization_id != self.role.organization_id:
            raise ValidationError({'role': 'Le rôle doit appartenir à cette organisation.'})
        if self.church_id and self.church.organization_id != self.organization_id:
            raise ValidationError({'church': 'L’église doit appartenir à cette organisation.'})
        if self.campus_id and (
            self.church_id is None or self.campus.church_id != self.church_id
        ):
            raise ValidationError({'campus': 'Le campus doit appartenir à l’église sélectionnée.'})


class ConfigurationAuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Les entrées du journal de configuration sont immuables.')

    def delete(self):
        raise ValidationError('Les entrées du journal de configuration ne peuvent pas être supprimées.')


class ConfigurationAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='configuration_audit_logs',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='configuration_audit_logs',
    )
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80, blank=True)
    scope = models.CharField(max_length=24, blank=True)
    result = models.CharField(max_length=16, default='SUCCESS')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'created_at'])]

    objects = ConfigurationAuditLogQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Les entrées du journal de configuration sont immuables.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Les entrées du journal de configuration ne peuvent pas être supprimées.')

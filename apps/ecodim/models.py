import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.core.models import Church, Organization
from apps.families.models import Family
from apps.members.models import Member
from apps.people.models import Person


class EcodimChild(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Actif'
        INACTIVE = 'INACTIVE', 'Inactif'
        ARCHIVED = 'ARCHIVED', 'Archivé'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=20)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    date_of_birth = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=10, blank=True)
    photo_url = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    church = models.ForeignKey(Church, on_delete=models.PROTECT, related_name='ecodim_children')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_children_created',
    )
    family = models.ForeignKey(Family, on_delete=models.SET_NULL, null=True, blank=True, related_name='ecodim_children')
    member = models.ForeignKey(Member, on_delete=models.PROTECT, null=True, blank=True, related_name='ecodim_profiles')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='ecodim_children')
    person = models.ForeignKey(Person, on_delete=models.PROTECT, null=True, blank=True, related_name='ecodim_profiles')

    class Meta:
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['church', 'status', 'last_name', 'first_name']),
            models.Index(fields=['organization', 'created_at']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['church', 'reference'], name='ecodim_child_reference_per_church'),
            models.UniqueConstraint(
                fields=['church', 'member'],
                condition=Q(member__isnull=False),
                name='ecodim_child_member_per_church',
            ),
            models.UniqueConstraint(
                fields=['church', 'person'],
                condition=Q(person__isnull=False),
                name='ecodim_child_person_per_church',
            ),
            models.CheckConstraint(
                condition=Q(member__isnull=True) | Q(person__isnull=True),
                name='ecodim_child_single_identity_link',
            ),
        ]

    def __str__(self):
        return f'{self.first_name} {self.last_name}'.strip()


class EcodimAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField(max_length=60)
    detail = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_audit_logs',
    )
    church = models.ForeignKey(Church, on_delete=models.PROTECT, related_name='ecodim_audit_logs')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='ecodim_audit_logs')
    child = models.ForeignKey(
        EcodimChild,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='audit_logs',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'created_at']),
            models.Index(fields=['church', 'created_at']),
        ]


class EcodimChildGuardian(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30, blank=True)
    relationship = models.CharField(max_length=80, blank=True)
    is_legal_guardian = models.BooleanField(default=True)
    is_primary_contact = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    child = models.ForeignKey(EcodimChild, on_delete=models.CASCADE, related_name='guardians')
    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ecodim_guardianships',
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ecodim_guardianships',
    )

    class Meta:
        ordering = ['-is_primary_contact', 'name']
        constraints = [
            models.CheckConstraint(
                condition=Q(person__isnull=True) | Q(member__isnull=True),
                name='ecodim_guardian_single_identity_link',
            ),
            models.UniqueConstraint(
                fields=['child', 'person'],
                condition=Q(person__isnull=False),
                name='ecodim_guardian_person_per_child',
            ),
            models.UniqueConstraint(
                fields=['child', 'member'],
                condition=Q(member__isnull=False),
                name='ecodim_guardian_member_per_child',
            ),
        ]


class EcodimClass(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        ARCHIVED = 'ARCHIVED', 'Archivée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    level = models.CharField(max_length=80, blank=True)
    minimum_age = models.PositiveSmallIntegerField(null=True, blank=True)
    maximum_age = models.PositiveSmallIntegerField(null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    church = models.ForeignKey(Church, on_delete=models.PROTECT, related_name='ecodim_classes')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_classes_created',
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='ecodim_classes')

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['church', 'name'], name='ecodim_class_name_per_church'),
            models.CheckConstraint(
                condition=Q(maximum_age__isnull=True)
                | Q(minimum_age__isnull=True)
                | Q(maximum_age__gte=F('minimum_age')),
                name='ecodim_class_age_range_valid',
            ),
            models.CheckConstraint(
                condition=Q(capacity__isnull=True) | Q(capacity__gte=1),
                name='ecodim_class_capacity_positive',
            ),
        ]

    def __str__(self):
        return self.name


class EcodimClassTeacher(models.Model):
    class Role(models.TextChoices):
        LEAD = 'LEAD', 'Enseignant principal'
        ASSISTANT = 'ASSISTANT', 'Enseignant adjoint'
        SUBSTITUTE = 'SUBSTITUTE', 'Remplaçant'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.ASSISTANT)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_class_teachers_assigned',
    )
    ecodim_class = models.ForeignKey(EcodimClass, on_delete=models.CASCADE, related_name='teachers')
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='ecodim_class_assignments')

    class Meta:
        ordering = ['member__last_name', 'member__first_name']
        constraints = [
            models.UniqueConstraint(
                fields=['ecodim_class', 'member'],
                condition=Q(is_active=True),
                name='ecodim_active_teacher_per_class',
            ),
        ]


class EcodimEnrollment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_enrollments_assigned',
    )
    child = models.ForeignKey(EcodimChild, on_delete=models.PROTECT, related_name='enrollments')
    ecodim_class = models.ForeignKey(EcodimClass, on_delete=models.PROTECT, related_name='enrollments')

    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['ecodim_class', 'ended_at'])]
        constraints = [
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gt=F('started_at')),
                name='ecodim_enrollment_dates_valid',
            ),
            models.UniqueConstraint(
                fields=['child'],
                condition=Q(ended_at__isnull=True),
                name='ecodim_one_current_class_per_child',
            ),
        ]


class EcodimPickupAuthorization(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        SUSPENDED = 'SUSPENDED', 'Suspendue'
        REVOKED = 'REVOKED', 'Révoquée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30, blank=True)
    relationship = models.CharField(max_length=80, blank=True)
    valid_from = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_pickup_authorizations_created',
    )
    child = models.ForeignKey(EcodimChild, on_delete=models.PROTECT, related_name='pickup_authorizations')
    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ecodim_pickup_authorizations',
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ecodim_pickup_authorizations',
    )

    class Meta:
        ordering = ['name']
        constraints = [
            models.CheckConstraint(
                condition=Q(person__isnull=True) | Q(member__isnull=True),
                name='ecodim_pickup_single_identity_link',
            ),
            models.CheckConstraint(
                condition=Q(valid_until__isnull=True) | Q(valid_until__gte=F('valid_from')),
                name='ecodim_pickup_dates_valid',
            ),
        ]


class EcodimChildHandoff(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    picked_up_by_name = models.CharField(max_length=200)
    handed_off_at = models.DateTimeField(default=timezone.now)
    verification_note = models.CharField(max_length=500, blank=True)
    child = models.ForeignKey(EcodimChild, on_delete=models.PROTECT, related_name='handoffs')
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_handoffs_confirmed',
    )
    authorization = models.ForeignKey(EcodimPickupAuthorization, on_delete=models.PROTECT, related_name='handoffs')

    class Meta:
        ordering = ['-handed_off_at']
        indexes = [models.Index(fields=['child', 'handed_off_at'])]


class EcodimSession(models.Model):
    class Status(models.TextChoices):
        PLANNED = 'PLANNED', 'Planifiée'
        IN_PROGRESS = 'IN_PROGRESS', 'En cours'
        COMPLETED = 'COMPLETED', 'Terminée'
        CANCELLED = 'CANCELLED', 'Annulée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    church = models.ForeignKey(Church, on_delete=models.PROTECT, related_name='ecodim_sessions')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_sessions_created',
    )
    ecodim_class = models.ForeignKey(
        EcodimClass,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sessions',
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='ecodim_sessions')

    class Meta:
        ordering = ['starts_at']
        indexes = [models.Index(fields=['church', 'status', 'starts_at'])]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F('starts_at')), name='ecodim_session_end_after_start'),
        ]


class EcodimStaffAssignment(models.Model):
    class Role(models.TextChoices):
        DIRECTOR = 'DIRECTOR', 'Direction ECODIM'
        CHILD_SAFETY = 'CHILD_SAFETY', 'Sécurité des enfants'
        CHECK_IN = 'CHECK_IN', 'Pointage'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ecodim_staff_assignments_created',
    )
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='ecodim_staff_assignments')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='ecodim_staff_assignments')

    class Meta:
        ordering = ['member__last_name', 'member__first_name']
        constraints = [
            models.UniqueConstraint(fields=['church', 'member', 'role'], name='ecodim_staff_role_per_church'),
        ]

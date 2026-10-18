import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class EventScope(models.TextChoices):
    ORGANIZATION = 'ORGANIZATION', 'Organization'
    CHURCH = 'CHURCH', 'Church'
    CAMPUS = 'CAMPUS', 'Campus'


class EventStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    PUBLISHED = 'PUBLISHED', 'Published'
    IN_PROGRESS = 'IN_PROGRESS', 'In progress'
    COMPLETED = 'COMPLETED', 'Completed'
    CANCELLED = 'CANCELLED', 'Cancelled'
    ARCHIVED = 'ARCHIVED', 'Archived'


class RecurrenceFrequency(models.TextChoices):
    NONE = 'NONE', 'None'
    DAILY = 'DAILY', 'Daily'
    WEEKLY = 'WEEKLY', 'Weekly'
    MONTHLY = 'MONTHLY', 'Monthly'


class RegistrationMode(models.TextChoices):
    DISABLED = 'DISABLED', 'Disabled'
    OPTIONAL = 'OPTIONAL', 'Optional'
    REQUIRED = 'REQUIRED', 'Required'


class CheckInMode(models.TextChoices):
    DISABLED = 'DISABLED', 'Disabled'
    REGISTRATION = 'REGISTRATION', 'Registration required'
    SELF = 'SELF', 'Self check-in'


class RegistrationStatus(models.TextChoices):
    CONFIRMED = 'CONFIRMED', 'Confirmed'
    WAITLISTED = 'WAITLISTED', 'Waitlisted'
    OFFERED = 'OFFERED', 'Place offered'
    CANCELLED = 'CANCELLED', 'Cancelled'
    DECLINED = 'DECLINED', 'Declined'
    EXPIRED = 'EXPIRED', 'Offer expired'


class AttendanceState(models.TextChoices):
    PRESENT = 'PRESENT', 'Present'
    REVOKED = 'REVOKED', 'Revoked'


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='events')
    scope = models.CharField(max_length=20, choices=EventScope.choices)
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, null=True, blank=True, related_name='events')
    campus = models.ForeignKey('core.Campus', on_delete=models.PROTECT, null=True, blank=True, related_name='events')
    included_churches = models.ManyToManyField('core.Church', blank=True, related_name='organization_events')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=80, blank=True)
    location = models.CharField(max_length=255, blank=True)
    timezone = models.CharField(max_length=64, default='UTC')
    status = models.CharField(max_length=20, choices=EventStatus.choices, default=EventStatus.DRAFT)
    recurrence_frequency = models.CharField(
        max_length=10,
        choices=RecurrenceFrequency.choices,
        default=RecurrenceFrequency.NONE,
    )
    recurrence_interval = models.PositiveSmallIntegerField(default=1)
    recurrence_until = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_events',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='updated_events',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope=EventScope.ORGANIZATION, church__isnull=True, campus__isnull=True)
                    | Q(scope=EventScope.CHURCH, church__isnull=False, campus__isnull=True)
                    | Q(scope=EventScope.CAMPUS, church__isnull=True, campus__isnull=False)
                ),
                name='event_scope_matches_location',
            ),
            models.CheckConstraint(condition=Q(recurrence_interval__gte=1), name='event_recurrence_interval_positive'),
        ]
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['scope', 'church']),
        ]

    def __str__(self):
        return self.title


class ChurchEventAdministrator(models.Model):
    church = models.ForeignKey('core.Church', on_delete=models.CASCADE, related_name='event_administrators')
    member = models.ForeignKey(
        'members.Member',
        on_delete=models.CASCADE,
        related_name='church_event_administrator_roles',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='assigned_church_event_administrators',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['member__last_name', 'member__first_name']
        constraints = [
            models.UniqueConstraint(fields=['church', 'member'], name='unique_church_event_administrator'),
        ]

    def __str__(self):
        return f'{self.member} — {self.church}'


class EventOccurrence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='occurrences')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=EventStatus.choices, default=EventStatus.DRAFT)
    location = models.CharField(max_length=255, blank=True)
    registration_mode = models.CharField(
        max_length=12,
        choices=RegistrationMode.choices,
        default=RegistrationMode.DISABLED,
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    waitlist_enabled = models.BooleanField(default=False)
    checkin_mode = models.CharField(max_length=15, choices=CheckInMode.choices, default=CheckInMode.DISABLED)
    checkin_opens_at = models.DateTimeField(null=True, blank=True)
    checkin_closes_at = models.DateTimeField(null=True, blank=True)
    self_checkin_token_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['starts_at']
        constraints = [
            models.UniqueConstraint(fields=['event', 'starts_at'], name='unique_event_occurrence_start'),
            models.CheckConstraint(condition=Q(ends_at__gt=models.F('starts_at')), name='event_occurrence_end_after_start'),
            models.CheckConstraint(condition=Q(capacity__isnull=True) | Q(capacity__gte=1), name='event_capacity_positive'),
            models.CheckConstraint(
                condition=~Q(checkin_mode=CheckInMode.REGISTRATION, registration_mode=RegistrationMode.DISABLED),
                name='event_registration_checkin_requires_registration',
            ),
            models.CheckConstraint(
                condition=Q(checkin_closes_at__isnull=True)
                | Q(checkin_opens_at__isnull=True)
                | Q(checkin_closes_at__gte=models.F('checkin_opens_at')),
                name='event_checkin_window_valid',
            ),
        ]
        indexes = [models.Index(fields=['status', 'starts_at'])]


class EventRegistration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurrence = models.ForeignKey(EventOccurrence, on_delete=models.PROTECT, related_name='registrations')
    member = models.ForeignKey('members.Member', on_delete=models.PROTECT, null=True, blank=True, related_name='event_registrations')
    person = models.ForeignKey('people.Person', on_delete=models.PROTECT, null=True, blank=True, related_name='event_registrations')
    status = models.CharField(max_length=12, choices=RegistrationStatus.choices, default=RegistrationStatus.CONFIRMED)
    qr_token_hash = models.CharField(max_length=64, blank=True)
    verification_code_hash = models.CharField(max_length=64, blank=True)
    manage_token_hash = models.CharField(max_length=64, blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_registrations_created',
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_registrations_cancelled',
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.CheckConstraint(
                condition=(Q(member__isnull=False, person__isnull=True) | Q(member__isnull=True, person__isnull=False)),
                name='event_registration_exactly_one_attendee',
            ),
            models.UniqueConstraint(
                fields=['occurrence', 'member'],
                condition=Q(member__isnull=False),
                name='unique_event_member_registration',
            ),
            models.UniqueConstraint(
                fields=['qr_token_hash'],
                condition=~Q(qr_token_hash=''),
                name='event_registration_qr_token_unique',
            ),
            models.UniqueConstraint(
                fields=['occurrence', 'verification_code_hash'],
                condition=~Q(verification_code_hash=''),
                name='event_registration_verification_code_unique',
            ),
            models.UniqueConstraint(
                fields=['manage_token_hash'],
                condition=~Q(manage_token_hash=''),
                name='event_registration_manage_token_unique',
            ),
        ]
        indexes = [models.Index(fields=['occurrence', 'status', 'created_at'])]


class EventAttendance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurrence = models.ForeignKey(EventOccurrence, on_delete=models.PROTECT, related_name='attendances')
    registration = models.OneToOneField(
        EventRegistration,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='attendance',
    )
    member = models.ForeignKey('members.Member', on_delete=models.PROTECT, null=True, blank=True, related_name='event_attendances')
    state = models.CharField(max_length=10, choices=AttendanceState.choices, default=AttendanceState.PRESENT)
    checked_in_at = models.DateTimeField(default=timezone.now)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_attendances_recorded',
    )
    source = models.CharField(max_length=20, default='REGISTRATION')

    class Meta:
        ordering = ['-checked_in_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(registration__isnull=False, member__isnull=True)
                    | Q(registration__isnull=True, member__isnull=False)
                ),
                name='event_attendance_exactly_one_attendee',
            ),
            models.UniqueConstraint(
                fields=['occurrence', 'member'],
                condition=Q(member__isnull=False),
                name='unique_event_member_attendance',
            ),
        ]


class EventAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    occurrence = models.ForeignKey(
        EventOccurrence,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_audit_logs',
    )
    action = models.CharField(max_length=60)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['event', 'created_at'])]


class EventAttendanceCorrection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attendance = models.ForeignKey(EventAttendance, on_delete=models.PROTECT, related_name='corrections')
    previous_state = models.CharField(max_length=10, choices=AttendanceState.choices)
    corrected_state = models.CharField(max_length=10, choices=AttendanceState.choices)
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='event_attendance_corrections')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

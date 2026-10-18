import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SessionMethod(models.TextChoices):
    MANUAL = 'MANUAL', 'Manual'
    LIST = 'LIST', 'List'
    QR = 'QR', 'QR Code'
    SELF = 'SELF', 'Self check-in'


class AttendanceStatus(models.TextChoices):
    PRESENT = 'PRESENT', 'Present'
    ABSENT = 'ABSENT', 'Absent'
    LATE = 'LATE', 'Late'
    EXCUSED = 'EXCUSED', 'Excused absence'
    EARLY_DEPARTURE = 'EARLY_DEPARTURE', 'Early departure'


class SessionLifecycle(models.TextChoices):
    PLANNED = 'PLANNED', 'Planned'
    IN_PROGRESS = 'IN_PROGRESS', 'In progress'
    CLOSED = 'CLOSED', 'Closed'
    CANCELLED = 'CANCELLED', 'Cancelled'


class PopulationRule(models.TextChoices):
    ALL_MEMBERS = 'ALL_MEMBERS', 'All active members'
    MINISTRY = 'MINISTRY', 'Ministry'
    TRIBE = 'TRIBE', 'Tribe'
    MANUAL = 'MANUAL', 'Manually selected'


class RecurrenceFrequency(models.TextChoices):
    NONE = 'NONE', 'None'
    WEEKLY = 'WEEKLY', 'Weekly'
    MONTHLY = 'MONTHLY', 'Monthly'


class ExcuseStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'


class AttendanceAlertStatus(models.TextChoices):
    NEW = 'NEW', 'New'
    SEEN = 'SEEN', 'Seen'
    IN_PROGRESS = 'IN_PROGRESS', 'In progress'
    RESOLVED = 'RESOLVED', 'Resolved'


class AttendanceSessionType(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='attendance_session_types')
    name = models.CharField(max_length=150)
    population_rule = models.CharField(max_length=20, choices=PopulationRule.choices, default=PopulationRule.ALL_MEMBERS)
    default_methods = models.JSONField(default=list)
    tolerance_minutes = models.PositiveSmallIntegerField(default=15)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'name'], name='attendance_type_name_per_org_unique'),
        ]

    def __str__(self):
        return self.name


class AttendanceVisitor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='attendance_visitors')
    person = models.ForeignKey('people.Person', on_delete=models.PROTECT, related_name='attendance_visitor_profiles')
    reusable = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='attendance_visitors_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'person'], name='attendance_visitor_per_org_unique'),
        ]


class AttendanceSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='attendance_sessions')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, null=True, blank=True, related_name='attendance_sessions')
    campus = models.ForeignKey(
        'core.Campus',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='attendance_sessions',
    )
    session_type = models.ForeignKey(
        AttendanceSessionType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sessions',
    )
    ministry = models.ForeignKey(
        'ministries.Ministry',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='attendance_sessions',
    )
    tribe = models.ForeignKey(
        'ministries.Tribe',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='attendance_sessions',
    )
    event_occurrence = models.OneToOneField(
        'events.EventOccurrence',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='attendance_session',
    )
    title = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=SessionLifecycle.choices, default=SessionLifecycle.PLANNED)
    allowed_methods = models.JSONField(default=list)
    tolerance_minutes = models.PositiveSmallIntegerField(default=15)
    recurrence_frequency = models.CharField(
        max_length=10,
        choices=RecurrenceFrequency.choices,
        default=RecurrenceFrequency.NONE,
    )
    recurrence_series_id = models.UUIDField(null=True, blank=True, db_index=True)
    recurrence_until = models.DateField(null=True, blank=True)
    qr_token_hash = models.CharField(max_length=64, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='attendance_sessions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-starts_at', 'title']
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=models.F('starts_at')), name='attendance_session_end_after_start'),
            models.CheckConstraint(
                condition=Q(recurrence_frequency=RecurrenceFrequency.NONE, recurrence_until__isnull=True)
                | Q(recurrence_frequency__in=[RecurrenceFrequency.WEEKLY, RecurrenceFrequency.MONTHLY]),
                name='attendance_recurrence_configuration_valid',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'starts_at']),
            models.Index(fields=['church', 'status', 'starts_at']),
        ]


class ExpectedParticipant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name='expected_participants')
    member = models.ForeignKey('members.Member', on_delete=models.PROTECT, null=True, blank=True, related_name='expected_attendance')
    visitor = models.ForeignKey(AttendanceVisitor, on_delete=models.PROTECT, null=True, blank=True, related_name='expected_sessions')
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    expected_for_absence = models.BooleanField(default=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='attendance_participants_added',
    )
    addition_reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['last_name', 'first_name']
        constraints = [
            models.CheckConstraint(
                condition=(Q(member__isnull=False, visitor__isnull=True) | Q(member__isnull=True, visitor__isnull=False)),
                name='attendance_expected_exactly_one_person',
            ),
            models.UniqueConstraint(fields=['session', 'member'], condition=Q(member__isnull=False), name='attendance_expected_member_unique'),
            models.UniqueConstraint(fields=['session', 'visitor'], condition=Q(visitor__isnull=False), name='attendance_expected_visitor_unique'),
        ]


class AttendanceRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expected_participant = models.OneToOneField(ExpectedParticipant, on_delete=models.PROTECT, related_name='attendance')
    status = models.CharField(max_length=20, choices=AttendanceStatus.choices)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    method = models.CharField(max_length=10, choices=SessionMethod.choices, null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attendance_records_created',
    )
    departed_at = models.DateTimeField(null=True, blank=True)
    departure_reason = models.CharField(max_length=500, blank=True)
    event_attendance = models.OneToOneField(
        'events.EventAttendance',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='canonical_attendance_record',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['expected_participant__last_name', 'expected_participant__first_name']
        indexes = [models.Index(fields=['status', 'updated_at'])]


class AttendanceCorrection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attendance = models.ForeignKey(AttendanceRecord, on_delete=models.PROTECT, related_name='corrections')
    previous_status = models.CharField(max_length=20, choices=AttendanceStatus.choices)
    new_status = models.CharField(max_length=20, choices=AttendanceStatus.choices)
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='attendance_corrections')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class AbsenceExcuse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attendance = models.ForeignKey(AttendanceRecord, on_delete=models.PROTECT, related_name='excuses')
    status = models.CharField(max_length=10, choices=ExcuseStatus.choices, default=ExcuseStatus.PENDING)
    reason = models.CharField(max_length=1000)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='attendance_excuses_submitted',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attendance_excuses_decided',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']


class AttendanceAlert(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='attendance_alerts')
    member = models.ForeignKey('members.Member', on_delete=models.PROTECT, related_name='attendance_alerts')
    rule_key = models.CharField(max_length=40)
    status = models.CharField(max_length=12, choices=AttendanceAlertStatus.choices, default=AttendanceAlertStatus.NEW)
    trigger_detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'status', 'created_at'])]
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'member', 'rule_key'],
                condition=~Q(status=AttendanceAlertStatus.RESOLVED),
                name='active_attendance_alert_unique',
            ),
        ]


class AttendancePolicy(models.Model):
    class EvaluationFrequency(models.TextChoices):
        DAILY = 'DAILY', 'Daily'
        WEEKLY = 'WEEKLY', 'Weekly'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='attendance_policies')
    church = models.ForeignKey('core.Church', on_delete=models.CASCADE, related_name='attendance_policies')
    consecutive_absences = models.PositiveSmallIntegerField(default=3)
    minimum_attendance_rate = models.DecimalField(max_digits=5, decimal_places=2, default=60)
    observation_weeks = models.PositiveSmallIntegerField(default=8)
    evaluation_frequency = models.CharField(max_length=10, choices=EvaluationFrequency.choices, default=EvaluationFrequency.DAILY)
    count_excused_absences = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='attendance_policies_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)
    last_evaluated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'church'], name='attendance_policy_per_church_unique'),
            models.CheckConstraint(condition=Q(consecutive_absences__gte=1), name='attendance_policy_absences_positive'),
            models.CheckConstraint(condition=Q(minimum_attendance_rate__lte=100), name='attendance_policy_rate_max_100'),
            models.CheckConstraint(condition=Q(observation_weeks__gte=1), name='attendance_policy_observation_positive'),
        ]


class AttendanceAlertNote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert = models.ForeignKey(AttendanceAlert, on_delete=models.PROTECT, related_name='notes')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='attendance_alert_notes')
    action = models.CharField(max_length=30)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AttendanceAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='attendance_audit_logs')
    session = models.ForeignKey(AttendanceSession, on_delete=models.PROTECT, null=True, blank=True, related_name='audit_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='attendance_audit_logs')
    action = models.CharField(max_length=60)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'created_at'])]

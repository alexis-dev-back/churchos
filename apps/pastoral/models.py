import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class ImmutablePastoralQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Les éléments historiques pastoraux sont immuables.')

    def delete(self):
        raise ValidationError('Les éléments historiques pastoraux ne peuvent pas être supprimés.')


class ImmutablePastoralRecord(models.Model):
    objects = ImmutablePastoralQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Les éléments historiques pastoraux sont immuables.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Les éléments historiques pastoraux ne peuvent pas être supprimés.')


DEFAULT_STATUSES = [
    {'code': 'NEW', 'label': 'Nouveau', 'initial': True, 'terminal': False},
    {'code': 'TO_ASSIGN', 'label': 'À attribuer', 'initial': False, 'terminal': False},
    {'code': 'IN_PROGRESS', 'label': 'En cours', 'initial': False, 'terminal': False},
    {'code': 'WAITING', 'label': 'En attente', 'initial': False, 'terminal': False},
    {'code': 'RESOLVED', 'label': 'Résolu', 'initial': False, 'terminal': False},
    {'code': 'CLOSED', 'label': 'Clôturé', 'initial': False, 'terminal': True},
]

DEFAULT_TRANSITIONS = [
    'NEW>TO_ASSIGN', 'NEW>IN_PROGRESS', 'TO_ASSIGN>IN_PROGRESS',
    'IN_PROGRESS>WAITING', 'WAITING>IN_PROGRESS', 'IN_PROGRESS>RESOLVED',
    'WAITING>RESOLVED', 'RESOLVED>CLOSED', 'IN_PROGRESS>CLOSED',
    'WAITING>CLOSED', 'CLOSED>IN_PROGRESS',
]

DEFAULT_INTERACTION_KINDS = [
    {'code': 'VISIT', 'label': 'Visite'},
    {'code': 'CALL', 'label': 'Appel'},
    {'code': 'MEETING', 'label': 'Rendez-vous'},
    {'code': 'INTERVIEW', 'label': 'Entretien'},
    {'code': 'OTHER', 'label': 'Autre'},
]


def default_statuses():
    return [dict(item) for item in DEFAULT_STATUSES]


def default_transitions():
    return list(DEFAULT_TRANSITIONS)


def default_interaction_kinds():
    return [dict(item) for item in DEFAULT_INTERACTION_KINDS]


class PastoralWorkflow(models.Model):
    organization = models.OneToOneField(
        'core.Organization',
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='pastoral_workflow',
    )
    statuses = models.JSONField(default=default_statuses)
    transitions = models.JSONField(default=default_transitions)
    interaction_kinds = models.JSONField(default=default_interaction_kinds)
    block_close_with_open_items = models.BooleanField(default=True)
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    archive_after_days = models.PositiveIntegerField(null=True, blank=True)
    unassigned_alert_after_days = models.PositiveIntegerField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pastoral_workflows_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if (
            not isinstance(self.statuses, list)
            or not isinstance(self.transitions, list)
            or not isinstance(self.interaction_kinds, list)
        ):
            raise ValidationError('La configuration des statuts, interactions et transitions est invalide.')
        codes = [item.get('code') for item in self.statuses if isinstance(item, dict)]
        required = {'NEW', 'TO_ASSIGN', 'IN_PROGRESS', 'WAITING', 'RESOLVED', 'CLOSED'}
        if len(codes) != len(self.statuses) or len(set(codes)) != len(codes) or not required.issubset(codes):
            raise ValidationError({'statuses': 'Les six statuts requis doivent être présents et uniques.'})
        if not all(isinstance(item, str) and '>' in item for item in self.transitions):
            raise ValidationError({'transitions': 'Chaque transition doit être au format SOURCE>CIBLE.'})
        kind_codes = [
            item.get('code') for item in self.interaction_kinds
            if isinstance(item, dict) and isinstance(item.get('code'), str)
        ]
        if (
            not kind_codes
            or len(kind_codes) != len(self.interaction_kinds)
            or len(set(kind_codes)) != len(kind_codes)
            or any(
                not isinstance(item.get('label'), str) or not item['label'].strip()
                for item in self.interaction_kinds if isinstance(item, dict)
            )
        ):
            raise ValidationError({'interaction_kinds': 'Les types d’interaction doivent avoir des codes et libellés uniques.'})


class PastoralCase(models.Model):
    class Sensitivity(models.TextChoices):
        STANDARD = 'STANDARD', 'Standard'
        RESTRICTED = 'RESTRICTED', 'Restreint'
        VERY_RESTRICTED = 'VERY_RESTRICTED', 'Très restreint'

    class Priority(models.TextChoices):
        LOW = 'LOW', 'Faible'
        NORMAL = 'NORMAL', 'Normale'
        HIGH = 'HIGH', 'Élevée'
        URGENT = 'URGENT', 'Urgente'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='pastoral_cases')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, related_name='pastoral_cases')
    campus = models.ForeignKey(
        'core.Campus',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='pastoral_cases',
    )
    member = models.ForeignKey(
        'members.Member',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='pastoral_cases',
    )
    person = models.ForeignKey(
        'people.Person',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='pastoral_cases',
    )
    title = models.CharField(max_length=180)
    category = models.CharField(max_length=80, blank=True)
    source = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=40, default='NEW')
    sensitivity = models.CharField(max_length=20, choices=Sensitivity.choices, default=Sensitivity.STANDARD)
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.NORMAL)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='pastoral_cases_assigned',
    )
    next_due_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='pastoral_cases_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at', '-created_at']
        constraints = [
            models.CheckConstraint(
                condition=(Q(member__isnull=False, person__isnull=True) | Q(member__isnull=True, person__isnull=False)),
                name='pastoral_case_exactly_one_subject',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'church', 'status']),
            models.Index(fields=['organization', 'assigned_to', 'status']),
            models.Index(fields=['organization', 'campus', 'status']),
        ]


class PastoralCaseAccess(models.Model):
    class AccessLevel(models.TextChoices):
        READ = 'READ', 'Lecture'
        EDIT = 'EDIT', 'Lecture et modification'

    class Source(models.TextChoices):
        SHARE = 'SHARE', 'Partage explicite'
        ASSIGNMENT = 'ASSIGNMENT', 'Attribution'
        CREATOR = 'CREATOR', 'Créateur'

    case = models.ForeignKey(PastoralCase, on_delete=models.CASCADE, related_name='access_grants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pastoral_case_grants')
    access_level = models.CharField(max_length=8, choices=AccessLevel.choices, default=AccessLevel.READ)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.SHARE)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pastoral_access_granted')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['case', 'user'], name='pastoral_case_access_unique'),
        ]


class PastoralAssignmentHistory(ImmutablePastoralRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(PastoralCase, on_delete=models.PROTECT, related_name='assignment_history')
    previous_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    new_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PastoralInteractionHistory(ImmutablePastoralRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    interaction = models.ForeignKey(
        'PastoralInteraction', on_delete=models.PROTECT, related_name='history',
    )
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    previous_scheduled_at = models.DateTimeField(null=True, blank=True)
    new_scheduled_at = models.DateTimeField(null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PastoralStatusHistory(ImmutablePastoralRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(PastoralCase, on_delete=models.PROTECT, related_name='status_history')
    previous_status = models.CharField(max_length=40)
    new_status = models.CharField(max_length=40)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class PastoralInteraction(models.Model):
    class Kind(models.TextChoices):
        VISIT = 'VISIT', 'Visite'
        CALL = 'CALL', 'Appel'
        MEETING = 'MEETING', 'Rendez-vous'
        INTERVIEW = 'INTERVIEW', 'Entretien'
        OTHER = 'OTHER', 'Autre'

    class Status(models.TextChoices):
        PLANNED = 'PLANNED', 'Planifié'
        CONFIRMED = 'CONFIRMED', 'Confirmé'
        RESCHEDULED = 'RESCHEDULED', 'Reporté'
        COMPLETED = 'COMPLETED', 'Réalisé'
        MISSED = 'MISSED', 'Absent'
        CANCELLED = 'CANCELLED', 'Annulé'

    class Sensitivity(models.TextChoices):
        CASE_TEAM = 'CASE_TEAM', 'Équipe du dossier'
        RESTRICTED = 'RESTRICTED', 'Restreinte'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(PastoralCase, on_delete=models.CASCADE, related_name='interactions')
    kind = models.CharField(max_length=40)
    objective = models.CharField(max_length=500, blank=True)
    scheduled_at = models.DateTimeField()
    location = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    sensitivity = models.CharField(max_length=12, choices=Sensitivity.choices, default=Sensitivity.CASE_TEAM)
    outcome = models.TextField(blank=True)
    next_due_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-scheduled_at']
        indexes = [models.Index(fields=['case', 'scheduled_at', 'status'])]


class PastoralInteractionShare(models.Model):
    interaction = models.ForeignKey(PastoralInteraction, on_delete=models.CASCADE, related_name='shares')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pastoral_interaction_shares')
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['interaction', 'user'], name='pastoral_interaction_share_unique')]


class PastoralNote(models.Model):
    class Sensitivity(models.TextChoices):
        CASE_TEAM = 'CASE_TEAM', 'Équipe du dossier'
        RESTRICTED = 'RESTRICTED', 'Restreinte'
        AUTHOR_ONLY = 'AUTHOR_ONLY', 'Rédacteur uniquement'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(PastoralCase, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pastoral_notes')
    sensitivity = models.CharField(max_length=16, choices=Sensitivity.choices, default=Sensitivity.AUTHOR_ONLY)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['case', 'sensitivity', 'created_at'])]


class PastoralNoteShare(models.Model):
    note = models.ForeignKey(PastoralNote, on_delete=models.CASCADE, related_name='shares')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pastoral_note_shares')
    can_edit = models.BooleanField(default=False)
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['note', 'user'], name='pastoral_note_share_unique')]


class PastoralNoteRevision(ImmutablePastoralRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    note = models.ForeignKey(PastoralNote, on_delete=models.PROTECT, related_name='revisions')
    content = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class PrayerRequest(models.Model):
    class Status(models.TextChoices):
        NEW = 'NEW', 'Nouvelle'
        IN_PROGRESS = 'IN_PROGRESS', 'En cours'
        ANSWERED = 'ANSWERED', 'Traitée'
        CLOSED = 'CLOSED', 'Clôturée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='prayer_requests')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, related_name='prayer_requests')
    campus = models.ForeignKey(
        'core.Campus', on_delete=models.PROTECT, null=True, blank=True, related_name='prayer_requests',
    )
    person = models.ForeignKey(
        'people.Person', on_delete=models.PROTECT, null=True, blank=True, related_name='prayer_requests',
    )
    member = models.ForeignKey(
        'members.Member', on_delete=models.PROTECT, null=True, blank=True, related_name='prayer_requests',
    )
    is_anonymous = models.BooleanField(default=False)
    category = models.CharField(max_length=80, blank=True)
    content = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    sensitivity = models.CharField(
        max_length=20, choices=PastoralCase.Sensitivity.choices, default=PastoralCase.Sensitivity.RESTRICTED,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='prayer_requests_assigned',
    )
    linked_case = models.ForeignKey(
        PastoralCase, on_delete=models.SET_NULL, null=True, blank=True, related_name='prayer_requests',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='prayer_requests_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(is_anonymous=True, member__isnull=True, person__isnull=True)
                    | Q(is_anonymous=False, member__isnull=False, person__isnull=True)
                    | Q(is_anonymous=False, member__isnull=True, person__isnull=False)
                ),
                name='pastoral_prayer_identity_valid',
            ),
        ]
        indexes = [models.Index(fields=['organization', 'church', 'status'])]


class PrayerRequestShare(models.Model):
    request = models.ForeignKey(PrayerRequest, on_delete=models.CASCADE, related_name='shares')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='prayer_request_shares')
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['request', 'user'], name='pastoral_prayer_share_unique')]


class PrayerRequestHistory(ImmutablePastoralRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(PrayerRequest, on_delete=models.PROTECT, related_name='history')
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    previous_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    new_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PastoralTask(models.Model):
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'À faire'
        IN_PROGRESS = 'IN_PROGRESS', 'En cours'
        DONE = 'DONE', 'Terminée'
        CANCELLED = 'CANCELLED', 'Annulée'

    class Priority(models.TextChoices):
        LOW = 'LOW', 'Faible'
        NORMAL = 'NORMAL', 'Normale'
        HIGH = 'HIGH', 'Élevée'
        URGENT = 'URGENT', 'Urgente'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(PastoralCase, on_delete=models.CASCADE, related_name='tasks')
    interaction = models.ForeignKey(
        PastoralInteraction, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks',
    )
    title = models.CharField(max_length=180)
    description = models.CharField(max_length=500, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pastoral_tasks_assigned',
    )
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.NORMAL)
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    recurrence = models.CharField(max_length=20, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_at', '-created_at']
        indexes = [models.Index(fields=['assigned_to', 'status', 'due_at'])]


class PastoralTaskHistory(ImmutablePastoralRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(PastoralTask, on_delete=models.PROTECT, related_name='history')
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    previous_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    new_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PastoralAuditEvent(ImmutablePastoralRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='pastoral_audit_events')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, null=True, blank=True, related_name='pastoral_audit_events')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='pastoral_audit_events',
    )
    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=48)
    object_id = models.CharField(max_length=64, blank=True)
    result = models.CharField(max_length=12, default='SUCCESS')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'church', 'created_at']),
            models.Index(fields=['object_type', 'object_id']),
        ]

class PastoralNotification(ImmutablePastoralRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='pastoral_notifications')
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pastoral_notifications',
    )
    task = models.ForeignKey(PastoralTask, on_delete=models.PROTECT, related_name='notifications')
    notification_key = models.CharField(max_length=180, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class PastoralNotificationReceipt(models.Model):
    notification = models.ForeignKey(PastoralNotification, on_delete=models.CASCADE, related_name='receipts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pastoral_notification_receipts')
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['notification', 'user'], name='pastoral_notification_receipt_unique')]

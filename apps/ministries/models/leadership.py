import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class LeadershipRole(models.TextChoices):
    LEADER = 'LEADER', 'Leader'
    ADJOINT = 'ADJOINT', 'Adjoint'


class MinistryLeadership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ministry = models.ForeignKey('ministries.Ministry', on_delete=models.PROTECT, related_name='leadership')
    member = models.ForeignKey('members.Member', on_delete=models.PROTECT, related_name='ministry_leadership')
    role = models.CharField(max_length=20, choices=LeadershipRole.choices)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(blank=True, null=True)
    ended_reason = models.CharField(max_length=255, blank=True)
    appointed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='appointed_ministry_leaders',
        blank=True,
        null=True,
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='ended_ministry_leadership',
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(
                fields=['ministry'],
                condition=Q(role=LeadershipRole.LEADER, ended_at__isnull=True),
                name='ministry_one_active_leader',
            ),
            models.UniqueConstraint(
                fields=['ministry', 'member', 'role'],
                condition=Q(ended_at__isnull=True),
                name='ministry_member_active_role_unique',
            ),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gte=models.F('started_at')),
                name='ministry_leadership_dates_valid',
            ),
        ]


class TribeLeadership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tribe = models.ForeignKey('ministries.Tribe', on_delete=models.PROTECT, related_name='leadership')
    member = models.ForeignKey('members.Member', on_delete=models.PROTECT, related_name='tribe_leadership')
    role = models.CharField(max_length=20, choices=LeadershipRole.choices)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(blank=True, null=True)
    ended_reason = models.CharField(max_length=255, blank=True)
    appointed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='appointed_tribe_leaders',
        blank=True,
        null=True,
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='ended_tribe_leadership',
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tribe'],
                condition=Q(role=LeadershipRole.LEADER, ended_at__isnull=True),
                name='tribe_one_active_leader',
            ),
            models.UniqueConstraint(
                fields=['tribe', 'member', 'role'],
                condition=Q(ended_at__isnull=True),
                name='tribe_member_active_role_unique',
            ),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gte=models.F('started_at')),
                name='tribe_leadership_dates_valid',
            ),
        ]

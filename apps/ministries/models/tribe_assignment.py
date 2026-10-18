import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TribeAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey('members.Member', on_delete=models.PROTECT, related_name='tribe_assignments')
    tribe = models.ForeignKey('ministries.Tribe', on_delete=models.PROTECT, related_name='assignments')
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(blank=True, null=True)
    ended_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='created_tribe_assignments',
        blank=True,
        null=True,
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='ended_tribe_assignments',
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-joined_at']
        constraints = [
            models.UniqueConstraint(
                fields=['member'],
                condition=Q(left_at__isnull=True),
                name='tribe_one_active_assignment_per_member',
            ),
            models.CheckConstraint(
                condition=Q(left_at__isnull=True) | Q(left_at__gte=models.F('joined_at')),
                name='tribe_assignment_dates_valid',
            ),
        ]

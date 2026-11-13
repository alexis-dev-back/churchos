import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Announcement(models.Model):
    class Scope(models.TextChoices):
        ORGANIZATION = 'ORGANIZATION', 'Organisation'
        CHURCH = 'CHURCH', 'Église'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Brouillon'
        PUBLISHED = 'PUBLISHED', 'Publié'
        ARCHIVED = 'ARCHIVED', 'Archivé'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='announcements',
    )
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.ORGANIZATION)
    church = models.ForeignKey(
        'core.Church',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='announcements',
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    category = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    publish_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='announcements_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-publish_at', '-created_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope='ORGANIZATION', church__isnull=True)
                    | Q(scope='CHURCH', church__isnull=False)
                ),
                name='communication_scope_matches_church',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'status', 'publish_at']),
            models.Index(fields=['church', 'status', 'publish_at']),
        ]

    def __str__(self):
        return self.title

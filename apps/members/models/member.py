import uuid

from django.conf import settings
from django.db import models

from apps.core.models import Campus, Church, Organization


class Member(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        ACTIVE = 'ACTIVE', 'Active'
        SUSPENDED = 'SUSPENDED', 'Suspended'
        ARCHIVED = 'ARCHIVED', 'Archived'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='member_profile')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='members')
    church = models.ForeignKey(Church, on_delete=models.PROTECT, related_name='members')
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name='members', blank=True, null=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, null=True)
    gender = models.CharField(max_length=10, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    photo = models.CharField(max_length=255, blank=True, null=True)
    marital_status = models.CharField(max_length=30, blank=True, null=True)
    member_since = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'email'], name='unique_member_email_per_organization'),
        ]

    def __str__(self):
        return f'{self.first_name} {self.last_name}'

import uuid

from django.db import models
from django.db.models.functions import Lower

from apps.core.models import Church


class StructureStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    INACTIVE = 'INACTIVE', 'Inactive'
    ARCHIVED = 'ARCHIVED', 'Archived'


class ChurchStructure(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    church = models.ForeignKey(Church, on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    sector = models.CharField(max_length=100, blank=True, default='')
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=StructureStatus.choices, default=StructureStatus.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                Lower('name'),
                'church',
                name='%(app_label)s_%(class)s_church_name_ci_unique',
            ),
        ]

    def __str__(self):
        return self.name

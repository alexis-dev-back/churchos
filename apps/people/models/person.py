import uuid

from django.conf import settings
from django.db import models


class Person(models.Model):
    class PersonType(models.TextChoices):
        INVITED = 'INVITED', 'Invité(e)'
        NON_INVITED = 'NON_INVITED', 'Non invité(e)'

    class BaptismStatus(models.TextChoices):
        UNKNOWN = 'UNKNOWN', 'Non renseignée'
        NOT_BAPTIZED = 'NOT_BAPTIZED', 'Non baptisé(e)'
        IN_PREPARATION = 'IN_PREPARATION', 'En préparation'
        BAPTIZED = 'BAPTIZED', 'Baptisé(e)'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='people',
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='person_profile',
    )
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True, null=True)
    gender = models.CharField(max_length=10, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    photo = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    nationality = models.CharField(max_length=100, blank=True, null=True)
    profession = models.CharField(max_length=150, blank=True, null=True)
    education_level = models.CharField(max_length=100, blank=True, null=True)
    marital_status = models.CharField(max_length=30, blank=True, null=True)
    baptism_status = models.CharField(
        max_length=20,
        choices=BaptismStatus.choices,
        default=BaptismStatus.UNKNOWN,
    )
    person_type = models.CharField(
        max_length=20,
        choices=PersonType.choices,
        default=PersonType.NON_INVITED,
    )
    invited_by_member = models.ForeignKey(
        'members.Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invited_people',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f'{self.first_name} {self.last_name}'.strip()

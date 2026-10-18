import uuid

from django.db import models
from django.db.models import Q

from apps.families.models.family import Family
from apps.members.models import Member
from apps.people.models.person import Person


class FamilyMember(models.Model):
    class RelationshipType(models.TextChoices):
        HEAD = 'HEAD', 'Head'
        SPOUSE = 'SPOUSE', 'Spouse'
        CHILD = 'CHILD', 'Child'
        PARENT = 'PARENT', 'Parent'
        SIBLING = 'SIBLING', 'Sibling'
        OTHER = 'OTHER', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name='memberships')
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='family_memberships', blank=True, null=True)
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='family_memberships', blank=True, null=True)
    relationship_type = models.CharField(max_length=20, choices=RelationshipType.choices, default=RelationshipType.OTHER)
    is_primary_contact = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['family', 'person'], condition=Q(person__isnull=False), name='unique_family_person_member'),
            models.UniqueConstraint(fields=['family', 'member'], condition=Q(member__isnull=False), name='unique_family_church_member'),
            models.CheckConstraint(condition=Q(person__isnull=False) | Q(member__isnull=False), name='family_member_has_target'),
        ]

    def __str__(self):
        return f'{self.member or self.person} — {self.family}'

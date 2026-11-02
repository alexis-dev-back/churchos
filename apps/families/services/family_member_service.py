from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.families.models import Family, FamilyMember
from apps.members.models import Member
from apps.people.models import Person


class FamilyMemberService:
    @staticmethod
    def add_person_to_family(family, person, relationship_type='OTHER', is_primary_contact=False):
        if not isinstance(family, Family):
            raise ValueError('Famille invalide.')
        if not isinstance(person, Person):
            raise ValueError('Personne invalide.')
        if person.organization_id != family.organization_id:
            raise ValidationError('La personne doit appartenir à la même organisation que la famille.')

        if FamilyMember.objects.filter(family=family, person=person).exists():
            raise ValidationError('Cette personne est déjà associée à cette famille.')

        with transaction.atomic():
            membership = FamilyMember.objects.create(
                family=family,
                person=person,
                relationship_type=relationship_type,
                is_primary_contact=is_primary_contact,
            )
        return membership

    @staticmethod
    def add_member_to_family(family, member, relationship_type='OTHER', is_primary_contact=False):
        if not isinstance(family, Family):
            raise ValueError('Famille invalide.')
        if not isinstance(member, Member):
            raise ValueError('Membre invalide.')
        if member.organization_id != family.organization_id:
            raise ValueError('Le membre et la famille doivent appartenir à la même organisation.')
        if FamilyMember.objects.filter(family=family, member=member).exists():
            raise ValidationError('Ce membre est déjà associé à cette famille.')
        with transaction.atomic():
            return FamilyMember.objects.create(
                family=family,
                member=member,
                relationship_type=relationship_type,
                is_primary_contact=is_primary_contact,
            )

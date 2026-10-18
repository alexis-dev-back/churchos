from rest_framework.exceptions import ValidationError

from apps.families.models.family_member import FamilyMember


def validate_identity_link(*, organization, church, family, member=None, person=None):
    if member and (member.organization_id != organization.tenant_id or member.church_id != church.church_id):
        raise ValidationError({'member': 'Ce membre ne fait pas partie de cette église.'})
    if family and family.organization_id != organization.tenant_id:
        raise ValidationError({'family': 'Cette famille ne fait pas partie de cette organisation.'})
    if person and not family:
        raise ValidationError({'person': 'La famille est requise pour rattacher une personne existante.'})
    if member and family and not FamilyMember.objects.filter(
        family=family,
        member=member,
        left_at__isnull=True,
    ).exists():
        raise ValidationError({'family': 'Le membre sélectionné ne fait pas partie de cette famille.'})
    if person and family and not FamilyMember.objects.filter(
        family=family,
        person=person,
        left_at__isnull=True,
    ).exists():
        raise ValidationError({'person': 'La personne sélectionnée ne fait pas partie de cette famille.'})


def validate_person_family(*, family, person):
    if not family or not FamilyMember.objects.filter(
        family=family,
        person=person,
        left_at__isnull=True,
    ).exists():
        raise ValidationError({'person': 'Cette personne ne fait pas partie de la famille de l’enfant.'})

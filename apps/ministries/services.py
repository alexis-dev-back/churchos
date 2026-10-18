from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.members.models import Member
from apps.ministries.models import (
    LeadershipRole,
    Ministry,
    MinistryAssignment,
    MinistryLeadership,
    StructureStatus,
    Tribe,
    TribeAssignment,
    TribeLeadership,
)


def assign_member(structure, member, actor, *, is_tribe=False, replace_active=False):
    if structure.status != StructureStatus.ACTIVE:
        raise ValidationError('Une structure inactive ou archivée ne peut pas recevoir de nouvelle affectation.')
    if member.church_id != structure.church_id or member.organization_id != structure.church.organization_id:
        raise ValidationError('Le membre doit appartenir à la même église que la structure.')
    if member.status != Member.Status.ACTIVE:
        raise ValidationError('Seuls les membres actifs de l’église peuvent être affectés.')

    assignment_model = TribeAssignment if is_tribe else MinistryAssignment
    structure_field = 'tribe' if is_tribe else 'ministry'
    try:
        with transaction.atomic():
            Member.objects.select_for_update().get(pk=member.pk)
            if is_tribe:
                active = TribeAssignment.objects.select_for_update().filter(
                    member=member,
                    left_at__isnull=True,
                ).first()
                if active is not None:
                    if active.tribe_id == structure.pk:
                        raise ValidationError('Ce membre est déjà affecté à cette tribu.')
                    if not replace_active:
                        raise ValidationError('Ce membre est déjà affecté à une autre tribu de cette église.')
                    active.left_at = timezone.now()
                    active.ended_reason = 'Changement de tribu'
                    active.ended_by = actor
                    active.save(update_fields=['left_at', 'ended_reason', 'ended_by', 'updated_at'])
            elif assignment_model.objects.filter(
                member=member,
                **{structure_field: structure},
                left_at__isnull=True,
            ).exists():
                raise ValidationError('Ce membre est déjà affecté à ce ministère.')
            return assignment_model.objects.create(
                member=member,
                **{structure_field: structure},
                created_by=actor,
            )
    except IntegrityError as exc:
        raise ValidationError('Une affectation active existe déjà pour ce membre.') from exc


def close_assignment(assignment, actor, ended_reason=''):
    if assignment.left_at is not None:
        raise ValidationError('Cette affectation est déjà clôturée.')
    assignment.left_at = timezone.now()
    assignment.ended_reason = ended_reason
    assignment.ended_by = actor
    assignment.save(update_fields=['left_at', 'ended_reason', 'ended_by', 'updated_at'])
    return assignment


def appoint_leader(structure, member, role, actor, *, is_tribe=False):
    if structure.status != StructureStatus.ACTIVE:
        raise ValidationError('Une structure inactive ou archivée ne peut pas recevoir de nouveau responsable.')
    if member.church_id != structure.church_id or member.organization_id != structure.church.organization_id:
        raise ValidationError('Le responsable doit appartenir à la même église que la structure.')
    if member.status != Member.Status.ACTIVE:
        raise ValidationError('Seuls les membres actifs de l’église peuvent être responsables.')
    leadership_model = TribeLeadership if is_tribe else MinistryLeadership
    structure_field = 'tribe' if is_tribe else 'ministry'
    try:
        with transaction.atomic():
            structure.__class__.objects.select_for_update().get(pk=structure.pk)
            if role == LeadershipRole.LEADER:
                if leadership_model.objects.filter(
                    **{structure_field: structure},
                    role=LeadershipRole.LEADER,
                    ended_at__isnull=True,
                ).exists():
                    raise ValidationError('Cette structure a déjà un responsable principal actif.')
            return leadership_model.objects.create(
                member=member,
                role=role,
                appointed_by=actor,
                **{structure_field: structure},
            )
    except IntegrityError as exc:
        raise ValidationError('Cette fonction active existe déjà pour ce membre.') from exc


def close_leadership(leadership, actor, ended_reason=''):
    if leadership.ended_at is not None:
        raise ValidationError('Cette fonction est déjà clôturée.')
    leadership.ended_at = timezone.now()
    leadership.ended_reason = ended_reason
    leadership.ended_by = actor
    leadership.save(update_fields=['ended_at', 'ended_reason', 'ended_by', 'updated_at'])
    return leadership


@transaction.atomic
def close_member_church_roles(member, actor, ended_reason='Départ de l’église'):
    ended_at = timezone.now()
    for model in (MinistryAssignment, TribeAssignment):
        model.objects.filter(member=member, left_at__isnull=True).update(
            left_at=ended_at,
            ended_reason=ended_reason,
            ended_by=actor,
            updated_at=ended_at,
        )
    for model in (MinistryLeadership, TribeLeadership):
        model.objects.filter(member=member, ended_at__isnull=True).update(
            ended_at=ended_at,
            ended_reason=ended_reason,
            ended_by=actor,
            updated_at=ended_at,
        )

import calendar
import csv
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import OrganizationMembership
from infrastructure.tenancy import TenantContext

from .models import (
    PastoralAssignmentHistory,
    PastoralAuditEvent,
    PastoralCase,
    PastoralCaseAccess,
    PastoralInteraction,
    PastoralInteractionHistory,
    PastoralInteractionShare,
    PastoralNote,
    PastoralNoteRevision,
    PastoralNoteShare,
    PastoralNotification,
    PastoralNotificationReceipt,
    PastoralStatusHistory,
    PastoralTask,
    PastoralTaskHistory,
    PastoralWorkflow,
    PrayerRequest,
    PrayerRequestShare,
    PrayerRequestHistory,
)
from .serializers import (
    PastoralCaseCreateSerializer,
    PastoralCaseSerializer,
    PastoralCaseUpdateSerializer,
    PastoralPotentialDuplicateSerializer,
    PastoralInteractionCreateSerializer,
    PastoralInteractionSerializer,
    PastoralInteractionUpdateSerializer,
    PastoralNoteCreateSerializer,
    PastoralNoteSerializer,
    PastoralNoteUpdateSerializer,
    PastoralPrayerCreateSerializer,
    PastoralPrayerSerializer,
    PastoralPrayerUpdateSerializer,
    PastoralTaskCreateSerializer,
    PastoralTaskSerializer,
    PastoralTaskUpdateSerializer,
    PastoralTransitionSerializer,
    PastoralWorkflowSerializer,
    RevokeShareSerializer,
    ShareSerializer,
)
from .services import (
    accessible_case_queryset,
    accessible_prayer_requests,
    assign_case,
    can_access_case,
    can_access_note,
    can_access_prayer,
    can_read_interaction,
    create_case,
    create_prayer_request,
    get_workflow,
    has_any_scoped_permission,
    has_organization_permission,
    has_scoped_permission,
    record_audit,
    require_case_access,
    require_scoped_permission,
    revise_note,
    transition_case,
    user_in_organization,
    validate_location,
    validate_subject,
)


class PastoralPagination(LimitOffsetPagination):
    default_limit = 25
    max_limit = 100


def _organization():
    organization = TenantContext.get()
    if organization is None:
        raise PermissionDenied('Sélectionnez une organisation avant d’accéder aux dossiers pastoraux.')
    return organization


def _case_for_request(request, case_id, permission_code='pastoral.case.read', edit=False):
    organization = _organization()
    case = PastoralCase.objects.filter(
        pk=case_id,
        organization=organization,
    ).select_related('church', 'campus', 'member', 'person', 'assigned_to', 'created_by').first()
    if case is None:
        raise NotFound('Dossier pastoral introuvable.')
    if not can_access_case(request.user, case, permission_code, edit):
        record_audit(
            organization, request.user, 'CASE_ACCESS_DENIED', 'PastoralCase', case.pk,
            case.church, result='DENIED',
        )
        raise NotFound('Dossier pastoral introuvable.')
    return case


def _visible_cases(user, organization):
    candidates = accessible_case_queryset(user, organization)
    visible_ids = [
        case.pk for case in candidates.iterator()
        if can_access_case(user, case, 'pastoral.case.read')
    ]
    return candidates.filter(pk__in=visible_ids)


def _audit_history(case, actor, action, history_type, object_id=''):
    return record_audit(
        case.organization, actor, action, history_type, object_id, case.church,
    )


class PastoralCaseListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        if not has_any_scoped_permission(request.user, organization, 'pastoral.case.read'):
            raise PermissionDenied('Vous ne disposez pas du droit de consulter les dossiers pastoraux.')
        queryset = _visible_cases(request.user, organization)
        for parameter, field in (
            ('status', 'status'),
            ('church_id', 'church_id'),
            ('campus_id', 'campus_id'),
            ('priority', 'priority'),
            ('sensitivity', 'sensitivity'),
        ):
            value = request.query_params.get(parameter)
            if value:
                queryset = queryset.filter(**{field: value})
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(category__icontains=search))
        paginator = PastoralPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        record_audit(organization, request.user, 'CASE_LIST_VIEWED', 'PastoralCase', church=None)
        return paginator.get_paginated_response(PastoralCaseSerializer(page, many=True).data)

    def post(self, request):
        serializer = PastoralCaseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = create_case(
            actor=request.user,
            organization=_organization(),
            data=serializer.validated_data,
        )
        duplicates = _visible_cases(request.user, case.organization).filter(
            Q(member=case.member) if case.member_id else Q(person=case.person),
        ).exclude(pk=case.pk).exclude(status='CLOSED').values('id', 'title', 'status')[:10]
        response = PastoralCaseSerializer(case).data
        response['possible_duplicates'] = list(duplicates)
        return Response(response, status=status.HTTP_201_CREATED)


class PastoralCasePotentialDuplicatesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        serializer = PastoralPotentialDuplicateSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        filters = serializer.validated_data
        church, campus = validate_location(
            organization, filters['church_id'], filters.get('campus_id'),
        )
        member, person = validate_subject(
            organization, church, campus, filters.get('member_id'), filters.get('person_id'),
        )
        if not has_scoped_permission(
            request.user, organization, church, campus, 'pastoral.case.create',
        ):
            raise PermissionDenied('Vous ne pouvez pas créer de dossier dans ce périmètre.')
        candidates = PastoralCase.objects.filter(
            organization=organization,
            status__in=['NEW', 'TO_ASSIGN', 'IN_PROGRESS', 'WAITING', 'RESOLVED'],
        )
        candidates = candidates.filter(member=member) if member else candidates.filter(person=person)
        visible = [
            case for case in candidates.select_related('church')
            if can_access_case(request.user, case)
        ]
        return Response([{
            'id': case.pk,
            'title': case.title,
            'status': case.status,
            'church_name': case.church.name,
        } for case in visible])


class PastoralCaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = _case_for_request(request, case_id)
        record_audit(case.organization, request.user, 'CASE_VIEWED', 'PastoralCase', case.pk, case.church)
        return Response(PastoralCaseSerializer(case).data)

    def patch(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.case.update', edit=True)
        if case.closed_at:
            raise PermissionDenied('Un dossier clôturé ne peut être modifié que par une action de réouverture autorisée.')
        serializer = PastoralCaseUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(case, field, value)
        case.save()
        record_audit(case.organization, request.user, 'CASE_UPDATED', 'PastoralCase', case.pk, case.church)
        return Response(PastoralCaseSerializer(case).data)


class PastoralCaseAssignmentView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, case_id):
        organization = _organization()
        case = get_object_or_404(PastoralCase, pk=case_id, organization=organization)
        assignee_id = request.data.get('assigned_to')
        reason = request.data.get('reason', '')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise serializers.ValidationError({'reason': 'Un motif de réaffectation valide est requis.'})
        assignee = None
        if assignee_id is not None:
            try:
                assignee = OrganizationMembership.objects.get(
                    user_id=assignee_id,
                    organization=organization,
                    is_active=True,
                ).user
            except OrganizationMembership.DoesNotExist as error:
                raise serializers.ValidationError({'assigned_to': 'Le responsable doit appartenir à cette organisation.'}) from error
        case = assign_case(actor=request.user, case=case, assignee=assignee, reason=reason)
        return Response(PastoralCaseSerializer(case).data)


class PastoralCaseAssignmentSuggestionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.case.assign', edit=True)
        candidates = OrganizationMembership.objects.filter(
            organization=case.organization,
            is_active=True,
            user__is_active=True,
        ).select_related('user')
        suggestions = []
        for membership in candidates:
            user = membership.user
            if not has_scoped_permission(
                user, case.organization, case.church, case.campus, 'pastoral.case.read',
            ):
                continue
            active_case_count = PastoralCase.objects.filter(
                organization=case.organization,
                assigned_to=user,
            ).exclude(status='CLOSED').count()
            suggestions.append({
                'id': user.pk,
                'username': user.get_username(),
                'active_case_count': active_case_count,
            })
        suggestions.sort(key=lambda item: (item['active_case_count'], item['username'].casefold()))
        return Response(suggestions)


class PastoralCaseShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.case.share', edit=True)
        serializer = ShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data['user_id']
        membership = OrganizationMembership.objects.filter(
            user_id=user_id, organization=case.organization, is_active=True,
        ).select_related('user').first()
        if membership is None:
            raise serializers.ValidationError({'user_id': 'Utilisateur hors de cette organisation.'})
        access, created = PastoralCaseAccess.objects.update_or_create(
            case=case,
            user=membership.user,
            defaults={
                'access_level': serializer.validated_data['access_level'],
                'source': PastoralCaseAccess.Source.SHARE,
                'granted_by': request.user,
            },
        )
        record_audit(
            case.organization, request.user, 'CASE_SHARED', 'PastoralCaseAccess', access.pk, case.church,
            metadata={'recipient_user_id': user_id, 'created': created},
        )
        return Response({'shared': True, 'access_level': access.access_level}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.case.share', edit=True)
        serializer = RevokeShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deleted, _ = PastoralCaseAccess.objects.filter(
            case=case,
            user_id=serializer.validated_data['user_id'],
            source=PastoralCaseAccess.Source.SHARE,
        ).delete()
        record_audit(
            case.organization, request.user, 'CASE_SHARE_REVOKED', 'PastoralCase',
            case.pk, case.church,
            metadata={'recipient_user_id': serializer.validated_data['user_id'], 'revoked': bool(deleted)},
        )
        return Response({'revoked': bool(deleted)})


class PastoralCaseHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = _case_for_request(request, case_id)
        assignments = case.assignment_history.select_related('previous_user', 'new_user', 'actor').all()
        statuses = case.status_history.select_related('actor').all()
        interactions = PastoralInteractionHistory.objects.filter(
            interaction__case=case,
        ).select_related('actor', 'interaction')
        return Response({
            'assignments': [{
                'previous_user': row.previous_user.username if row.previous_user else None,
                'new_user': row.new_user.username if row.new_user else None,
                'actor': row.actor.username,
                'reason': row.reason,
                'created_at': row.created_at,
            } for row in assignments],
            'statuses': [{
                'previous_status': row.previous_status,
                'new_status': row.new_status,
                'actor': row.actor.username,
                'reason': row.reason,
                'created_at': row.created_at,
            } for row in statuses],
            'interactions': [{
                'interaction_id': row.interaction_id,
                'previous_status': row.previous_status,
                'new_status': row.new_status,
                'previous_scheduled_at': row.previous_scheduled_at,
                'new_scheduled_at': row.new_scheduled_at,
                'actor': row.actor.username,
                'reason': row.reason,
                'created_at': row.created_at,
            } for row in interactions],
        })


class PastoralCaseTransitionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        serializer = PastoralTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = PastoralCase.objects.filter(pk=case_id, organization=_organization()).first()
        if case is None:
            raise NotFound('Dossier pastoral introuvable.')
        case = transition_case(
            actor=request.user,
            case=case,
            new_status=serializer.validated_data['status'],
            reason=serializer.validated_data['reason'],
            reopen_due_at=serializer.validated_data.get('next_due_at'),
        )
        return Response(PastoralCaseSerializer(case).data)


class PastoralCaseCloseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        payload = {**request.data, 'status': 'CLOSED'}
        serializer = PastoralTransitionSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        case = PastoralCase.objects.filter(pk=case_id, organization=_organization()).first()
        if case is None:
            raise NotFound('Dossier pastoral introuvable.')
        case = transition_case(
            actor=request.user, case=case, new_status='CLOSED',
            reason=serializer.validated_data['reason'],
        )
        return Response(PastoralCaseSerializer(case).data)


class PastoralCaseReopenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        payload = {**request.data, 'status': 'IN_PROGRESS'}
        serializer = PastoralTransitionSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        case = PastoralCase.objects.filter(pk=case_id, organization=_organization()).first()
        if case is None:
            raise NotFound('Dossier pastoral introuvable.')
        case = transition_case(
            actor=request.user, case=case, new_status='IN_PROGRESS',
            reason=serializer.validated_data['reason'],
            reopen_due_at=serializer.validated_data.get('next_due_at'),
        )
        return Response(PastoralCaseSerializer(case).data)


class PastoralCaseArchiveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.case.archive', edit=True)
        if not case.closed_at:
            raise serializers.ValidationError({'status': 'Seul un dossier clôturé peut être archivé.'})
        case.archived_at = timezone.now()
        case.save(update_fields=['archived_at', 'updated_at'])
        record_audit(case.organization, request.user, 'CASE_ARCHIVED', 'PastoralCase', case.pk, case.church)
        return Response(PastoralCaseSerializer(case).data)


class PastoralWorkflowView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        if not (
            has_organization_permission(request.user, organization, 'pastoral.workflow.read')
            or has_organization_permission(request.user, organization, 'pastoral.workflow.manage')
        ):
            raise PermissionDenied('Vous ne pouvez pas consulter le workflow pastoral.')
        return Response(PastoralWorkflowSerializer(get_workflow(organization)).data)

    def patch(self, request):
        organization = _organization()
        if not has_organization_permission(request.user, organization, 'pastoral.workflow.manage'):
            raise PermissionDenied('Vous ne pouvez pas modifier le workflow pastoral.')
        workflow = get_workflow(organization)
        serializer = PastoralWorkflowSerializer(
            workflow,
            data=request.data,
            partial=True,
            context={'organization': organization},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        record_audit(organization, request.user, 'WORKFLOW_UPDATED', 'PastoralWorkflow', organization.tenant_id)
        return Response(serializer.data)


class PastoralCaseInteractionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = _case_for_request(request, case_id)
        interactions = case.interactions.select_related('created_by').all()
        record_audit(case.organization, request.user, 'INTERACTIONS_VIEWED', 'PastoralCase', case.pk, case.church)
        return Response(PastoralInteractionSerializer(interactions, many=True, context={'request': request}).data)

    @transaction.atomic
    def post(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.interaction.create', edit=True)
        serializer = PastoralInteractionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        workflow = get_workflow(case.organization)
        configured_kinds = {
            item['code'] for item in workflow.interaction_kinds if isinstance(item, dict)
        }
        if values['kind'] not in configured_kinds:
            raise serializers.ValidationError({'kind': 'Type d’interaction non configuré.'})
        interaction = PastoralInteraction.objects.create(case=case, created_by=request.user, **values)
        if (
            interaction.sensitivity == PastoralInteraction.Sensitivity.RESTRICTED
            and values.get('outcome')
            and not has_scoped_permission(
                request.user, case.organization, case.church, case.campus,
                'pastoral.interaction.restricted.read',
            )
        ):
            raise PermissionDenied('Un compte rendu restreint exige la permission de lecture correspondante.')
        PastoralInteractionHistory.objects.create(
            interaction=interaction,
            new_status=interaction.status,
            new_scheduled_at=interaction.scheduled_at,
            actor=request.user,
            reason='Création de l’interaction',
        )
        record_audit(case.organization, request.user, 'INTERACTION_CREATED', 'PastoralInteraction', interaction.pk, case.church)
        return Response(
            PastoralInteractionSerializer(interaction, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class PastoralInteractionKindsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = _case_for_request(request, case_id)
        return Response(get_workflow(case.organization).interaction_kinds)


class PastoralInteractionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, case_id, interaction_id):
        case = _case_for_request(request, case_id, 'pastoral.interaction.update', edit=True)
        interaction = get_object_or_404(PastoralInteraction, pk=interaction_id, case=case)
        serializer = PastoralInteractionUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        previous_status = interaction.status
        previous_scheduled_at = interaction.scheduled_at
        previous_outcome = interaction.outcome
        if 'outcome' in values and not can_read_interaction(request.user, interaction):
            record_audit(
                case.organization, request.user, 'INTERACTION_OUTCOME_ACCESS_DENIED',
                'PastoralInteraction', interaction.pk, case.church, result='DENIED',
            )
            raise NotFound('Interaction pastorale introuvable.')
        for field, value in values.items():
            if field != 'reason':
                setattr(interaction, field, value)
        interaction.full_clean(exclude=['case', 'created_by'])
        interaction.save()
        if (
            previous_status != interaction.status
            or previous_scheduled_at != interaction.scheduled_at
            or previous_outcome != interaction.outcome
        ):
            PastoralInteractionHistory.objects.create(
                interaction=interaction,
                previous_status=previous_status,
                new_status=interaction.status,
                previous_scheduled_at=previous_scheduled_at,
                new_scheduled_at=interaction.scheduled_at,
                actor=request.user,
                reason=values['reason'],
            )
        record_audit(case.organization, request.user, 'INTERACTION_UPDATED', 'PastoralInteraction', interaction.pk, case.church)
        return Response(PastoralInteractionSerializer(interaction, context={'request': request}).data)


class PastoralInteractionShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id, interaction_id):
        case = _case_for_request(request, case_id, 'pastoral.interaction.share', edit=True)
        interaction = get_object_or_404(PastoralInteraction, pk=interaction_id, case=case)
        serializer = ShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data['user_id']
        if not OrganizationMembership.objects.filter(
            user_id=user_id, organization=case.organization, is_active=True,
        ).exists():
            raise serializers.ValidationError({'user_id': 'Utilisateur hors de cette organisation.'})
        PastoralInteractionShare.objects.get_or_create(
            interaction=interaction, user_id=user_id, defaults={'shared_by': request.user},
        )
        record_audit(case.organization, request.user, 'INTERACTION_SHARED', 'PastoralInteraction', interaction.pk, case.church)
        return Response({'shared': True}, status=status.HTTP_201_CREATED)

    def delete(self, request, case_id, interaction_id):
        case = _case_for_request(request, case_id, 'pastoral.interaction.share', edit=True)
        interaction = get_object_or_404(PastoralInteraction, pk=interaction_id, case=case)
        serializer = RevokeShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deleted, _ = PastoralInteractionShare.objects.filter(
            interaction=interaction, user_id=serializer.validated_data['user_id'],
        ).delete()
        record_audit(
            case.organization, request.user, 'INTERACTION_SHARE_REVOKED',
            'PastoralInteraction', interaction.pk, case.church,
            metadata={'recipient_user_id': serializer.validated_data['user_id'], 'revoked': bool(deleted)},
        )
        return Response({'revoked': bool(deleted)})


class PastoralCaseNoteView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = _case_for_request(request, case_id)
        notes = case.notes.select_related('author').all()
        visible = [note for note in notes if can_access_note(request.user, note)]
        record_audit(case.organization, request.user, 'NOTES_VIEWED', 'PastoralCase', case.pk, case.church)
        return Response(PastoralNoteSerializer(
            visible, many=True, context={'request': request},
        ).data)

    def post(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.note.create', edit=True)
        serializer = PastoralNoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = PastoralNote.objects.create(
            case=case,
            author=request.user,
            **serializer.validated_data,
        )
        record_audit(case.organization, request.user, 'NOTE_CREATED', 'PastoralNote', note.pk, case.church)
        return Response(
            PastoralNoteSerializer(note, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class PastoralNoteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, case_id, note_id):
        case = _case_for_request(request, case_id)
        note = get_object_or_404(PastoralNote, pk=note_id, case=case)
        if not can_access_note(request.user, note, edit=True):
            record_audit(case.organization, request.user, 'NOTE_ACCESS_DENIED', 'PastoralNote', note.pk, case.church, result='DENIED')
            raise NotFound('Note pastorale introuvable.')
        serializer = PastoralNoteUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = revise_note(
            actor=request.user,
            note=note,
            content=serializer.validated_data['content'],
            reason=serializer.validated_data['reason'],
        )
        return Response(PastoralNoteSerializer(note, context={'request': request}).data)


class PastoralNoteShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id, note_id):
        case = _case_for_request(request, case_id, 'pastoral.note.share', edit=True)
        note = get_object_or_404(PastoralNote, pk=note_id, case=case)
        if note.author_id != request.user.id:
            raise NotFound('Note pastorale introuvable.')
        serializer = ShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data['user_id']
        if not OrganizationMembership.objects.filter(
            user_id=user_id, organization=case.organization, is_active=True,
        ).exists():
            raise serializers.ValidationError({'user_id': 'Utilisateur hors de cette organisation.'})
        share, created = PastoralNoteShare.objects.update_or_create(
            note=note,
            user_id=user_id,
            defaults={'can_edit': serializer.validated_data['can_edit'], 'shared_by': request.user},
        )
        record_audit(case.organization, request.user, 'NOTE_SHARED', 'PastoralNote', note.pk, case.church)
        return Response({'shared': True, 'can_edit': share.can_edit}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, case_id, note_id):
        case = _case_for_request(request, case_id, 'pastoral.note.share', edit=True)
        note = get_object_or_404(PastoralNote, pk=note_id, case=case)
        if note.author_id != request.user.id:
            raise NotFound('Note pastorale introuvable.')
        serializer = RevokeShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deleted, _ = PastoralNoteShare.objects.filter(
            note=note, user_id=serializer.validated_data['user_id'],
        ).delete()
        record_audit(
            case.organization, request.user, 'NOTE_SHARE_REVOKED',
            'PastoralNote', note.pk, case.church,
            metadata={'recipient_user_id': serializer.validated_data['user_id'], 'revoked': bool(deleted)},
        )
        return Response({'revoked': bool(deleted)})


class PastoralNoteHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id, note_id):
        case = _case_for_request(request, case_id)
        note = get_object_or_404(PastoralNote, pk=note_id, case=case)
        if not can_access_note(request.user, note):
            raise NotFound('Note pastorale introuvable.')
        revisions = note.revisions.select_related('actor').all()
        record_audit(case.organization, request.user, 'NOTE_HISTORY_VIEWED', 'PastoralNote', note.pk, case.church)
        return Response([{
            'content': revision.content,
            'actor': revision.actor.username,
            'reason': revision.reason,
            'created_at': revision.created_at,
        } for revision in revisions])


class PastoralPrayerRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        if not has_any_scoped_permission(request.user, organization, 'pastoral.prayer.read'):
            raise PermissionDenied('Vous ne disposez pas du droit de consulter les demandes de prière.')
        queryset = accessible_prayer_requests(request.user, organization)
        ids = [item.pk for item in queryset.iterator() if can_access_prayer(request.user, item)]
        queryset = queryset.filter(pk__in=ids)
        if request.query_params.get('status'):
            queryset = queryset.filter(status=request.query_params['status'])
        paginator = PastoralPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        record_audit(organization, request.user, 'PRAYER_LIST_VIEWED', 'PrayerRequest')
        return paginator.get_paginated_response(PastoralPrayerSerializer(page, many=True).data)

    def post(self, request):
        serializer = PastoralPrayerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = create_prayer_request(
            actor=request.user,
            organization=_organization(),
            data=serializer.validated_data,
        )
        return Response(PastoralPrayerSerializer(item).data, status=status.HTTP_201_CREATED)


class PastoralPrayerRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, request_id):
        item = get_object_or_404(PrayerRequest, pk=request_id, organization=_organization())
        if not can_access_prayer(request.user, item, 'pastoral.prayer.update'):
            record_audit(item.organization, request.user, 'PRAYER_ACCESS_DENIED', 'PrayerRequest', item.pk, item.church, result='DENIED')
            raise NotFound('Demande de prière introuvable.')
        serializer = PastoralPrayerUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        assignee = values.get('assigned_to', item.assigned_to)
        if assignee and not user_in_organization(assignee, item.organization):
            raise serializers.ValidationError({'assigned_to': 'Responsable hors de cette organisation.'})
        previous_status = item.status
        previous_assignee = item.assigned_to
        if 'assigned_to' in values:
            item.assigned_to = assignee
        if 'status' in values:
            item.status = values['status']
        item.save()
        if previous_status != item.status or previous_assignee != item.assigned_to:
            PrayerRequestHistory.objects.create(
                request=item,
                previous_status=previous_status,
                new_status=item.status,
                previous_assignee=previous_assignee,
                new_assignee=item.assigned_to,
                actor=request.user,
                reason=values['reason'],
            )
        record_audit(item.organization, request.user, 'PRAYER_UPDATED', 'PrayerRequest', item.pk, item.church)
        return Response(PastoralPrayerSerializer(item).data)


class PastoralPrayerRequestShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        item = get_object_or_404(PrayerRequest, pk=request_id, organization=_organization())
        if not can_access_prayer(request.user, item, 'pastoral.prayer.share'):
            raise NotFound('Demande de prière introuvable.')
        serializer = ShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data['user_id']
        if not OrganizationMembership.objects.filter(
            user_id=user_id, organization=item.organization, is_active=True,
        ).exists():
            raise serializers.ValidationError({'user_id': 'Utilisateur hors de cette organisation.'})
        PrayerRequestShare.objects.get_or_create(
            request=item, user_id=user_id, defaults={'shared_by': request.user},
        )
        record_audit(item.organization, request.user, 'PRAYER_SHARED', 'PrayerRequest', item.pk, item.church)
        return Response({'shared': True}, status=status.HTTP_201_CREATED)

    def delete(self, request, request_id):
        item = get_object_or_404(PrayerRequest, pk=request_id, organization=_organization())
        if not can_access_prayer(request.user, item, 'pastoral.prayer.share'):
            raise NotFound('Demande de prière introuvable.')
        serializer = RevokeShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deleted, _ = PrayerRequestShare.objects.filter(
            request=item, user_id=serializer.validated_data['user_id'],
        ).delete()
        record_audit(
            item.organization, request.user, 'PRAYER_SHARE_REVOKED',
            'PrayerRequest', item.pk, item.church,
            metadata={'recipient_user_id': serializer.validated_data['user_id'], 'revoked': bool(deleted)},
        )
        return Response({'revoked': bool(deleted)})


class PastoralPrayerRequestHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, request_id):
        item = PrayerRequest.objects.filter(pk=request_id, organization=_organization()).first()
        if item is None or not can_access_prayer(request.user, item):
            raise NotFound('Demande de prière introuvable.')
        rows = item.history.select_related('actor', 'previous_assignee', 'new_assignee').all()
        return Response([{
            'previous_status': row.previous_status,
            'new_status': row.new_status,
            'previous_assignee': row.previous_assignee.username if row.previous_assignee else None,
            'new_assignee': row.new_assignee.username if row.new_assignee else None,
            'actor': row.actor.username,
            'reason': row.reason,
            'created_at': row.created_at,
        } for row in rows])


class PastoralCaseTaskView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = _case_for_request(request, case_id)
        tasks = case.tasks.select_related('assigned_to', 'created_by', 'interaction').all()
        return Response(PastoralTaskSerializer(tasks, many=True).data)

    @transaction.atomic
    def post(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.task.create', edit=True)
        serializer = PastoralTaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        assignee = values['assigned_to']
        if not user_in_organization(assignee, case.organization):
            raise serializers.ValidationError({'assigned_to': 'Responsable hors de cette organisation.'})
        interaction = None
        if values.get('interaction_id'):
            interaction = get_object_or_404(PastoralInteraction, pk=values['interaction_id'], case=case)
        require_scoped_permission(
            assignee, case.organization, case.church, case.campus, 'pastoral.case.read',
        )
        task = PastoralTask.objects.create(
            case=case,
            interaction=interaction,
            created_by=request.user,
            **{key: value for key, value in values.items() if key != 'interaction_id'},
        )
        PastoralTaskHistory.objects.create(
            task=task,
            new_status=task.status,
            new_assignee=task.assigned_to,
            actor=request.user,
            reason='Création de la tâche',
        )
        record_audit(case.organization, request.user, 'TASK_CREATED', 'PastoralTask', task.pk, case.church)
        return Response(PastoralTaskSerializer(task).data, status=status.HTTP_201_CREATED)


class PastoralTaskListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        cases = _visible_cases(request.user, organization).values('id')
        tasks = PastoralTask.objects.filter(case_id__in=cases, assigned_to=request.user).select_related('case', 'assigned_to')
        if request.query_params.get('status'):
            tasks = tasks.filter(status=request.query_params['status'])
        return Response(PastoralTaskSerializer(tasks, many=True).data)


class PastoralTaskDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, task_id):
        task = PastoralTask.objects.filter(
            pk=task_id,
            case__organization=_organization(),
        ).select_related('case', 'case__church', 'case__campus').select_for_update(of=('self',)).first()
        if task is None or not can_access_case(request.user, task.case):
            raise NotFound('Tâche pastorale introuvable.')
        if task.assigned_to_id != request.user.id and not can_access_case(
            request.user, task.case, 'pastoral.task.manage', edit=True,
        ):
            raise NotFound('Tâche pastorale introuvable.')
        serializer = PastoralTaskUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if 'assigned_to' in values:
            if not user_in_organization(values['assigned_to'], task.case.organization):
                raise serializers.ValidationError({'assigned_to': 'Responsable hors de cette organisation.'})
            require_scoped_permission(
                values['assigned_to'], task.case.organization, task.case.church, task.case.campus, 'pastoral.case.read',
            )
        if values.get('recurrence') and values.get('due_at', task.due_at) is None:
            raise serializers.ValidationError({'due_at': 'Une échéance est requise pour une tâche récurrente.'})
        previous_status = task.status
        previous_assignee = task.assigned_to
        was_complete = task.status in [PastoralTask.Status.DONE, PastoralTask.Status.CANCELLED]
        for field, value in values.items():
            if field != 'reason':
                setattr(task, field, value)
        task.save()
        if previous_status != task.status or previous_assignee != task.assigned_to:
            PastoralTaskHistory.objects.create(
                task=task,
                previous_status=previous_status,
                new_status=task.status,
                previous_assignee=previous_assignee,
                new_assignee=task.assigned_to,
                actor=request.user,
                reason=values['reason'],
            )
        if task.status == PastoralTask.Status.DONE and not was_complete and task.recurrence:
            next_due_at = task.due_at + (
                timedelta(days=1) if task.recurrence == 'DAILY'
                else timedelta(weeks=1) if task.recurrence == 'WEEKLY'
                else timedelta(days=0)
            )
            if task.recurrence == 'MONTHLY':
                month = task.due_at.month % 12 + 1
                year = task.due_at.year + (1 if task.due_at.month == 12 else 0)
                next_due_at = task.due_at.replace(
                    year=year, month=month, day=min(task.due_at.day, calendar.monthrange(year, month)[1]),
                )
            next_task = PastoralTask.objects.create(
                case=task.case,
                interaction=task.interaction,
                title=task.title,
                description=task.description,
                assigned_to=task.assigned_to,
                priority=task.priority,
                due_at=next_due_at,
                status=PastoralTask.Status.OPEN,
                recurrence=task.recurrence,
                created_by=request.user,
            )
            PastoralTaskHistory.objects.create(
                task=next_task,
                new_status=next_task.status,
                new_assignee=next_task.assigned_to,
                actor=request.user,
                reason='Création depuis la récurrence',
            )
        record_audit(task.case.organization, request.user, 'TASK_UPDATED', 'PastoralTask', task.pk, task.case.church)
        return Response(PastoralTaskSerializer(task).data)


class PastoralTaskHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        task = PastoralTask.objects.filter(
            pk=task_id, case__organization=_organization(),
        ).select_related('case').first()
        if task is None or not can_access_case(request.user, task.case):
            raise NotFound('Tâche pastorale introuvable.')
        rows = task.history.select_related('actor', 'previous_assignee', 'new_assignee').all()
        return Response([{
            'previous_status': row.previous_status,
            'new_status': row.new_status,
            'previous_assignee': row.previous_assignee.username if row.previous_assignee else None,
            'new_assignee': row.new_assignee.username if row.new_assignee else None,
            'actor': row.actor.username,
            'reason': row.reason,
            'created_at': row.created_at,
        } for row in rows])


class PastoralNotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        notifications = PastoralNotification.objects.filter(
            organization=_organization(),
            recipient=request.user,
        ).select_related('task', 'task__case').distinct()
        visible = [item for item in notifications if can_access_case(request.user, item.task.case)]
        return Response([{
            'id': item.pk,
            'type': 'TASK_DUE',
            'message': 'Une tâche de suivi pastoral arrive à échéance ou est en retard.',
            'task_id': item.task_id,
            'case_id': item.task.case_id,
            'created_at': item.created_at,
            'read_at': item.receipts.filter(user=request.user).values_list('read_at', flat=True).first(),
        } for item in visible])

    def post(self, request, notification_id):
        notification = get_object_or_404(
            PastoralNotification,
            pk=notification_id,
            organization=_organization(),
            recipient=request.user,
        )
        if not can_access_case(request.user, notification.task.case):
            raise NotFound('Notification introuvable.')
        PastoralNotificationReceipt.objects.update_or_create(
            notification=notification,
            user=request.user,
            defaults={'read_at': timezone.now()},
        )
        return Response({'read': True})


class PastoralDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        cases = _visible_cases(request.user, organization)
        if not has_any_scoped_permission(request.user, organization, 'pastoral.report.read'):
            raise PermissionDenied('Vous ne pouvez pas consulter le tableau de bord pastoral.')
        now = timezone.now()
        tasks = PastoralTask.objects.filter(case__in=cases)
        interactions = PastoralInteraction.objects.filter(case__in=cases)
        prayers = accessible_prayer_requests(request.user, organization)
        prayer_ids = [
            item.pk for item in prayers.iterator()
            if can_access_prayer(request.user, item, 'pastoral.prayer.read')
        ]
        unassigned_cases = cases.filter(assigned_to__isnull=True)
        unassigned_alert_days = get_workflow(organization).unassigned_alert_after_days
        return Response({
            'cases_by_status': list(cases.values('status').annotate(total=Count('id')).order_by('status')),
            'open_cases': cases.exclude(status='CLOSED').count(),
            'closed_cases': cases.filter(status='CLOSED').count(),
            'waiting_cases': cases.filter(status='WAITING').count(),
            'unassigned_cases': unassigned_cases.count(),
            'unassigned_alerts': (
                unassigned_cases.filter(created_at__lt=now - timedelta(days=unassigned_alert_days)).count()
                if unassigned_alert_days is not None else 0
            ),
            'overdue_tasks': tasks.filter(
                due_at__lt=now,
                status__in=[PastoralTask.Status.OPEN, PastoralTask.Status.IN_PROGRESS],
            ).count(),
            'interactions_completed': interactions.filter(status=PastoralInteraction.Status.COMPLETED).count(),
            'interactions_missed': interactions.filter(status=PastoralInteraction.Status.MISSED).count(),
            'prayer_requests_followed': PrayerRequest.objects.filter(
                pk__in=prayer_ids, status=PrayerRequest.Status.IN_PROGRESS,
            ).count(),
        })


class PastoralReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        if not has_any_scoped_permission(request.user, organization, 'pastoral.report.read'):
            raise PermissionDenied('Vous ne pouvez pas consulter les rapports pastoraux.')
        cases = _visible_cases(request.user, organization)
        period_from = request.query_params.get('from')
        period_to = request.query_params.get('to')
        try:
            start_date = parse_date(period_from) if period_from else None
        except ValueError:
            start_date = None
        try:
            end_date = parse_date(period_to) if period_to else None
        except ValueError:
            end_date = None
        if period_from and start_date is None:
            raise serializers.ValidationError({'from': 'Date invalide, format attendu AAAA-MM-JJ.'})
        if period_to and end_date is None:
            raise serializers.ValidationError({'to': 'Date invalide, format attendu AAAA-MM-JJ.'})
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError({'to': 'La date de fin doit être postérieure à la date de début.'})
        if start_date:
            cases = cases.filter(created_at__date__gte=start_date)
        if end_date:
            cases = cases.filter(created_at__date__lte=end_date)
        report = {
            'period_from': period_from,
            'period_to': period_to,
            'cases_by_status': list(cases.values('status').annotate(total=Count('id')).order_by('status')),
            'cases_by_priority': list(cases.values('priority').annotate(total=Count('id')).order_by('priority')),
            'open_tasks': PastoralTask.objects.filter(
                case__in=cases,
            ).exclude(status__in=[PastoralTask.Status.DONE, PastoralTask.Status.CANCELLED]).count(),
        }
        if request.query_params.get('export') != 'csv':
            return Response(report)
        if not has_any_scoped_permission(request.user, organization, 'pastoral.export'):
            raise PermissionDenied('Vous ne pouvez pas exporter ces indicateurs pastoraux.')
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="pastoral-report.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['Indicateur pastoral', 'Valeur', 'Période du', period_from or '', 'au', period_to or ''])
        for item in report['cases_by_status']:
            writer.writerow(['Dossiers par statut', item['total'], item['status']])
        for item in report['cases_by_priority']:
            writer.writerow(['Dossiers par priorité', item['total'], item['priority']])
        writer.writerow(['Tâches ouvertes', report['open_tasks']])
        return response


class PastoralAuditView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = _case_for_request(request, case_id, 'pastoral.audit.read')
        related_object_ids = [str(case.pk)]
        related_object_ids.extend(str(value) for value in case.interactions.values_list('pk', flat=True))
        related_object_ids.extend(str(value) for value in case.notes.values_list('pk', flat=True))
        related_object_ids.extend(str(value) for value in case.tasks.values_list('pk', flat=True))
        related_object_ids.extend(
            str(value) for value in case.prayer_requests.values_list('pk', flat=True)
        )
        entries = PastoralAuditEvent.objects.filter(
            organization=case.organization,
            object_id__in=related_object_ids,
        ).select_related('actor')[:100]
        return Response([{
            'action': event.action,
            'actor': event.actor.username if event.actor else None,
            'result': event.result,
            'metadata': event.metadata,
            'created_at': event.created_at,
        } for event in entries])

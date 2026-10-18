from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Campus
from apps.core.services.configuration_access import (
    configuration_church_ids,
    has_configuration_permission,
)
from apps.members.models import Member
from infrastructure.tenancy import TenantContext

from .models import (
    AbsenceExcuse,
    AttendanceAlert,
    AttendanceAlertNote,
    AttendanceAuditLog,
    AttendancePolicy,
    AttendanceAlertStatus,
    AttendanceRecord,
    AttendanceSession,
    AttendanceSessionType,
    AttendanceStatus,
    ExpectedParticipant,
    ExcuseStatus,
    SessionLifecycle,
    SessionMethod,
)
from .permissions import (
    IsAttendanceManager,
    IsAttendanceOrganizationAdmin,
    is_attendance_organization_admin,
)
from .serializers import (
    AbsenceExcuseSerializer,
    AttendanceCheckInSerializer,
    AttendanceCorrectionHistorySerializer,
    AttendanceCorrectionSerializer,
    AttendanceRecordSerializer,
    AttendanceSessionCreateSerializer,
    AttendanceSessionSerializer,
    AttendanceSessionTypeSerializer,
    AttendanceAlertNoteSerializer,
    AttendanceAlertSerializer,
    AttendancePolicySerializer,
    MemberAttendanceHistorySerializer,
    EarlyDepartureSerializer,
    ExpectedParticipantSerializer,
    ExcuseCreateSerializer,
    ReasonSerializer,
    VisitorCreateSerializer,
)
from .services import (
    correct_attendance,
    create_sessions,
    create_visitor,
    decide_excuse,
    evaluate_attendance_alerts,
    record_attendance,
    record_early_departure,
    record_audit,
    member_is_in_session_scope,
    submit_excuse,
    transition_session,
    weekly_report,
)


class AttendancePagination(LimitOffsetPagination):
    default_limit = 25
    max_limit = 100


def _session_queryset():
    recurrence_counts = AttendanceSession.objects.filter(
        recurrence_series_id=OuterRef('recurrence_series_id'),
    ).order_by().values('recurrence_series_id').annotate(total=Count('id')).values('total')[:1]
    return AttendanceSession.objects.filter(
        organization=TenantContext.get(),
    ).select_related('church', 'campus', 'session_type').annotate(
        _expected_count=Count(
            'expected_participants',
            filter=Q(expected_participants__expected_for_absence=True),
            distinct=True,
        ),
        _present_count=Count(
            'expected_participants__attendance',
            filter=Q(expected_participants__attendance__status__in=[
                AttendanceStatus.PRESENT,
                AttendanceStatus.LATE,
                AttendanceStatus.EARLY_DEPARTURE,
            ]),
            distinct=True,
        ),
        _recurrence_count=Subquery(recurrence_counts),
    )


class AttendanceSessionTypeListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request):
        queryset = AttendanceSessionType.objects.filter(organization=TenantContext.get())
        if not is_attendance_organization_admin(request.user, TenantContext.get()):
            queryset = queryset.filter(
                is_active=True,
                population_rule__in=['ALL_MEMBERS', 'MANUAL'],
            )
        paginator = AttendancePagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(AttendanceSessionTypeSerializer(page, many=True).data)

    def post(self, request):
        if not is_attendance_organization_admin(request.user, TenantContext.get()):
            return Response({'detail': 'Seul un administrateur peut configurer les types de séance.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = AttendanceSessionTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save(organization=TenantContext.get())
        record_audit(TenantContext.get(), 'SESSION_TYPE_CREATED', request.user, detail={'type_id': str(item.id)})
        return Response(AttendanceSessionTypeSerializer(item).data, status=status.HTTP_201_CREATED)


class AttendanceSessionTypeDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceOrganizationAdmin]

    def patch(self, request, type_id):
        item = get_object_or_404(AttendanceSessionType, pk=type_id, organization=TenantContext.get())
        serializer = AttendanceSessionTypeSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        record_audit(TenantContext.get(), 'SESSION_TYPE_UPDATED', request.user, detail={'type_id': str(item.id)})
        return Response(serializer.data)


class AttendanceSessionListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request):
        queryset = _session_queryset()
        if (
            not is_attendance_organization_admin(request.user, TenantContext.get())
            and not has_configuration_permission(request.user, 'attendance.read', TenantContext.get())
        ):
            from apps.events.permissions import managed_church_ids

            church_ids = set(managed_church_ids(request.user, TenantContext.get())) | configuration_church_ids(
                request.user,
                TenantContext.get(),
                'attendance',
            )
            queryset = queryset.filter(
                Q(church_id__in=church_ids)
                | Q(event_occurrence__event__church_id__in=church_ids)
                | Q(event_occurrence__event__campus__church_id__in=church_ids)
            ).distinct()
        status_filter = request.query_params.get('status')
        church_id = request.query_params.get('church_id')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if church_id:
            queryset = queryset.filter(church_id=church_id)
        if from_date:
            queryset = queryset.filter(starts_at__date__gte=from_date)
        if to_date:
            queryset = queryset.filter(starts_at__date__lte=to_date)
        page = AttendancePagination()
        results = page.paginate_queryset(queryset, request, view=self)
        return page.get_paginated_response(AttendanceSessionSerializer(results, many=True).data)

    def post(self, request):
        serializer = AttendanceSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not is_attendance_organization_admin(request.user, TenantContext.get()):
            requested_rule = serializer.validated_data.get('population_rule')
            session_type = None
            type_id = serializer.validated_data.get('session_type_id')
            if type_id:
                session_type = get_object_or_404(
                    AttendanceSessionType,
                    pk=type_id,
                    organization=TenantContext.get(),
                    is_active=True,
                )
            effective_rule = requested_rule or (session_type.population_rule if session_type else 'ALL_MEMBERS')
            if effective_rule not in ('ALL_MEMBERS', 'MANUAL'):
                return Response(
                    {'population_rule': 'Les responsables d’église peuvent sélectionner tous les membres ou une liste manuelle.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        sessions = create_sessions(request.user, serializer.validated_data)
        return Response({
            'session': AttendanceSessionSerializer(sessions[0]).data,
            'generated_sessions': AttendanceSessionSerializer(sessions, many=True).data,
        }, status=status.HTTP_201_CREATED)


class AttendanceSessionDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request, session_id):
        session = get_object_or_404(_session_queryset(), pk=session_id)
        return Response(AttendanceSessionSerializer(session).data)

    def get_queryset(self):
        return _session_queryset()


class AttendanceSessionActionView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def post(self, request, session_id, action):
        session = get_object_or_404(_session_queryset(), pk=session_id)
        if session.event_occurrence_id:
            raise ValidationError('Gérez le statut de cette séance depuis le module Événements.')
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated, raw_qr_token = transition_session(
            session,
            action,
            request.user,
            serializer.validated_data.get('reason', ''),
        )
        payload = AttendanceSessionSerializer(updated).data
        if raw_qr_token:
            payload['qr_token'] = raw_qr_token
        return Response(payload)


class ExpectedParticipantListView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request, session_id):
        session = get_object_or_404(_session_queryset(), pk=session_id)
        participants = session.expected_participants.select_related('member', 'visitor__person').prefetch_related('attendance__excuses')
        return Response(ExpectedParticipantSerializer(participants, many=True).data)

    @transaction.atomic
    def post(self, request, session_id):
        session = get_object_or_404(_session_queryset().select_for_update(), pk=session_id)
        if session.status != SessionLifecycle.PLANNED:
            raise ValidationError('La liste attendue ne peut être corrigée qu’avant l’ouverture.')
        member_id = request.data.get('member_id')
        reason = (request.data.get('reason') or '').strip()
        if not member_id or not reason:
            raise ValidationError({'reason': 'L’identifiant du membre et le motif de correction sont obligatoires.'})
        member = Member.objects.filter(
            pk=member_id,
            organization=session.organization,
            church_id=session.church_id,
            campus_id=session.campus_id if session.campus_id else None,
            status=Member.Status.ACTIVE,
            user__is_active=True,
        ).first()
        if member is None or not member_is_in_session_scope(session, member):
            raise ValidationError({'member_id': 'Membre introuvable, inactif ou hors périmètre.'})
        expected, created = ExpectedParticipant.objects.get_or_create(
            session=session,
            member=member,
            defaults={
                'first_name': member.first_name,
                'last_name': member.last_name,
                'added_by': request.user,
                'addition_reason': reason,
            },
        )
        if not created:
            raise ValidationError('Ce membre figure déjà dans la liste attendue.')
        record_audit(session.organization, 'EXPECTED_PARTICIPANT_ADDED', request.user, session, {
            'participant_id': str(expected.id),
            'reason': reason,
        })
        return Response(ExpectedParticipantSerializer(expected).data, status=status.HTTP_201_CREATED)

    def delete(self, request, session_id, participant_id):
        session = get_object_or_404(_session_queryset(), pk=session_id)
        if session.status != SessionLifecycle.PLANNED:
            raise ValidationError('La liste attendue ne peut être corrigée qu’avant l’ouverture.')
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'Le motif de retrait est obligatoire.'})
        participant = get_object_or_404(
            ExpectedParticipant,
            pk=participant_id,
            session=session,
            expected_for_absence=True,
        )
        participant.expected_for_absence = False
        participant.addition_reason = f'{participant.addition_reason}\nRetiré : {reason}'.strip()
        participant.save(update_fields=['expected_for_absence', 'addition_reason'])
        record_audit(session.organization, 'EXPECTED_PARTICIPANT_REMOVED', request.user, session, {
            'participant_id': str(participant.id),
            'reason': reason,
        })
        return Response(ExpectedParticipantSerializer(participant).data)


class AttendanceVisitorCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def post(self, request, session_id):
        session = get_object_or_404(_session_queryset(), pk=session_id)
        serializer = VisitorCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant = create_visitor(session, request.user, serializer.validated_data)
        return Response(ExpectedParticipantSerializer(participant).data, status=status.HTTP_201_CREATED)


class AttendanceCheckInView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def post(self, request, session_id):
        session = get_object_or_404(_session_queryset(), pk=session_id)
        if session.event_occurrence_id:
            raise ValidationError('Utilisez le pointage du module Événements pour cette occurrence.')
        serializer = AttendanceCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if values['method'] == SessionMethod.SELF:
            raise ValidationError({'method': 'L’auto-pointage doit utiliser le point d’accès membre.'})
        participant = get_object_or_404(
            ExpectedParticipant,
            pk=values['expected_participant_id'],
            session=session,
        )
        attendance, created = record_attendance(
            session,
            participant,
            request.user,
            values['method'],
            raw_qr_token=values.get('qr_token', ''),
        )
        return Response(
            {
                'result': 'RECORDED' if created else 'ALREADY_RECORDED',
                'detail': 'Présence enregistrée.' if created else 'Cette personne a déjà été pointée.',
                'attendance': AttendanceRecordSerializer(attendance).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AttendanceSelfCheckInView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(_session_queryset(), pk=session_id)
        if session.event_occurrence_id:
            raise ValidationError('Utilisez l’auto-pointage du module Événements pour cette occurrence.')
        member = Member.objects.filter(
            user=request.user,
            organization=session.organization,
            status=Member.Status.ACTIVE,
            user__is_active=True,
        ).first()
        if member is None or not member_is_in_session_scope(session, member):
            return Response({'detail': 'Aucun profil membre actif dans cette église.'}, status=status.HTTP_403_FORBIDDEN)
        participant = get_object_or_404(ExpectedParticipant, session=session, member=member)
        attendance, created = record_attendance(session, participant, request.user, SessionMethod.SELF)
        return Response(
            {
                'result': 'RECORDED' if created else 'ALREADY_RECORDED',
                'attendance': AttendanceRecordSerializer(attendance).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AttendanceRecordCorrectionView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request, attendance_id):
        attendance = get_object_or_404(
            AttendanceRecord.objects.select_related('expected_participant__session'),
            pk=attendance_id,
            expected_participant__session__organization=TenantContext.get(),
        )
        return Response(AttendanceCorrectionHistorySerializer(attendance.corrections.select_related('actor'), many=True).data)

    def post(self, request, attendance_id):
        attendance = get_object_or_404(
            AttendanceRecord.objects.select_related('expected_participant__session'),
            pk=attendance_id,
            expected_participant__session__organization=TenantContext.get(),
        )
        if attendance.event_attendance_id:
            raise ValidationError('Corrigez ce pointage depuis le module Événements pour conserver une source unique.')
        serializer = AttendanceCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        corrected = correct_attendance(
            attendance,
            request.user,
            serializer.validated_data['status'],
            serializer.validated_data['reason'],
        )
        return Response(AttendanceRecordSerializer(corrected).data)


class AttendanceExcuseListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, attendance_id):
        attendance = get_object_or_404(
            AttendanceRecord.objects.select_related('expected_participant__session'),
            pk=attendance_id,
            expected_participant__session__organization=TenantContext.get(),
        )
        if attendance.event_attendance_id:
            raise ValidationError('Consultez les justificatifs des occurrences depuis le module Événements.')
        if not _can_manage(
            request.user,
            attendance.expected_participant.session.organization,
            attendance.expected_participant.session.church_id,
        ):
            if not attendance.expected_participant.member_id or attendance.expected_participant.member.user_id != request.user.id:
                return Response({'detail': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)
        return Response(AbsenceExcuseSerializer(attendance.excuses.all(), many=True).data)

    def post(self, request, attendance_id):
        attendance = get_object_or_404(
            AttendanceRecord.objects.select_related(
                'expected_participant__session',
                'expected_participant__member',
            ),
            pk=attendance_id,
            expected_participant__session__organization=TenantContext.get(),
        )
        if attendance.event_attendance_id:
            raise ValidationError('Les justificatifs des occurrences ne sont pas gérés dans le registre central.')
        if not _can_manage(
            request.user,
            attendance.expected_participant.session.organization,
            attendance.expected_participant.session.church_id,
        ):
            if not attendance.expected_participant.member_id or attendance.expected_participant.member.user_id != request.user.id:
                return Response({'detail': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = ExcuseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        excuse = submit_excuse(attendance, request.user, serializer.validated_data['reason'])
        return Response(AbsenceExcuseSerializer(excuse).data, status=status.HTTP_201_CREATED)


class AttendanceExcuseDecisionView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def post(self, request, excuse_id, decision):
        excuse = get_object_or_404(
            AbsenceExcuse.objects.select_related('attendance__expected_participant__session'),
            pk=excuse_id,
            attendance__expected_participant__session__organization=TenantContext.get(),
        )
        if excuse.attendance.event_attendance_id:
            raise ValidationError('Les justificatifs des occurrences ne sont pas gérés dans le registre central.')
        if decision not in ('approve', 'reject'):
            raise ValidationError('Décision inconnue.')
        updated = decide_excuse(excuse, request.user, decision == 'approve')
        return Response(AbsenceExcuseSerializer(updated).data)


class AttendanceEarlyDepartureView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def post(self, request, attendance_id):
        attendance = get_object_or_404(
            AttendanceRecord.objects.select_related('expected_participant__session'),
            pk=attendance_id,
            expected_participant__session__organization=TenantContext.get(),
        )
        if attendance.event_attendance_id:
            raise ValidationError('Enregistrez le départ anticipé depuis le module Événements.')
        serializer = EarlyDepartureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = record_early_departure(
            attendance,
            request.user,
            serializer.validated_data['departed_at'],
            serializer.validated_data.get('reason', ''),
        )
        return Response(AttendanceRecordSerializer(result).data)


class AttendanceWeeklyReportView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request):
        today = timezone.localdate()
        try:
            start = date.fromisoformat(request.query_params.get('from', (today - timedelta(days=today.weekday())).isoformat()))
            end = date.fromisoformat(request.query_params.get('to', (start + timedelta(days=6)).isoformat()))
        except ValueError as error:
            raise ValidationError({'date': 'Utilisez le format AAAA-MM-JJ.'}) from error
        if end < start or (end - start).days > 366:
            raise ValidationError({'period': 'La période est invalide ou dépasse 366 jours.'})
        result = weekly_report(
            TenantContext.get(),
            start,
            end,
            request.query_params.get('church_id'),
            request.query_params.get('session_type_id'),
        )
        return Response(result)


def _can_manage(user, organization, church_id=None):
    if is_attendance_organization_admin(user, organization):
        return True
    if church_id is None:
        return False
    from apps.events.permissions import managed_church_ids

    return str(church_id) in {str(value) for value in managed_church_ids(user, organization)}


class AttendanceAlertListView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request):
        alerts = AttendanceAlert.objects.filter(
            organization=TenantContext.get(),
        ).select_related('member')
        church_ids = None
        if not is_attendance_organization_admin(request.user, TenantContext.get()):
            from apps.events.permissions import managed_church_ids

            church_ids = set(managed_church_ids(request.user, TenantContext.get())) | configuration_church_ids(
                request.user,
                TenantContext.get(),
                'attendance',
            )
            alerts = alerts.filter(member__church_id__in=church_ids)
        evaluate_attendance_alerts(TenantContext.get(), church_ids=church_ids)
        alert_status = request.query_params.get('status')
        if alert_status:
            alerts = alerts.filter(status=alert_status)
        return Response(AttendanceAlertSerializer(alerts, many=True).data)


class AttendanceAlertActionView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    @transaction.atomic
    def post(self, request, alert_id, action):
        alert = get_object_or_404(
            AttendanceAlert.objects.select_for_update(),
            pk=alert_id,
            organization=TenantContext.get(),
        )
        if action == 'resolve':
            if alert.status == AttendanceAlertStatus.RESOLVED:
                raise ValidationError('Cette alerte est déjà résolue.')
            alert.status = 'RESOLVED'
            alert.resolved_at = timezone.now()
        elif action == 'take':
            if alert.status not in (AttendanceAlertStatus.NEW, AttendanceAlertStatus.SEEN, AttendanceAlertStatus.IN_PROGRESS):
                raise ValidationError('Une alerte résolue ne peut pas être prise en charge.')
            alert.status = 'IN_PROGRESS'
        elif action == 'seen':
            if alert.status == AttendanceAlertStatus.RESOLVED:
                raise ValidationError('Une alerte résolue ne peut pas être marquée comme consultée.')
            if alert.status == 'NEW':
                alert.status = 'SEEN'
        elif action in ('note', 'contact'):
            if alert.status == AttendanceAlertStatus.RESOLVED:
                raise ValidationError('Une note ne peut pas être ajoutée à une alerte résolue.')
            if not (request.data.get('note') or '').strip():
                raise ValidationError({'note': 'Saisissez une note de suivi ou la prise de contact.'})
        else:
            raise ValidationError('Action d’alerte inconnue.')
        alert.save(update_fields=['status', 'resolved_at'])
        note = request.data.get('note', '')
        entry = AttendanceAlertNote.objects.create(
            alert=alert,
            actor=request.user,
            action=action.upper(),
            note=note.strip(),
        )
        record_audit(alert.organization, f'ALERT_{action.upper()}', request.user, detail={'alert_id': str(alert.id)})
        return Response({
            'alert': AttendanceAlertSerializer(alert).data,
            'note': AttendanceAlertNoteSerializer(entry).data,
        })


class AttendanceAlertNotesView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request, alert_id):
        alert = get_object_or_404(AttendanceAlert, pk=alert_id, organization=TenantContext.get())
        return Response(AttendanceAlertNoteSerializer(alert.notes.select_related('actor'), many=True).data)


class AttendanceAuditView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request, session_id):
        session = get_object_or_404(_session_queryset(), pk=session_id)
        return Response(session.audit_logs.select_related('actor').values(
            'id', 'action', 'detail', 'actor_id', 'created_at',
        ))


class AttendancePolicyListView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request):
        policies = AttendancePolicy.objects.filter(
            organization=TenantContext.get(),
        ).select_related('church', 'updated_by')
        if not is_attendance_organization_admin(request.user, TenantContext.get()):
            from apps.events.permissions import managed_church_ids

            policies = policies.filter(church_id__in=managed_church_ids(request.user, TenantContext.get()))
        return Response(AttendancePolicySerializer(policies, many=True).data)


class AttendancePolicyDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceOrganizationAdmin]

    def put(self, request, church_id):
        from apps.core.models import Church

        church = get_object_or_404(
            Church,
            church_id=church_id,
            organization=TenantContext.get(),
        )
        serializer = AttendancePolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        policy, _ = AttendancePolicy.objects.update_or_create(
            organization=TenantContext.get(),
            church=church,
            defaults={
                **serializer.validated_data,
                'updated_by': request.user,
                'last_evaluated_at': None,
            },
        )
        record_audit(
            TenantContext.get(),
            'ATTENDANCE_POLICY_UPDATED',
            request.user,
            detail={'church_id': str(church.church_id)},
        )
        return Response(AttendancePolicySerializer(policy).data)


class AttendanceMemberHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, member_id):
        member = get_object_or_404(
            Member.objects.select_related('organization'),
            pk=member_id,
            organization=TenantContext.get(),
        )
        if member.user_id != request.user.id and not _can_manage(request.user, member.organization, member.church_id):
            return Response({'detail': 'Accès refusé.'}, status=status.HTTP_403_FORBIDDEN)
        history = ExpectedParticipant.objects.filter(
            member=member,
            session__organization=member.organization,
        ).select_related('session').prefetch_related('attendance__excuses')
        page = AttendancePagination()
        results = page.paginate_queryset(history, request, view=self)
        return page.get_paginated_response(MemberAttendanceHistorySerializer(results, many=True).data)


class AttendanceManagerContextView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request):
        organization = TenantContext.get()
        if organization is None:
            raise ValidationError('Aucune organisation active.')
        from apps.events.permissions import managed_church_ids

        is_admin = is_attendance_organization_admin(request.user, organization)
        member_church_id = Member.objects.filter(
            user=request.user,
            organization=organization,
            status=Member.Status.ACTIVE,
        ).values_list('church_id', flat=True).first()
        church_ids = list(
            organization.churches.filter(is_active=True).values_list('church_id', flat=True)
        ) if is_admin or has_configuration_permission(request.user, 'attendance.read', organization) else (
            sorted(
                set(managed_church_ids(request.user, organization))
                | configuration_church_ids(request.user, organization, 'attendance'),
                key=str,
            )
        )
        if not is_admin and not church_ids and member_church_id:
            church_ids = [member_church_id]
        default_church_id = (
            member_church_id if member_church_id in church_ids
            else church_ids[0] if len(church_ids) == 1
            else None
        )
        church_selection_fixed = bool(
            default_church_id
            and (member_church_id == default_church_id or len(church_ids) == 1)
        )
        churches = list(organization.churches.filter(
            church_id__in=church_ids,
            is_active=True,
        ).order_by('name').values('church_id', 'name'))
        campuses = Campus.objects.filter(
            church_id__in=church_ids,
            is_active=True,
        ).order_by('name').values('campus_id', 'name', 'church_id')
        return Response({
            'is_organization_admin': is_admin,
            'managed_church_ids': [str(church_id) for church_id in church_ids],
            'default_church_id': str(default_church_id) if default_church_id else None,
            'church_selection_fixed': church_selection_fixed,
            'churches': [{
                'church_id': str(church['church_id']),
                'name': church['name'],
            } for church in churches],
            'campuses': [{
                'campus_id': str(campus['campus_id']),
                'name': campus['name'],
                'church': str(campus['church_id']),
            } for campus in campuses],
        })


class AttendanceMemberListView(APIView):
    permission_classes = [IsAuthenticated, IsAttendanceManager]

    def get(self, request):
        members = Member.objects.filter(
            organization=TenantContext.get(),
            status=Member.Status.ACTIVE,
        ).select_related('church', 'campus').order_by('last_name', 'first_name')
        if not is_attendance_organization_admin(request.user, TenantContext.get()):
            from apps.events.permissions import managed_church_ids

            church_ids = set(managed_church_ids(request.user, TenantContext.get())) | configuration_church_ids(
                request.user,
                TenantContext.get(),
                'attendance',
            )
            members = members.filter(church_id__in=church_ids)
        church_id = request.query_params.get('church_id')
        campus_id = request.query_params.get('campus_id')
        if church_id:
            members = members.filter(church_id=church_id)
        if campus_id:
            members = members.filter(campus_id=campus_id)
        return Response([{
            'id': str(member.id),
            'first_name': member.first_name,
            'last_name': member.last_name,
            'church_id': str(member.church_id),
            'campus_id': str(member.campus_id) if member.campus_id else None,
        } for member in members])

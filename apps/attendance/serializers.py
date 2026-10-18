from rest_framework import serializers

from .models import (
    AbsenceExcuse,
    AttendanceAlert,
    AttendanceAlertNote,
    AttendancePolicy,
    AttendanceCorrection,
    AttendanceRecord,
    AttendanceSession,
    AttendanceSessionType,
    AttendanceStatus,
    ExpectedParticipant,
    PopulationRule,
    RecurrenceFrequency,
    SessionMethod,
)


class AttendanceSessionTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceSessionType
        fields = [
            'id', 'name', 'population_rule', 'default_methods', 'tolerance_minutes',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_default_methods(self, value):
        if not isinstance(value, list) or not value or any(method not in SessionMethod.values for method in value):
            raise serializers.ValidationError('Sélectionnez au moins une méthode de pointage valide.')
        return list(dict.fromkeys(value))


class AttendanceSessionCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    church_id = serializers.UUIDField()
    campus_id = serializers.UUIDField(required=False, allow_null=True)
    session_type_id = serializers.UUIDField(required=False, allow_null=True)
    population_rule = serializers.ChoiceField(choices=PopulationRule.choices, required=False)
    ministry_id = serializers.UUIDField(required=False, allow_null=True)
    tribe_id = serializers.UUIDField(required=False, allow_null=True)
    member_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    event_occurrence_id = serializers.UUIDField(required=False, allow_null=True)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    allowed_methods = serializers.ListField(child=serializers.ChoiceField(choices=SessionMethod.choices), required=False)
    tolerance_minutes = serializers.IntegerField(required=False, min_value=0, max_value=240)
    recurrence_frequency = serializers.ChoiceField(choices=RecurrenceFrequency.choices, required=False)
    recurrence_until = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs['ends_at'] <= attrs['starts_at']:
            raise serializers.ValidationError({'ends_at': 'La fin doit suivre le début de la séance.'})
        if len(set(attrs.get('member_ids', []))) != len(attrs.get('member_ids', [])):
            raise serializers.ValidationError({'member_ids': 'Un membre ne peut apparaître qu’une seule fois.'})
        return attrs


class AttendanceSessionSerializer(serializers.ModelSerializer):
    church_name = serializers.CharField(source='church.name', read_only=True)
    campus_name = serializers.CharField(source='campus.name', read_only=True, default=None)
    session_type_name = serializers.CharField(source='session_type.name', read_only=True, default=None)
    expected_count = serializers.SerializerMethodField()
    present_count = serializers.SerializerMethodField()
    recurrence_count = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceSession
        fields = [
            'id', 'title', 'church', 'church_name', 'campus', 'campus_name', 'session_type',
            'session_type_name', 'ministry', 'tribe', 'event_occurrence', 'starts_at', 'ends_at',
            'status', 'allowed_methods', 'tolerance_minutes', 'recurrence_frequency',
            'recurrence_series_id', 'recurrence_until', 'opened_at', 'closed_at',
            'expected_count', 'present_count', 'recurrence_count', 'created_at', 'updated_at',
        ]

    def get_expected_count(self, obj):
        if hasattr(obj, '_expected_count'):
            return obj._expected_count
        return obj.expected_participants.filter(expected_for_absence=True).count()

    def get_present_count(self, obj):
        if hasattr(obj, '_present_count'):
            return obj._present_count
        return AttendanceRecord.objects.filter(
            expected_participant__session=obj,
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.EARLY_DEPARTURE],
        ).count()

    def get_recurrence_count(self, obj):
        if hasattr(obj, '_recurrence_count'):
            return obj._recurrence_count or 1
        if not obj.recurrence_series_id:
            return 1
        return AttendanceSession.objects.filter(recurrence_series_id=obj.recurrence_series_id).count()


class ExpectedParticipantSerializer(serializers.ModelSerializer):
    member_id = serializers.UUIDField(read_only=True)
    visitor_id = serializers.UUIDField(read_only=True)
    attendance = serializers.SerializerMethodField()
    is_visitor = serializers.SerializerMethodField()

    class Meta:
        model = ExpectedParticipant
        fields = [
            'id', 'member_id', 'visitor_id', 'first_name', 'last_name', 'is_visitor',
            'attendance', 'addition_reason', 'expected_for_absence', 'created_at',
        ]

    def get_is_visitor(self, obj):
        return obj.visitor_id is not None

    def get_attendance(self, obj):
        try:
            record = obj.attendance
        except AttendanceRecord.DoesNotExist:
            return None
        return AttendanceRecordSerializer(record).data


class AttendanceRecordSerializer(serializers.ModelSerializer):
    excuses = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceRecord
        fields = [
            'id', 'status', 'checked_in_at', 'method', 'recorded_by',
            'departed_at', 'departure_reason', 'excuses', 'updated_at',
        ]

    def get_excuses(self, obj):
        return AbsenceExcuseSerializer(obj.excuses.all(), many=True).data


class AttendanceCheckInSerializer(serializers.Serializer):
    expected_participant_id = serializers.UUIDField()
    method = serializers.ChoiceField(choices=SessionMethod.choices)
    qr_token = serializers.CharField(required=False, allow_blank=True)


class VisitorCreateSerializer(serializers.Serializer):
    visitor_id = serializers.UUIDField(required=False)
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    reusable = serializers.BooleanField(default=False)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate(self, attrs):
        if 'visitor_id' not in attrs and not (attrs.get('first_name') and attrs.get('last_name')):
            raise serializers.ValidationError('Un prénom et un nom sont requis pour créer une fiche visiteur.')
        return attrs


class AttendanceCorrectionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=AttendanceStatus.choices)
    reason = serializers.CharField(max_length=500, allow_blank=False)


class ExcuseCreateSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, allow_blank=False)


class EarlyDepartureSerializer(serializers.Serializer):
    departed_at = serializers.DateTimeField()
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class AttendanceCorrectionHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceCorrection
        fields = ['id', 'previous_status', 'new_status', 'reason', 'actor', 'created_at']


class AbsenceExcuseSerializer(serializers.ModelSerializer):
    class Meta:
        model = AbsenceExcuse
        fields = ['id', 'status', 'reason', 'submitted_by', 'decided_by', 'submitted_at', 'decided_at']


class AttendanceAlertSerializer(serializers.ModelSerializer):
    member_name = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceAlert
        fields = ['id', 'member', 'member_name', 'rule_key', 'status', 'trigger_detail', 'created_at', 'resolved_at']

    def get_member_name(self, obj):
        return str(obj.member)


class AttendanceAlertNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceAlertNote
        fields = ['id', 'actor', 'action', 'note', 'created_at']


class AttendancePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendancePolicy
        fields = [
            'id', 'church', 'consecutive_absences', 'minimum_attendance_rate',
            'observation_weeks', 'evaluation_frequency', 'count_excused_absences',
            'last_evaluated_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church', 'last_evaluated_at', 'updated_at']

    def validate_minimum_attendance_rate(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError('Le taux doit être compris entre 0 et 100.')
        return value


class MemberAttendanceHistorySerializer(serializers.ModelSerializer):
    session_id = serializers.UUIDField(source='session.id', read_only=True)
    session_title = serializers.CharField(source='session.title', read_only=True)
    starts_at = serializers.DateTimeField(source='session.starts_at', read_only=True)
    session_status = serializers.CharField(source='session.status', read_only=True)
    attendance = serializers.SerializerMethodField()

    class Meta:
        model = ExpectedParticipant
        fields = ['session_id', 'session_title', 'starts_at', 'session_status', 'expected_for_absence', 'attendance']

    def get_attendance(self, obj):
        try:
            return AttendanceRecordSerializer(obj.attendance).data
        except AttendanceRecord.DoesNotExist:
            return None

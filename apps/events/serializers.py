from datetime import datetime, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from rest_framework import serializers

from apps.core.models import Church
from apps.events.models import (
    Event,
    EventAttendance,
    EventAttendanceCorrection,
    EventAuditLog,
    EventOccurrence,
    EventRegistration,
    EventScope,
    EventStatus,
    RecurrenceFrequency,
    RegistrationMode,
)
from apps.events.services import validate_location
from apps.members.models import Member
from infrastructure.tenancy import TenantContext


def parse_event_datetime(value, zone_name):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        value_dt = value
    else:
        try:
            value_dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError as error:
            raise serializers.ValidationError('Date et heure invalides; utilisez ISO 8601.') from error
    if value_dt.tzinfo is not None:
        return value_dt
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as error:
        raise serializers.ValidationError({'timezone': 'Fuseau horaire IANA inconnu.'}) from error
    aware = value_dt.replace(tzinfo=zone)
    if aware.astimezone(datetime_timezone.utc).astimezone(zone).replace(tzinfo=None) != value_dt:
        raise serializers.ValidationError('Cette heure locale n’existe pas dans le fuseau horaire sélectionné.')
    return aware


class EventOccurrenceSerializer(serializers.ModelSerializer):
    registration_count = serializers.SerializerMethodField()
    waitlist_count = serializers.SerializerMethodField()
    offered_count = serializers.SerializerMethodField()
    attendance_count = serializers.SerializerMethodField()

    class Meta:
        model = EventOccurrence
        fields = [
            'id', 'starts_at', 'ends_at', 'status', 'location', 'registration_mode',
            'capacity', 'waitlist_enabled', 'checkin_mode', 'checkin_opens_at',
            'checkin_closes_at', 'registration_count', 'waitlist_count', 'offered_count', 'attendance_count',
        ]

    def get_registration_count(self, obj):
        return obj.registrations.filter(status='CONFIRMED').count()

    def get_waitlist_count(self, obj):
        return obj.registrations.filter(status='WAITLISTED').count()

    def get_offered_count(self, obj):
        return obj.registrations.filter(status='OFFERED', offer_expires_at__gt=timezone.now()).count()

    def get_attendance_count(self, obj):
        return obj.attendances.filter(state='PRESENT').count()


class EventSerializer(serializers.ModelSerializer):
    church_name = serializers.CharField(source='church.name', read_only=True, allow_null=True)
    campus_name = serializers.CharField(source='campus.name', read_only=True, allow_null=True)
    included_churches = serializers.SerializerMethodField()
    occurrences = EventOccurrenceSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            'id', 'organization', 'scope', 'church', 'church_name', 'campus', 'campus_name',
            'included_churches', 'title', 'description', 'category', 'location', 'timezone',
            'status', 'recurrence_frequency', 'recurrence_interval', 'recurrence_until',
            'occurrences', 'created_by', 'updated_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_included_churches(self, event):
        return [{'church_id': str(church.church_id), 'name': church.name} for church in event.included_churches.all()]


class EventWriteSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=EventScope.choices)
    church_id = serializers.UUIDField(required=False, allow_null=True)
    campus_id = serializers.UUIDField(required=False, allow_null=True)
    included_church_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True, max_length=80)
    location = serializers.CharField(required=False, allow_blank=True, max_length=255)
    timezone = serializers.CharField(required=False, default='UTC', max_length=64)
    recurrence_frequency = serializers.ChoiceField(
        choices=RecurrenceFrequency.choices,
        required=False,
        default=RecurrenceFrequency.NONE,
    )
    recurrence_interval = serializers.IntegerField(required=False, min_value=1, max_value=52, default=1)
    recurrence_until = serializers.DateField(required=False, allow_null=True)
    starts_at = serializers.CharField()
    ends_at = serializers.CharField()
    occurrence_location = serializers.CharField(required=False, allow_blank=True, max_length=255)
    registration_mode = serializers.ChoiceField(
        choices=RegistrationMode.choices,
        required=False,
        default=RegistrationMode.DISABLED,
    )
    capacity = serializers.IntegerField(required=False, min_value=1, allow_null=True)
    waitlist_enabled = serializers.BooleanField(required=False, default=False)
    checkin_mode = serializers.ChoiceField(choices=['DISABLED', 'REGISTRATION', 'SELF'], required=False, default='DISABLED')
    checkin_opens_at = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    checkin_closes_at = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate_timezone(self, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise serializers.ValidationError('Fuseau horaire IANA inconnu.') from error
        return value

    def validate(self, attrs):
        zone_name = attrs.get('timezone', 'UTC')
        for field in ('starts_at', 'ends_at', 'checkin_opens_at', 'checkin_closes_at'):
            if field in attrs:
                attrs[field] = parse_event_datetime(attrs[field], zone_name)
        if attrs['ends_at'] <= attrs['starts_at']:
            raise serializers.ValidationError({'ends_at': 'La fin doit être postérieure au début.'})
        frequency = attrs['recurrence_frequency']
        if frequency != RecurrenceFrequency.NONE and not attrs.get('recurrence_until'):
            raise serializers.ValidationError({'recurrence_until': 'Une date de fin est requise pour une récurrence.'})
        if attrs.get('recurrence_until') and attrs['recurrence_until'] < attrs['starts_at'].date():
            raise serializers.ValidationError({'recurrence_until': 'La fin de récurrence doit suivre la première séance.'})
        if frequency == RecurrenceFrequency.NONE and attrs.get('recurrence_until'):
            raise serializers.ValidationError({'recurrence_until': 'Cette date ne s’applique qu’aux événements récurrents.'})
        if attrs.get('checkin_opens_at') and attrs.get('checkin_closes_at') and attrs['checkin_closes_at'] < attrs['checkin_opens_at']:
            raise serializers.ValidationError({'checkin_closes_at': 'La fermeture du pointage doit suivre son ouverture.'})
        if attrs['checkin_mode'] == 'REGISTRATION' and attrs['registration_mode'] == RegistrationMode.DISABLED:
            raise serializers.ValidationError({'checkin_mode': 'Le pointage par inscription nécessite des inscriptions activées.'})
        organization = TenantContext.get()
        church, campus = validate_location(
            organization,
            attrs['scope'],
            attrs.get('church_id'),
            attrs.get('campus_id'),
        )
        attrs['church'] = church
        attrs['campus'] = campus
        church_ids = attrs.get('included_church_ids', [])
        if attrs['scope'] != EventScope.ORGANIZATION and church_ids:
            raise serializers.ValidationError({'included_church_ids': 'Seuls les événements d’organisation peuvent cibler plusieurs églises.'})
        churches = list(Church.objects.filter(
            church_id__in=church_ids,
            organization=organization,
            is_active=True,
        ))
        if len(churches) != len(set(church_ids)):
            raise serializers.ValidationError({'included_church_ids': 'Une église est invalide ou hors de votre organisation.'})
        attrs['included_churches_objects'] = churches
        return attrs


class EventPatchSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True, max_length=80)
    location = serializers.CharField(required=False, allow_blank=True, max_length=255)
    timezone = serializers.CharField(required=False, max_length=64)
    recurrence_frequency = serializers.ChoiceField(choices=RecurrenceFrequency.choices, required=False)
    recurrence_interval = serializers.IntegerField(required=False, min_value=1, max_value=52)
    recurrence_until = serializers.DateField(required=False, allow_null=True)
    included_church_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True)

    def validate_timezone(self, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise serializers.ValidationError('Fuseau horaire IANA inconnu.') from error
        return value

    def validate_included_church_ids(self, ids):
        event = self.context['event']
        if event.scope != EventScope.ORGANIZATION:
            raise serializers.ValidationError('Seuls les événements d’organisation peuvent cibler plusieurs églises.')
        churches = list(Church.objects.filter(church_id__in=ids, organization=event.organization, is_active=True))
        if len(churches) != len(set(ids)):
            raise serializers.ValidationError('Une église est invalide ou hors de cette organisation.')
        return churches


class EventSeriesUpdateSerializer(serializers.Serializer):
    confirm_future = serializers.BooleanField()
    event = serializers.DictField(required=False)
    occurrence = serializers.DictField(required=False)

    def validate(self, attrs):
        if not attrs['confirm_future']:
            raise serializers.ValidationError({'confirm_future': 'Confirmez explicitement la modification des occurrences futures.'})
        if not attrs.get('event') and not attrs.get('occurrence'):
            raise serializers.ValidationError('Fournissez des changements pour la série ou ses occurrences.')
        target = self.context['event'].occurrences.filter(
            starts_at__gte=timezone.now(),
            status__in=[EventStatus.DRAFT, EventStatus.PUBLISHED],
        ).order_by('starts_at').first()
        if attrs.get('event'):
            event_serializer = EventPatchSerializer(
                data=attrs['event'],
                partial=True,
                context={'event': self.context['event']},
            )
            event_serializer.is_valid(raise_exception=True)
            attrs['event_changes'] = event_serializer.validated_data
        if attrs.get('occurrence'):
            if target is None:
                raise serializers.ValidationError({'occurrence': 'Aucune occurrence future modifiable.'})
            occurrence_serializer = EventOccurrenceWriteSerializer(
                target,
                data=attrs['occurrence'],
                partial=True,
                context={'timezone': self.context['event'].timezone},
            )
            occurrence_serializer.is_valid(raise_exception=True)
            attrs['occurrence_changes'] = occurrence_serializer.validated_data
            attrs['target_occurrence'] = target
        return attrs


class EventOccurrenceWriteSerializer(serializers.ModelSerializer):
    starts_at = serializers.CharField()
    ends_at = serializers.CharField()
    checkin_opens_at = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    checkin_closes_at = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = EventOccurrence
        fields = [
            'starts_at', 'ends_at', 'location', 'registration_mode', 'capacity',
            'waitlist_enabled', 'checkin_mode', 'checkin_opens_at', 'checkin_closes_at',
        ]

    def validate(self, attrs):
        zone_name = self.context.get('timezone') or (self.instance.event.timezone if self.instance else 'UTC')
        for field in ('starts_at', 'ends_at', 'checkin_opens_at', 'checkin_closes_at'):
            if field in attrs:
                attrs[field] = parse_event_datetime(attrs[field], zone_name)
        starts_at = attrs.get('starts_at', self.instance.starts_at if self.instance else None)
        ends_at = attrs.get('ends_at', self.instance.ends_at if self.instance else None)
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({'ends_at': 'La fin doit être postérieure au début.'})
        opens_at = attrs.get('checkin_opens_at', self.instance.checkin_opens_at if self.instance else None)
        closes_at = attrs.get('checkin_closes_at', self.instance.checkin_closes_at if self.instance else None)
        if opens_at and closes_at and closes_at < opens_at:
            raise serializers.ValidationError({'checkin_closes_at': 'La fermeture du pointage doit suivre son ouverture.'})
        checkin_mode = attrs.get('checkin_mode', self.instance.checkin_mode if self.instance else None)
        registration_mode = attrs.get('registration_mode', self.instance.registration_mode if self.instance else None)
        if checkin_mode == 'REGISTRATION' and registration_mode == RegistrationMode.DISABLED:
            raise serializers.ValidationError({'checkin_mode': 'Le pointage par inscription nécessite des inscriptions activées.'})
        if 'capacity' in attrs and attrs['capacity'] is None and self.instance and self.instance.capacity is not None and self.instance.registrations.filter(status__in=['CONFIRMED', 'OFFERED']).count() > 0:
            raise serializers.ValidationError({'capacity': 'Une capacité ne peut pas être retirée après les premières inscriptions.'})
        if 'capacity' in attrs and attrs['capacity'] is not None and self.instance:
            reserved = self.instance.registrations.filter(status__in=['CONFIRMED', 'OFFERED']).count()
            if attrs['capacity'] < reserved:
                raise serializers.ValidationError({'capacity': 'La capacité ne peut pas être inférieure aux places déjà réservées.'})
        return attrs


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=EventStatus.choices)
    confirm = serializers.BooleanField(required=False, default=False)


class PublicRegistrationSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context['request']
        user = request.user
        if user.is_authenticated:
            member = Member.objects.filter(user=user, organization=self.context['event'].organization).first()
            if member is not None:
                attrs['member'] = member
                return attrs
        if not attrs.get('first_name') or not attrs.get('last_name'):
            raise serializers.ValidationError('Le prénom et le nom sont requis pour une inscription visiteur.')
        if not attrs.get('email') and not attrs.get('phone'):
            raise serializers.ValidationError('Un téléphone ou une adresse électronique est requis.')
        return attrs


class RegistrationSerializer(serializers.ModelSerializer):
    attendee_name = serializers.SerializerMethodField()
    attendee_email = serializers.SerializerMethodField()
    attendee_phone = serializers.SerializerMethodField()

    class Meta:
        model = EventRegistration
        fields = [
            'id', 'occurrence', 'member', 'person', 'attendee_name', 'attendee_email',
            'attendee_phone', 'status', 'offer_expires_at', 'created_at', 'cancelled_at',
        ]
        read_only_fields = fields

    def get_attendee_name(self, registration):
        attendee = registration.member or registration.person
        return str(attendee)

    def get_attendee_email(self, registration):
        attendee = registration.member or registration.person
        return attendee.email

    def get_attendee_phone(self, registration):
        attendee = registration.member or registration.person
        return attendee.phone


class ScanSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200, trim_whitespace=True)
    expected_occurrence_id = serializers.UUIDField()


class CheckInSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)
    exception_reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class SelfCheckInSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)


class AttendanceCorrectionSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=['PRESENT', 'REVOKED'])
    reason = serializers.CharField(max_length=500, allow_blank=False)


class AttendanceSerializer(serializers.ModelSerializer):
    attendee_name = serializers.SerializerMethodField()
    attendee_phone = serializers.SerializerMethodField()

    class Meta:
        model = EventAttendance
        fields = ['id', 'occurrence', 'registration', 'member', 'attendee_name', 'attendee_phone', 'state', 'checked_in_at', 'checked_in_by', 'source']
        read_only_fields = fields

    def get_attendee_name(self, attendance):
        if attendance.registration_id:
            attendee = attendance.registration.member or attendance.registration.person
        else:
            attendee = attendance.member
        return str(attendee)

    def get_attendee_phone(self, attendance):
        if attendance.registration_id:
            attendee = attendance.registration.member or attendance.registration.person
        else:
            attendee = attendance.member
        return attendee.phone


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventAuditLog
        fields = ['id', 'occurrence', 'actor', 'action', 'detail', 'created_at']
        read_only_fields = fields


class AttendanceCorrectionHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = EventAttendanceCorrection
        fields = ['id', 'attendance', 'previous_state', 'corrected_state', 'reason', 'actor', 'created_at']
        read_only_fields = fields

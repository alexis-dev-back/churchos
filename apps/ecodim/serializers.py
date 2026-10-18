from django.utils import timezone
from rest_framework import serializers

from apps.ecodim.models import (
    EcodimChild,
    EcodimChildGuardian,
    EcodimChildHandoff,
    EcodimClass,
    EcodimPickupAuthorization,
    EcodimSession,
    EcodimStaffAssignment,
)
from apps.ecodim.validators import validate_identity_link, validate_person_family
from apps.members.models import Member


class EcodimChildSerializer(serializers.ModelSerializer):
    age = serializers.SerializerMethodField()
    family_name = serializers.CharField(source='family.name', read_only=True, default=None)
    current_class_id = serializers.SerializerMethodField()
    current_class_name = serializers.SerializerMethodField()
    primary_guardian_name = serializers.SerializerMethodField()

    class Meta:
        model = EcodimChild
        fields = [
            'id', 'reference', 'first_name', 'last_name', 'date_of_birth', 'age',
            'gender', 'photo_url', 'status', 'family', 'family_name', 'member',
            'person', 'current_class_id', 'current_class_name', 'primary_guardian_name', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'reference', 'created_at', 'updated_at', 'age', 'family_name',
            'current_class_id', 'current_class_name', 'primary_guardian_name',
        ]

    def get_age(self, child):
        if not child.date_of_birth:
            return None
        today = timezone.localdate()
        return today.year - child.date_of_birth.year - (
            (today.month, today.day) < (child.date_of_birth.month, child.date_of_birth.day)
        )

    def get_current_class_name(self, child):
        enrollment = child.enrollments.filter(ended_at__isnull=True).select_related('ecodim_class').first()
        return enrollment.ecodim_class.name if enrollment else None

    def get_current_class_id(self, child):
        enrollment = child.enrollments.filter(ended_at__isnull=True).values_list('ecodim_class_id', flat=True).first()
        return str(enrollment) if enrollment else None

    def get_primary_guardian_name(self, child):
        guardian = child.guardians.filter(is_primary_contact=True).first()
        return guardian.name if guardian else None

    def validate_date_of_birth(self, value):
        if value and value > timezone.localdate():
            raise serializers.ValidationError('La date de naissance ne peut pas être dans le futur.')
        return value

    def validate(self, attrs):
        organization = self.context['organization']
        church = self.context['church']
        if self.instance is None and attrs.get('status', EcodimChild.Status.ACTIVE) != EcodimChild.Status.ACTIVE:
            raise serializers.ValidationError({'status': 'Un nouveau dossier doit être actif.'})
        family = attrs.get('family', getattr(self.instance, 'family', None))
        member = attrs.get('member', getattr(self.instance, 'member', None))
        person = attrs.get('person', getattr(self.instance, 'person', None))

        if member and person:
            raise serializers.ValidationError('L’enfant ne peut être lié qu’à un seul profil existant.')
        validate_identity_link(
            organization=organization,
            church=church,
            family=family,
            member=member,
            person=person,
        )
        return attrs


class EcodimClassSerializer(serializers.ModelSerializer):
    active_children_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = EcodimClass
        fields = [
            'id', 'name', 'description', 'level', 'minimum_age', 'maximum_age',
            'capacity', 'status', 'active_children_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'active_children_count']

    def validate(self, attrs):
        church = self.context.get('church') or getattr(self.instance, 'church', None)
        name = attrs.get('name', getattr(self.instance, 'name', None))
        if church and name and EcodimClass.objects.filter(church=church, name=name).exclude(
            pk=getattr(self.instance, 'pk', None),
        ).exists():
            raise serializers.ValidationError({
                'name': 'Une classe portant ce nom existe déjà dans cette église.',
            })
        minimum_age = attrs.get('minimum_age', getattr(self.instance, 'minimum_age', None))
        maximum_age = attrs.get('maximum_age', getattr(self.instance, 'maximum_age', None))
        if minimum_age is not None and maximum_age is not None and maximum_age < minimum_age:
            raise serializers.ValidationError({'maximum_age': 'L’âge maximum doit être supérieur ou égal à l’âge minimum.'})
        if self.instance is None and attrs.get('status', EcodimClass.Status.ACTIVE) != EcodimClass.Status.ACTIVE:
            raise serializers.ValidationError({'status': 'Une nouvelle classe doit être active.'})
        return attrs


class EcodimChildEnrollmentSerializer(serializers.Serializer):
    class_id = serializers.UUIDField()


class EcodimDashboardSerializer(serializers.Serializer):
    summary = serializers.DictField()
    age_distribution = serializers.ListField(child=serializers.DictField())
    registrations_by_month = serializers.ListField(child=serializers.DictField())
    classes = EcodimClassSerializer(many=True)
    upcoming_sessions = serializers.ListField(child=serializers.DictField())
    recent_children = EcodimChildSerializer(many=True)


class EcodimChildGuardianSerializer(serializers.ModelSerializer):
    class Meta:
        model = EcodimChildGuardian
        fields = [
            'id', 'name', 'phone', 'relationship', 'is_legal_guardian',
            'is_primary_contact', 'member', 'person', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        child = self.context['child']
        member = attrs.get('member')
        person = attrs.get('person')
        if member and person:
            raise serializers.ValidationError('Le responsable ne peut être lié qu’à un seul profil existant.')
        if member:
            validate_identity_link(
                organization=child.organization,
                church=child.church,
                family=child.family,
                member=member,
            )
        if person:
            validate_person_family(family=child.family, person=person)
        return attrs


class EcodimPickupAuthorizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EcodimPickupAuthorization
        fields = [
            'id', 'name', 'phone', 'relationship', 'valid_from', 'valid_until',
            'status', 'member', 'person', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        child = self.context['child']
        member = attrs.get('member')
        person = attrs.get('person')
        if member and person:
            raise serializers.ValidationError('Le responsable ne peut être lié qu’à un seul profil existant.')
        if member:
            validate_identity_link(
                organization=child.organization,
                church=child.church,
                family=child.family,
                member=member,
            )
        if person:
            validate_person_family(family=child.family, person=person)
        valid_from = attrs.get('valid_from', timezone.localdate())
        valid_until = attrs.get('valid_until')
        if valid_until and valid_until < valid_from:
            raise serializers.ValidationError({'valid_until': 'La date de fin doit suivre la date de début.'})
        if self.instance is None and attrs.get(
            'status',
            EcodimPickupAuthorization.Status.ACTIVE,
        ) != EcodimPickupAuthorization.Status.ACTIVE:
            raise serializers.ValidationError({'status': 'Une nouvelle autorisation doit être active.'})
        if (
            self.instance
            and self.instance.status == EcodimPickupAuthorization.Status.REVOKED
            and attrs.get('status', self.instance.status) != EcodimPickupAuthorization.Status.REVOKED
        ):
            raise serializers.ValidationError({'status': 'Une autorisation révoquée ne peut pas être réactivée.'})
        return attrs


class EcodimChildHandoffCreateSerializer(serializers.Serializer):
    authorization_id = serializers.UUIDField()
    verification_note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class EcodimChildHandoffSerializer(serializers.ModelSerializer):
    authorization_name = serializers.CharField(source='authorization.name', read_only=True)

    class Meta:
        model = EcodimChildHandoff
        fields = [
            'id', 'picked_up_by_name', 'handed_off_at', 'verification_note',
            'authorization', 'authorization_name', 'confirmed_by',
        ]
        read_only_fields = fields


class EcodimSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EcodimSession
        fields = [
            'id', 'title', 'starts_at', 'ends_at', 'location', 'status',
            'ecodim_class', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        organization = self.context['organization']
        church = self.context['church']
        ecodim_class = attrs.get('ecodim_class', getattr(self.instance, 'ecodim_class', None))
        if ecodim_class and (
            ecodim_class.organization_id != organization.tenant_id
            or ecodim_class.church_id != church.church_id
        ):
            raise serializers.ValidationError({'ecodim_class': 'La classe doit appartenir à cette église.'})
        starts_at = attrs.get('starts_at', getattr(self.instance, 'starts_at', None))
        ends_at = attrs.get('ends_at', getattr(self.instance, 'ends_at', None))
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({'ends_at': 'La séance doit se terminer après son début.'})
        return attrs

    def validate_status(self, value):
        if self.instance is None and value != EcodimSession.Status.PLANNED:
            raise serializers.ValidationError('Une nouvelle séance doit être planifiée.')
        return value


class EcodimChildDetailSerializer(EcodimChildSerializer):
    guardians = EcodimChildGuardianSerializer(many=True, read_only=True)
    pickup_authorizations = EcodimPickupAuthorizationSerializer(many=True, read_only=True)
    handoffs = EcodimChildHandoffSerializer(many=True, read_only=True)

    class Meta(EcodimChildSerializer.Meta):
        fields = EcodimChildSerializer.Meta.fields + ['guardians', 'pickup_authorizations', 'handoffs']


class EcodimLinkableMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = Member
        fields = ['id', 'first_name', 'last_name']
        read_only_fields = fields


class EcodimStaffAssignmentSerializer(serializers.ModelSerializer):
    member_name = serializers.SerializerMethodField()

    class Meta:
        model = EcodimStaffAssignment
        fields = ['id', 'member', 'member_name', 'role', 'is_active', 'created_at']
        read_only_fields = ['id', 'member_name', 'created_at']

    def get_member_name(self, assignment):
        return f'{assignment.member.first_name} {assignment.member.last_name}'.strip()

    def validate(self, attrs):
        organization = self.context['organization']
        church = self.context['church']
        member = attrs.get('member', getattr(self.instance, 'member', None))
        if member and (member.organization_id != organization.tenant_id or member.church_id != church.church_id):
            raise serializers.ValidationError({'member': 'Ce membre ne fait pas partie de cette église.'})
        if member and (member.status != Member.Status.ACTIVE or not member.user.is_active):
            raise serializers.ValidationError({'member': 'Seul un membre actif peut être affecté à l’équipe ECODIM.'})
        return attrs
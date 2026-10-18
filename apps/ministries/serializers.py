from rest_framework import serializers

from apps.core.models import Church
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
from infrastructure.tenancy import TenantContext


class StructureSerializer(serializers.ModelSerializer):
    church_id = serializers.UUIDField(write_only=True, required=False)
    church_name = serializers.CharField(source='church.name', read_only=True)
    active_members_count = serializers.IntegerField(read_only=True, default=0)
    leader_name = serializers.CharField(read_only=True, allow_blank=True, default='')

    class Meta:
        fields = [
            'id', 'church', 'church_id', 'church_name', 'name', 'sector', 'description', 'status',
            'active_members_count', 'leader_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church', 'church_name', 'created_at', 'updated_at']

    def validate_church_id(self, value):
        organization = TenantContext.get()
        try:
            return Church.objects.get(church_id=value, organization=organization, is_active=True)
        except Church.DoesNotExist as exc:
            raise serializers.ValidationError('L’église est introuvable ou ne fait pas partie de votre organisation.') from exc

    def validate(self, attrs):
        if self.instance is None and 'church_id' not in attrs:
            raise serializers.ValidationError({'church_id': 'Ce champ est obligatoire.'})
        if self.instance is not None and 'church_id' in attrs:
            raise serializers.ValidationError({'church_id': 'L’église de rattachement ne peut pas être modifiée.'})
        name = attrs.get('name')
        if name is not None:
            church = attrs.get('church_id') or (self.instance.church if self.instance else None)
            queryset = self.Meta.model.objects.filter(church=church, name__iexact=name)
            if self.instance is not None:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError({'name': 'Une structure de ce type porte déjà ce nom dans cette église.'})
        return attrs

    def create(self, validated_data):
        validated_data['church'] = validated_data.pop('church_id')
        validated_data.setdefault('status', StructureStatus.ACTIVE)
        return super().create(validated_data)


class MinistrySerializer(StructureSerializer):
    class Meta(StructureSerializer.Meta):
        model = Ministry


class TribeSerializer(StructureSerializer):
    class Meta(StructureSerializer.Meta):
        model = Tribe


class MemberReferenceField(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        return Member.objects.filter(organization=TenantContext.get())


class AssignmentSerializer(serializers.ModelSerializer):
    member_name = serializers.SerializerMethodField()
    structure_name = serializers.SerializerMethodField()

    class Meta:
        fields = [
            'id', 'member', 'member_name', 'structure_name', 'joined_at', 'left_at',
            'ended_reason', 'created_by', 'ended_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_member_name(self, assignment):
        return str(assignment.member)

    def get_structure_name(self, assignment):
        return str(getattr(assignment, self.context['structure_field']))


class MinistryAssignmentSerializer(AssignmentSerializer):
    class Meta(AssignmentSerializer.Meta):
        model = MinistryAssignment


class TribeAssignmentSerializer(AssignmentSerializer):
    class Meta(AssignmentSerializer.Meta):
        model = TribeAssignment


class AssignmentCreateSerializer(serializers.Serializer):
    member = MemberReferenceField(queryset=Member.objects.none())
    replace_active = serializers.BooleanField(required=False, default=False)

    def validate_member(self, member):
        structure = self.context['structure']
        if member.church_id != structure.church_id or member.organization_id != structure.church.organization_id:
            raise serializers.ValidationError('Le membre doit appartenir à la même église que la structure.')
        if member.status != Member.Status.ACTIVE:
            raise serializers.ValidationError('Seuls les membres actifs de l’église peuvent être affectés.')
        return member


class AssignmentCloseSerializer(serializers.Serializer):
    ended_reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class LeadershipSerializer(serializers.ModelSerializer):
    member_name = serializers.SerializerMethodField()
    structure_name = serializers.SerializerMethodField()

    class Meta:
        fields = [
            'id', 'member', 'member_name', 'structure_name', 'role', 'started_at',
            'ended_at', 'ended_reason', 'appointed_by', 'ended_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_member_name(self, leadership):
        return str(leadership.member)

    def get_structure_name(self, leadership):
        return str(getattr(leadership, self.context['structure_field']))


class MinistryLeadershipSerializer(LeadershipSerializer):
    class Meta(LeadershipSerializer.Meta):
        model = MinistryLeadership


class TribeLeadershipSerializer(LeadershipSerializer):
    class Meta(LeadershipSerializer.Meta):
        model = TribeLeadership


class LeadershipCreateSerializer(serializers.Serializer):
    member = MemberReferenceField(queryset=Member.objects.none())
    role = serializers.ChoiceField(choices=LeadershipRole.choices)

    def validate_member(self, member):
        structure = self.context['structure']
        if member.church_id != structure.church_id or member.organization_id != structure.church.organization_id:
            raise serializers.ValidationError('Le responsable doit appartenir à la même église que la structure.')
        if member.status != Member.Status.ACTIVE:
            raise serializers.ValidationError('Seuls les membres actifs de l’église peuvent être responsables.')
        if structure.status != StructureStatus.ACTIVE:
            raise serializers.ValidationError('Une structure inactive ou archivée ne peut pas recevoir de nouveau responsable.')
        return member


class LeadershipCloseSerializer(serializers.Serializer):
    ended_reason = serializers.CharField(required=False, allow_blank=True, max_length=255)

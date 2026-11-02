from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    PastoralCase,
    PastoralInteraction,
    PastoralNote,
    PastoralTask,
    PastoralWorkflow,
    PrayerRequest,
)

User = get_user_model()


class PastoralCaseCreateSerializer(serializers.Serializer):
    church_id = serializers.UUIDField()
    campus_id = serializers.UUIDField(required=False, allow_null=True)
    member_id = serializers.UUIDField(required=False, allow_null=True)
    person_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(max_length=180)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True)
    source = serializers.CharField(max_length=40, required=False, allow_blank=True)
    status = serializers.CharField(max_length=40, required=False, default='NEW')
    sensitivity = serializers.ChoiceField(choices=PastoralCase.Sensitivity.choices, required=False)
    priority = serializers.ChoiceField(choices=PastoralCase.Priority.choices, required=False)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    next_due_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        if bool(attrs.get('member_id')) == bool(attrs.get('person_id')):
            raise serializers.ValidationError('Sélectionnez exactement un membre ou une personne.')
        return attrs


class PastoralCaseUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=180, required=False)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True)
    sensitivity = serializers.ChoiceField(choices=PastoralCase.Sensitivity.choices, required=False)
    priority = serializers.ChoiceField(choices=PastoralCase.Priority.choices, required=False)
    next_due_at = serializers.DateTimeField(required=False, allow_null=True)


class PastoralPotentialDuplicateSerializer(serializers.Serializer):
    church_id = serializers.UUIDField()
    campus_id = serializers.UUIDField(required=False, allow_null=True)
    member_id = serializers.UUIDField(required=False)
    person_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if bool(attrs.get('member_id')) == bool(attrs.get('person_id')):
            raise serializers.ValidationError('Sélectionnez exactement un membre ou une personne.')
        return attrs


class PastoralCaseSerializer(serializers.ModelSerializer):
    church_name = serializers.CharField(source='church.name', read_only=True)
    campus_name = serializers.CharField(source='campus.name', read_only=True, default=None)
    member_name = serializers.SerializerMethodField()
    person_name = serializers.SerializerMethodField()
    assigned_to_name = serializers.CharField(source='assigned_to.username', read_only=True, default=None)

    class Meta:
        model = PastoralCase
        fields = [
            'id', 'church', 'church_name', 'campus', 'campus_name', 'member', 'member_name',
            'person', 'person_name', 'title', 'category', 'source', 'status', 'sensitivity',
            'priority', 'assigned_to', 'assigned_to_name', 'next_due_at', 'created_by',
            'created_at', 'updated_at', 'closed_at', 'archived_at',
        ]
        read_only_fields = fields

    def get_member_name(self, obj):
        return f'{obj.member.first_name} {obj.member.last_name}' if obj.member_id else None

    def get_person_name(self, obj):
        return f'{obj.person.first_name} {obj.person.last_name}' if obj.person_id else None


class PastoralInteractionCreateSerializer(serializers.Serializer):
    kind = serializers.CharField(max_length=40)
    objective = serializers.CharField(max_length=500, required=False, allow_blank=True)
    scheduled_at = serializers.DateTimeField()
    location = serializers.CharField(max_length=240, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=PastoralInteraction.Status.choices, required=False)
    sensitivity = serializers.ChoiceField(choices=PastoralInteraction.Sensitivity.choices, required=False)
    outcome = serializers.CharField(required=False, allow_blank=True)
    next_due_at = serializers.DateTimeField(required=False, allow_null=True)


class PastoralInteractionUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=PastoralInteraction.Status.choices, required=False)
    scheduled_at = serializers.DateTimeField(required=False)
    location = serializers.CharField(max_length=240, required=False, allow_blank=True)
    objective = serializers.CharField(max_length=500, required=False, allow_blank=True)
    outcome = serializers.CharField(required=False, allow_blank=True)
    next_due_at = serializers.DateTimeField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate(self, attrs):
        if {'status', 'scheduled_at', 'outcome'} & attrs.keys() and not attrs.get('reason', '').strip():
            raise serializers.ValidationError({'reason': 'Un motif est requis pour tracer le statut, le report ou le compte rendu.'})
        return attrs


class PastoralInteractionSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    outcome = serializers.SerializerMethodField()
    can_read_outcome = serializers.SerializerMethodField()

    class Meta:
        model = PastoralInteraction
        fields = [
            'id', 'case', 'kind', 'objective', 'scheduled_at', 'location', 'status',
            'sensitivity', 'outcome', 'can_read_outcome', 'next_due_at',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_outcome(self, obj):
        from .services import can_read_interaction

        request = self.context.get('request')
        if request is None or not can_read_interaction(request.user, obj):
            return None
        return obj.outcome

    def get_can_read_outcome(self, obj):
        from .services import can_read_interaction

        request = self.context.get('request')
        return bool(request and can_read_interaction(request.user, obj))


class PastoralNoteCreateSerializer(serializers.Serializer):
    content = serializers.CharField()
    sensitivity = serializers.ChoiceField(choices=PastoralNote.Sensitivity.choices, required=False)


class PastoralNoteUpdateSerializer(serializers.Serializer):
    content = serializers.CharField()
    reason = serializers.CharField(max_length=500)


class PastoralNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source='author.username', read_only=True)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = PastoralNote
        fields = [
            'id', 'case', 'author', 'author_name', 'sensitivity', 'content',
            'can_edit', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_can_edit(self, obj):
        from .services import can_access_note

        request = self.context.get('request')
        return bool(request and can_access_note(request.user, obj, edit=True))


class ShareSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    access_level = serializers.ChoiceField(choices=['READ', 'EDIT'], required=False, default='READ')
    can_edit = serializers.BooleanField(required=False, default=False)

    def validate_user_id(self, value):
        if not User.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('Utilisateur introuvable.')
        return value


class RevokeShareSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()


class PastoralPrayerCreateSerializer(serializers.Serializer):
    church_id = serializers.UUIDField()
    campus_id = serializers.UUIDField(required=False, allow_null=True)
    member_id = serializers.UUIDField(required=False, allow_null=True)
    person_id = serializers.UUIDField(required=False, allow_null=True)
    is_anonymous = serializers.BooleanField(required=False, default=False)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True)
    content = serializers.CharField()
    sensitivity = serializers.ChoiceField(choices=PastoralCase.Sensitivity.choices, required=False)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    linked_case = serializers.PrimaryKeyRelatedField(queryset=PastoralCase.objects.all(), required=False, allow_null=True)

    def validate(self, attrs):
        if attrs.get('is_anonymous'):
            if attrs.get('member_id') or attrs.get('person_id'):
                raise serializers.ValidationError('Une demande anonyme ne peut pas inclure d’identité.')
        elif bool(attrs.get('member_id')) == bool(attrs.get('person_id')):
            raise serializers.ValidationError('Sélectionnez exactement un membre ou une personne, ou déclarez la demande anonyme.')
        return attrs


class PastoralPrayerUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=PrayerRequest.Status.choices, required=False)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate(self, attrs):
        if {'status', 'assigned_to'} & attrs.keys() and not attrs.get('reason', '').strip():
            raise serializers.ValidationError({'reason': 'Un motif est requis pour tracer le changement.'})
        return attrs


class PastoralPrayerSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(source='assigned_to.username', read_only=True, default=None)
    subject_name = serializers.SerializerMethodField()

    class Meta:
        model = PrayerRequest
        fields = [
            'id', 'church', 'campus', 'is_anonymous', 'subject_name', 'category', 'content',
            'status', 'sensitivity', 'assigned_to', 'assigned_to_name', 'linked_case',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_subject_name(self, obj):
        if obj.is_anonymous:
            return None
        if obj.member_id:
            return f'{obj.member.first_name} {obj.member.last_name}'
        return f'{obj.person.first_name} {obj.person.last_name}' if obj.person_id else None


class PastoralTaskCreateSerializer(serializers.Serializer):
    interaction_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(max_length=180)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    priority = serializers.ChoiceField(choices=PastoralTask.Priority.choices, required=False)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    recurrence = serializers.ChoiceField(
        choices=[('', 'Aucune'), ('DAILY', 'Quotidienne'), ('WEEKLY', 'Hebdomadaire'), ('MONTHLY', 'Mensuelle')],
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        if attrs.get('recurrence') and not attrs.get('due_at'):
            raise serializers.ValidationError({'due_at': 'Une échéance est requise pour une tâche récurrente.'})
        return attrs


class PastoralTaskUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=180, required=False)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    priority = serializers.ChoiceField(choices=PastoralTask.Priority.choices, required=False)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=PastoralTask.Status.choices, required=False)
    recurrence = serializers.ChoiceField(
        choices=[('', 'Aucune'), ('DAILY', 'Quotidienne'), ('WEEKLY', 'Hebdomadaire'), ('MONTHLY', 'Mensuelle')],
        required=False,
        allow_blank=True,
    )
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate(self, attrs):
        if {'status', 'assigned_to'} & attrs.keys() and not attrs.get('reason', '').strip():
            raise serializers.ValidationError({'reason': 'Un motif est requis pour le changement de statut ou la réaffectation.'})
        if attrs.get('recurrence') and 'due_at' in attrs and attrs['due_at'] is None:
            raise serializers.ValidationError({'due_at': 'Une échéance est requise pour une tâche récurrente.'})
        return attrs


class PastoralTaskSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(source='assigned_to.username', read_only=True)

    class Meta:
        model = PastoralTask
        fields = [
            'id', 'case', 'interaction', 'title', 'description', 'assigned_to', 'assigned_to_name',
            'priority', 'due_at', 'status', 'recurrence', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class PastoralTransitionSerializer(serializers.Serializer):
    status = serializers.CharField(max_length=40)
    reason = serializers.CharField(max_length=500)
    next_due_at = serializers.DateTimeField(required=False, allow_null=True)


class PastoralWorkflowSerializer(serializers.ModelSerializer):
    class Meta:
        model = PastoralWorkflow
        fields = [
            'organization', 'statuses', 'transitions', 'block_close_with_open_items',
            'interaction_kinds', 'retention_days', 'archive_after_days',
            'unassigned_alert_after_days', 'updated_at',
        ]
        read_only_fields = ['organization', 'updated_at']

    def validate(self, attrs):
        instance = self.instance or PastoralWorkflow(organization=self.context['organization'])
        for key, value in attrs.items():
            setattr(instance, key, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(getattr(error, 'message_dict', str(error))) from error
        codes = {item['code'] for item in instance.statuses if isinstance(item, dict)}
        for transition in instance.transitions:
            source, target = transition.split('>', 1)
            if source not in codes or target not in codes:
                raise serializers.ValidationError({'transitions': 'Les transitions doivent utiliser des statuts configurés.'})
        return attrs

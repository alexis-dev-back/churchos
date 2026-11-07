from rest_framework import serializers

from apps.members.models import Member
from apps.people.models import Person
from infrastructure.tenancy import TenantContext


class PersonSerializer(serializers.ModelSerializer):
    invited_by_member_name = serializers.SerializerMethodField()
    invited_by_member = serializers.PrimaryKeyRelatedField(
        queryset=Member.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Person
        fields = [
            'id', 'user', 'first_name', 'last_name', 'middle_name', 'gender',
            'date_of_birth', 'phone', 'email', 'address', 'photo', 'notes',
            'nationality', 'profession', 'education_level', 'marital_status',
            'baptism_status', 'person_type', 'invited_by_member', 'invited_by_member_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']

    def validate(self, attrs):
        first_name = (attrs.get('first_name', self.instance.first_name if self.instance else '') or '').strip()
        last_name = (attrs.get('last_name', self.instance.last_name if self.instance else '') or '').strip()
        if not first_name or not last_name:
            raise serializers.ValidationError('first_name et last_name sont obligatoires.')
        if 'first_name' in attrs:
            attrs['first_name'] = first_name
        if 'last_name' in attrs:
            attrs['last_name'] = last_name
        invited_by_member = attrs.get('invited_by_member')
        organization = TenantContext.get()
        if invited_by_member and (
            organization is None
            or invited_by_member.organization_id != organization.tenant_id
        ):
            raise serializers.ValidationError({
                'invited_by_member': 'Le membre invitant doit appartenir à l’organisation active.',
            })
        return attrs

    def get_invited_by_member_name(self, person):
        member = person.invited_by_member
        return f'{member.first_name} {member.last_name}' if member else None

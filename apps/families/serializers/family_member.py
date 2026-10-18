from rest_framework import serializers

from apps.families.models import FamilyMember
from apps.members.models import Member
from apps.people.models import Person


class FamilyMemberSerializer(serializers.ModelSerializer):
    person_name = serializers.SerializerMethodField()
    member_name = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()

    class Meta:
        model = FamilyMember
        fields = [
            'id', 'family', 'person', 'member', 'person_name', 'member_name', 'email', 'phone', 'relationship_type', 'is_primary_contact',
            'joined_at', 'left_at', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'joined_at', 'left_at', 'created_at', 'updated_at']

    def validate(self, attrs):
        person = attrs.get('person')
        member = attrs.get('member')
        if bool(person) == bool(member):
            raise serializers.ValidationError('Fournissez soit person, soit member, mais pas les deux.')
        return attrs

    def validate_member(self, member):
        if member is None:
            return member
        family_id = self.initial_data.get('family')
        if family_id:
            from apps.families.models import Family
            family = Family.objects.filter(pk=family_id).first()
            if family is not None and member.organization_id != family.organization_id:
                raise serializers.ValidationError('Le membre doit appartenir à la même organisation que la famille.')
        return member

    def validate_person(self, person):
        if person is None:
            return person
        family_id = self.initial_data.get('family')
        if family_id:
            from apps.families.models import Family
            family = Family.objects.filter(pk=family_id).first()
            if family is not None and person.organization_id != family.organization_id:
                raise serializers.ValidationError('La personne doit appartenir à la même organisation que la famille.')
        return person

    def get_person_name(self, membership):
        return str(membership.person) if membership.person_id else None

    def get_member_name(self, membership):
        return str(membership.member) if membership.member_id else None

    def get_email(self, membership):
        return membership.member.email if membership.member_id else membership.person.email

    def get_phone(self, membership):
        return membership.member.phone if membership.member_id else membership.person.phone

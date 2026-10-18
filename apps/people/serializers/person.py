from rest_framework import serializers

from apps.people.models import Person


class PersonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = [
            'id', 'user', 'first_name', 'last_name', 'middle_name', 'gender',
            'date_of_birth', 'phone', 'email', 'address', 'photo', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']

    def validate(self, attrs):
        first_name = (attrs.get('first_name') or '').strip()
        last_name = (attrs.get('last_name') or '').strip()
        if not first_name or not last_name:
            raise serializers.ValidationError('first_name et last_name sont obligatoires.')
        return attrs

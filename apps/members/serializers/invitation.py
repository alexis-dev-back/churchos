from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.RegexField(regex=r'^\d{5}$', required=True)
    email = serializers.EmailField(required=True)
    username = serializers.CharField(required=True, max_length=150, trim_whitespace=True)
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirmation = serializers.CharField(write_only=True, min_length=8)

    def validate_username(self, value):
        username_field = User._meta.get_field('username')
        try:
            for validator in username_field.validators:
                validator(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc

        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirmation']:
            raise serializers.ValidationError({'password_confirmation': 'Les mots de passe ne correspondent pas.'})

        try:
            validate_password(
                attrs['password'],
                user=User(username=attrs['username'], email=attrs['email']),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)}) from exc
        return attrs

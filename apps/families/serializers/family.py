from rest_framework import serializers

from apps.families.models import Family


class FamilySerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True, default=0)
    primary_contact_name = serializers.CharField(read_only=True, allow_null=True)
    primary_contact_phone = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = Family
        fields = [
            'id', 'organization', 'name', 'address', 'neighborhood', 'ministry', 'phone', 'email',
            'notes', 'status', 'member_count', 'primary_contact_name',
            'primary_contact_phone', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'organization', 'created_at', 'updated_at']

from rest_framework import serializers

from apps.communication.models import Announcement
from apps.core.models import Church
from infrastructure.tenancy import TenantContext


class AnnouncementSerializer(serializers.ModelSerializer):
    scope = serializers.ChoiceField(
        choices=[Announcement.Scope.CHURCH],
        default=Announcement.Scope.CHURCH,
    )
    church_id = serializers.UUIDField(required=True, allow_null=False, write_only=True)
    church = serializers.UUIDField(source='church_id', read_only=True)
    church_name = serializers.CharField(source='church.name', read_only=True, allow_null=True)
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = Announcement
        fields = [
            'id', 'scope', 'church', 'church_id', 'church_name', 'title', 'body',
            'category', 'status', 'publish_at', 'author_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church', 'church_name', 'author_name', 'created_at', 'updated_at']

    def get_author_name(self, announcement):
        if not announcement.created_by:
            return ''
        return announcement.created_by.get_full_name() or announcement.created_by.get_username()

    def validate(self, attrs):
        attrs['scope'] = Announcement.Scope.CHURCH
        church_id = attrs.pop('church_id', None)
        if church_id is None:
            church_id = getattr(self.instance, 'church_id', None)
        organization = TenantContext.get()

        if not church_id:
            raise serializers.ValidationError({'church_id': 'Sélectionnez une église pour cette annonce.'})

        church = Church.objects.filter(
            church_id=church_id,
            organization=organization,
            is_active=True,
        ).first()
        if church is None:
            raise serializers.ValidationError({'church_id': 'Cette église n’est pas accessible.'})

        attrs['church'] = church
        return attrs

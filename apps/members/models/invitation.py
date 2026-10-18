import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone

from .member import Member


class MemberInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        USED = 'USED', 'Used'
        REVOKED = 'REVOKED', 'Revoked'
        EXPIRED = 'EXPIRED', 'Expired'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='invitations')
    token_hash = models.CharField(max_length=128, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_member_invitations')
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    token = None

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['token_hash'],
                condition=Q(status='PENDING'),
                name='unique_pending_member_invitation_token_hash',
            ),
        ]

    @property
    def is_valid(self):
        return self.status == self.Status.PENDING and self.expires_at > timezone.now() and self.used_at is None

    @classmethod
    def hash_token(cls, raw_token):
        return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

    def invalidate_if_needed(self):
        if self.status == self.Status.PENDING and self.expires_at <= timezone.now():
            self.status = self.Status.EXPIRED
            self.save(update_fields=['status'])

    def __str__(self):
        return f'{self.member} ({self.status})'

    @classmethod
    def create_for_member(cls, member, created_by, ttl_days=7):
        expires_at = timezone.now() + timedelta(days=ttl_days)
        cls.objects.filter(
            status=cls.Status.PENDING,
            expires_at__lte=timezone.now(),
        ).update(status=cls.Status.EXPIRED)

        for _ in range(20):
            raw_token = f'{secrets.randbelow(100000):05d}'
            token_hash = cls.hash_token(raw_token)
            try:
                with transaction.atomic():
                    invitation = cls.objects.create(
                        member=member,
                        token_hash=token_hash,
                        created_by=created_by,
                        expires_at=expires_at,
                        status=cls.Status.PENDING,
                    )
            except IntegrityError:
                if cls.objects.filter(token_hash=token_hash, status=cls.Status.PENDING).exists():
                    continue
                raise
            invitation.token = raw_token
            return invitation

        raise RuntimeError('Impossible de générer un code d’invitation unique.')

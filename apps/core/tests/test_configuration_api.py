from datetime import timedelta
import struct
import tempfile
import zlib

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import (
    AppPermission,
    Campus,
    Church,
    ConfigurationAuditLog,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    UserRoleAssignment,
)


User = get_user_model()


class ConfigurationApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='config-admin', password='Strong-password-123')
        self.organization = Organization.objects.create(name='Config Org', slug='config-org')
        self.admin_membership = OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.church = Church.objects.create(
            organization=self.organization,
            name='Config Church',
            slug='config-church',
        )
        self.campus = Campus.objects.create(
            church=self.church,
            name='Config Campus',
            slug='config-campus',
        )
        self.user = User.objects.create_user(username='config-user', password='Strong-password-123')
        self.membership = OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
        )
        self.other_organization = Organization.objects.create(name='Other Org', slug='other-config-org')
        self.other_church = Church.objects.create(
            organization=self.other_organization,
            name='Other Church',
            slug='other-config-church',
        )
        self.authenticate(self.admin)

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def create_role(self, name='Trésorier', code='tresorier'):
        response = self.client.post(
            '/api/configuration/roles/',
            {'name': name, 'code': code, 'description': 'Rôle financier personnalisé'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_permission_catalog_and_role_duplicate_validation(self):
        catalog = self.client.get('/api/configuration/permissions/')
        created = self.create_role()
        duplicate = self.client.post(
            '/api/configuration/roles/',
            {'name': 'Autre rôle', 'code': 'tresorier'},
            format='json',
        )

        self.assertEqual(catalog.status_code, 200)
        self.assertIn('finance.expense.approve', {item['code'] for item in catalog.data})
        self.assertEqual(created['permission_codes'], [])
        self.assertEqual(duplicate.status_code, 400)

    def test_role_permissions_and_assignment_enforce_tenant_and_scope(self):
        role = self.create_role()
        permissions = self.client.post(
            f"/api/configuration/roles/{role['id']}/permissions/",
            {'permission_codes': ['members.read', 'attendance.validate']},
            format='json',
        )
        assignment = self.client.post(
            '/api/configuration/user-roles/',
            {
                'membership_id': self.membership.pk,
                'role_id': role['id'],
                'scope': 'CAMPUS',
                'church_id': str(self.church.church_id),
                'campus_id': str(self.campus.campus_id),
            },
            format='json',
        )
        invalid_tenant = self.client.post(
            '/api/configuration/user-roles/',
            {
                'membership_id': self.membership.pk,
                'role_id': role['id'],
                'scope': 'CHURCH',
                'church_id': str(self.other_church.church_id),
            },
            format='json',
        )

        self.assertEqual(permissions.status_code, 200, permissions.data)
        self.assertCountEqual(permissions.data['permission_codes'], ['members.read', 'attendance.validate'])
        self.assertEqual(assignment.status_code, 201, assignment.data)
        self.assertEqual(invalid_tenant.status_code, 404)
        self.assertEqual(
            UserRoleAssignment.objects.filter(organization=self.organization).count(),
            1,
        )

    def test_custom_permission_scope_expiry_and_revoke_are_effective_immediately(self):
        role = self.create_role()
        self.client.post(
            f"/api/configuration/roles/{role['id']}/permissions/",
            {'permission_codes': ['members.read']},
            format='json',
        )
        assigned = self.client.post(
            '/api/configuration/user-roles/',
            {
                'membership_id': self.membership.pk,
                'role_id': role['id'],
                'scope': 'CAMPUS',
                'campus_id': str(self.campus.campus_id),
            },
            format='json',
        )
        assignment = UserRoleAssignment.objects.get(pk=assigned.data['id'])
        self.authenticate(self.user)
        from apps.core.services.configuration_access import has_configuration_permission

        self.assertTrue(has_configuration_permission(
            self.user,
            'members.read',
            self.organization,
            campus=self.campus,
        ))
        self.assertFalse(has_configuration_permission(
            self.user,
            'members.read',
            self.organization,
            church=self.church,
        ))
        assignment.expires_at = timezone.now() - timedelta(seconds=1)
        assignment.save(update_fields=['expires_at'])
        self.assertFalse(has_configuration_permission(
            self.user,
            'members.read',
            self.organization,
            campus=self.campus,
        ))
        assignment.expires_at = None
        assignment.save(update_fields=['expires_at'])
        self.authenticate(self.admin)
        revoked = self.client.post(
            f"/api/configuration/user-roles/{assignment.pk}/revoke/",
            {},
            format='json',
        )
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertIsNotNone(revoked.data['revoked_at'])
        from apps.core.services.configuration_access import has_configuration_permission
        self.assertFalse(has_configuration_permission(
            self.user,
            'members.read',
            self.organization,
            campus=self.campus,
        ))

    def test_non_admin_cannot_read_another_tenant_configuration(self):
        self.authenticate(self.user)
        response = self.client.get('/api/configuration/roles/')
        settings = self.client.get(
            f'/api/configuration/church/?church_id={self.church.church_id}'
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(settings.status_code, 403)

    def test_effective_access_and_member_api_use_custom_role_permissions(self):
        role = self.create_role()
        self.client.post(
            f"/api/configuration/roles/{role['id']}/permissions/",
            {'permission_codes': ['members.read']},
            format='json',
        )
        self.client.post(
            '/api/configuration/user-roles/',
            {
                'membership_id': self.membership.pk,
                'role_id': role['id'],
                'scope': 'ORGANIZATION',
            },
            format='json',
        )
        self.authenticate(self.user)

        access = self.client.get('/api/configuration/access/')
        members = self.client.get('/api/members/')

        self.assertEqual(access.status_code, 200, access.data)
        self.assertEqual(access.data['permission_codes'], ['members.read'])
        self.assertEqual(members.status_code, 200, members.data)

    def test_church_logo_upload_validates_file_content_and_size(self):
        def png_chunk(chunk_type, data):
            chunk = chunk_type + data
            return struct.pack('>I', len(data)) + chunk + struct.pack('>I', zlib.crc32(chunk) & 0xffffffff)

        image = (
            b'\x89PNG\r\n\x1a\n'
            + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + png_chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00'))
            + png_chunk(b'IEND', b'')
        )
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                invalid = self.client.patch(
                    f'/api/configuration/church/?church_id={self.church.church_id}',
                    {'logo_primary': SimpleUploadedFile('logo.png', b'not an image', content_type='image/png')},
                    format='multipart',
                )
                self.assertEqual(invalid.status_code, 400, invalid.data)
                valid = self.client.patch(
                    f'/api/configuration/church/?church_id={self.church.church_id}',
                    {'logo_primary': SimpleUploadedFile('logo.png', image, content_type='image/png')},
                    format='multipart',
                )
                self.assertEqual(valid.status_code, 200, valid.data)
                self.assertTrue(valid.data['logo_primary'].endswith('.png'))

    def test_church_settings_patch_updates_related_church_fields(self):
        response = self.client.patch(
            f'/api/configuration/church/?church_id={self.church.church_id}',
            {
                'address': '10 rue de la Paix',
                'phone': '+22501020304',
                'email': 'contact@config-church.test',
                'city': 'Abidjan',
                'region': 'Lagunes',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.church.refresh_from_db()
        self.assertEqual(self.church.address, '10 rue de la Paix')
        self.assertEqual(self.church.phone, '+22501020304')
        self.assertEqual(self.church.email, 'contact@config-church.test')
        self.assertEqual(response.data['city'], 'Abidjan')

    def test_membership_suspension_is_tenant_scoped_and_protects_last_admin(self):
        other_membership = OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.other_organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        suspend_admin = self.client.patch(
            f'/api/configuration/users/{self.admin_membership.pk}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(suspend_admin.status_code, 400, suspend_admin.data)

        second_admin = User.objects.create_user(username='config-admin-two', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=second_admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        suspend = self.client.patch(
            f'/api/configuration/users/{self.admin_membership.pk}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(suspend.status_code, 200, suspend.data)
        self.assertFalse(self.admin_membership.__class__.objects.get(pk=self.admin_membership.pk).is_active)
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_active)
        self.assertTrue(OrganizationMembership.objects.get(pk=other_membership.pk).is_active)

    def test_audit_log_is_immutable(self):
        self.create_role()
        entry = ConfigurationAuditLog.objects.get(organization=self.organization)
        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(ValidationError):
            ConfigurationAuditLog.objects.filter(pk=entry.pk).update(action='tampered')

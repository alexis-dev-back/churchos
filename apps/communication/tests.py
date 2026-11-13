from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.communication.models import Announcement
from apps.core.models import (
    AppPermission,
    Church,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    OrganizationRolePermission,
    UserRoleAssignment,
)
from apps.members.models import Member

User = get_user_model()


class CommunicationApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='communication-owner',
            email='communication-owner@example.com',
            password='Strong-password-123',
        )
        self.organization = Organization.objects.create(
            name='Communication Organization',
            slug='communication-organization',
            owner=self.owner,
        )
        OrganizationMembership.objects.create(
            user=self.owner,
            organization=self.organization,
            role=OrganizationMembership.Role.OWNER,
        )
        self.church = Church.objects.create(
            organization=self.organization,
            name='Church A',
            slug='communication-church-a',
            pastor_name='Pastor A',
        )
        self.other_church = Church.objects.create(
            organization=self.organization,
            name='Church B',
            slug='communication-church-b',
            pastor_name='Pastor B',
        )
        Member.objects.create(
            user=self.owner,
            organization=self.organization,
            church=self.church,
            first_name='Communication',
            last_name='Owner',
            email='communication-owner@example.com',
            status=Member.Status.ACTIVE,
        )

    def authenticate(self, user, organization=None):
        token = RefreshToken.for_user(user)
        token['tenant_id'] = str((organization or self.organization).tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

    def create_announcement(self, **kwargs):
        defaults = {
            'organization': self.organization,
            'created_by': self.owner,
            'title': 'Service update',
            'body': 'Sunday service begins at 10:00.',
            'scope': Announcement.Scope.CHURCH,
            'church': self.church,
            'status': Announcement.Status.PUBLISHED,
        }
        defaults.update(kwargs)
        return Announcement.objects.create(**defaults)

    def test_owner_can_create_and_manage_an_announcement(self):
        self.authenticate(self.owner)
        response = self.client.post('/api/communication/announcements/', {
            'scope': 'CHURCH',
            'church_id': str(self.church.church_id),
            'title': 'Youth meeting',
            'body': 'Meet after the service.',
            'category': 'Youth',
            'status': 'PUBLISHED',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        announcement = Announcement.objects.get(pk=response.data['id'])
        self.assertEqual(announcement.church, self.church)
        self.assertEqual(response.data['church_name'], self.church.name)

        updated = self.client.patch(
            f'/api/communication/announcements/{announcement.pk}/',
            {'status': 'ARCHIVED'},
            format='json',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['status'], Announcement.Status.ARCHIVED)

    def test_organization_scope_is_not_accepted(self):
        self.authenticate(self.owner)
        response = self.client.post('/api/communication/announcements/', {
            'scope': 'ORGANIZATION',
            'church_id': None,
            'title': 'Organization broadcast',
            'body': 'This scope must not be accepted.',
            'status': 'DRAFT',
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)

    def test_member_only_sees_published_announcements_for_their_scope(self):
        member_user = User.objects.create_user(
            username='communication-member',
            email='communication-member@example.com',
            password='Strong-password-123',
            is_active=True,
        )
        OrganizationMembership.objects.create(
            user=member_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        Member.objects.create(
            user=member_user,
            organization=self.organization,
            church=self.church,
            first_name='Communication',
            last_name='Member',
            email=member_user.email,
            status=Member.Status.ACTIVE,
        )
        church_notice = self.create_announcement(
            title='Church notice',
            church=self.church,
        )
        self.create_announcement(
            title='Other church notice',
            church=self.other_church,
        )
        self.create_announcement(
            title='Draft notice',
            status=Announcement.Status.DRAFT,
        )

        self.authenticate(member_user)
        response = self.client.get('/api/communication/announcements/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertCountEqual(
            [item['id'] for item in response.data],
            [str(church_notice.pk)],
        )

    def test_announcement_api_is_isolated_by_active_tenant(self):
        self.create_announcement(title='Tenant A notice')
        other_owner = User.objects.create_user(
            username='other-communication-owner',
            email='other-communication-owner@example.com',
            password='Strong-password-123',
        )
        other_organization = Organization.objects.create(
            name='Other Communication Organization',
            slug='other-communication-organization',
            owner=other_owner,
        )
        OrganizationMembership.objects.create(
            user=other_owner,
            organization=other_organization,
            role=OrganizationMembership.Role.OWNER,
        )

        self.authenticate(other_owner, other_organization)
        response = self.client.get('/api/communication/announcements/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, [])

    def test_manager_cannot_target_another_tenant_church(self):
        other_owner = User.objects.create_user(
            username='foreign-church-owner',
            email='foreign-church-owner@example.com',
            password='Strong-password-123',
        )
        other_organization = Organization.objects.create(
            name='Foreign Church Organization',
            slug='foreign-church-organization',
            owner=other_owner,
        )
        foreign_church = Church.objects.create(
            organization=other_organization,
            name='Foreign Church',
            slug='foreign-communication-church',
            pastor_name='Foreign Pastor',
        )

        self.authenticate(self.owner)
        response = self.client.post('/api/communication/announcements/', {
            'scope': 'CHURCH',
            'church_id': str(foreign_church.church_id),
            'title': 'Invalid target',
            'body': 'This must not be accepted.',
            'status': 'DRAFT',
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)

    def test_scoped_manager_only_sees_and_manages_assigned_church(self):
        manager = User.objects.create_user(
            username='church-communication-manager',
            email='church-communication-manager@example.com',
            password='Strong-password-123',
            is_active=True,
        )
        membership = OrganizationMembership.objects.create(
            user=manager,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        Member.objects.create(
            user=manager,
            organization=self.organization,
            church=self.church,
            first_name='Church',
            last_name='Manager',
            email='church-manager@example.com',
            status=Member.Status.ACTIVE,
        )
        role = OrganizationRole.objects.create(
            organization=self.organization,
            name='Communication manager',
            code='communication-manager',
            created_by=self.owner,
        )
        permission = AppPermission.objects.get(code='communication.manage')
        OrganizationRolePermission.objects.create(role=role, permission=permission)
        UserRoleAssignment.objects.create(
            organization=self.organization,
            membership=membership,
            role=role,
            scope=UserRoleAssignment.Scope.CHURCH,
            church=self.church,
            created_by=self.owner,
        )
        self.authenticate(manager)

        context = self.client.get('/api/communication/context/')
        self.assertEqual(context.status_code, 200, context.data)
        self.assertEqual(
            [str(church['church_id']) for church in context.data['churches']],
            [str(self.church.church_id)],
        )
        self.assertFalse(context.data['can_manage_all'])

        forbidden = self.client.post('/api/communication/announcements/', {
            'scope': 'CHURCH',
            'church_id': str(self.other_church.church_id),
            'title': 'Church B update',
            'body': 'This message is outside the assigned scope.',
            'status': 'DRAFT',
        }, format='json')
        self.assertEqual(forbidden.status_code, 403, forbidden.data)

        response = self.client.post('/api/communication/announcements/', {
            'scope': 'CHURCH',
            'church_id': str(self.church.church_id),
            'title': 'Church A update',
            'body': 'A church-scoped message.',
            'status': 'DRAFT',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)

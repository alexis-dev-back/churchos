from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Campus, Church, Organization, OrganizationMembership
from apps.events.models import ChurchEventAdministrator
from apps.members.models import Member

User = get_user_model()


class OrganizationAuthenticationTests(APITestCase):
    def test_church_context_defaults_to_the_authenticated_members_church(self):
        user = User.objects.create_user(username='church-admin', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='church-context')
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        first_church = Church.objects.create(organization=organization, name='Church A', slug='context-church-a')
        Church.objects.create(organization=organization, name='Church B', slug='context-church-b')
        Member.objects.create(
            user=user,
            organization=organization,
            church=first_church,
            first_name='Church',
            last_name='Admin',
            email='church-admin@example.com',
            status=Member.Status.ACTIVE,
        )
        refresh = RefreshToken.for_user(user)
        refresh['tenant_id'] = str(organization.tenant_id)
        access_token = str(refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.get('/api/churches/context/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['churches']), 2)
        self.assertEqual(response.data['default_church_id'], str(first_church.church_id))
        self.assertTrue(response.data['church_selection_fixed'])

    def test_church_context_limits_a_church_manager_to_their_assigned_church(self):
        user = User.objects.create_user(username='church-manager', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='church-manager-context')
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        managed_church = Church.objects.create(
            organization=organization,
            name='Managed',
            slug='managed-context-church',
        )
        Church.objects.create(organization=organization, name='Other', slug='other-context-church')
        member = Member.objects.create(
            user=user,
            organization=organization,
            church=managed_church,
            first_name='Church',
            last_name='Manager',
            email='church-manager@example.com',
            status=Member.Status.ACTIVE,
        )
        ChurchEventAdministrator.objects.create(church=managed_church, member=member)
        refresh = RefreshToken.for_user(user)
        refresh['tenant_id'] = str(organization.tenant_id)
        access_token = str(refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.get('/api/churches/context/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([church['church_id'] for church in response.data['churches']], [str(managed_church.church_id)])
        self.assertEqual(response.data['default_church_id'], str(managed_church.church_id))
        self.assertTrue(response.data['church_selection_fixed'])

    def test_login_and_refresh_return_jwt_pair(self):
        User.objects.create_user(username='alice', password='Strong-password-123')

        login = self.client.post('/api/auth/login/', {
            'username': 'alice',
            'password': 'Strong-password-123',
        }, format='json')

        self.assertEqual(login.status_code, 200)
        self.assertIn('access', login.data)
        self.assertIn('refresh', login.data)

        refresh = self.client.post('/api/auth/refresh/', {
            'refresh': login.data['refresh'],
        }, format='json')

        self.assertEqual(refresh.status_code, 200)
        self.assertIn('access', refresh.data)
        self.assertIn('refresh', refresh.data)

    def test_login_is_rate_limited(self):
        cache.clear()
        responses = [
            self.client.post('/api/auth/login/', {
                'username': 'unknown',
                'password': 'invalid',
            }, format='json')
            for _ in range(11)
        ]

        cache.clear()
        self.assertTrue(all(response.status_code == 400 for response in responses[:10]))
        self.assertEqual(responses[10].status_code, 429)

    def test_registration_creates_user_and_jwt_without_organization(self):
        superuser = User.objects.create_superuser(username='root', password='Strong-password-123')
        token = RefreshToken.for_user(superuser)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        response = self.client.post('/api/auth/register/', {
            'username': 'alice',
            'email': 'alice@example.com',
            'password': 'Strong-password-123',
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(Organization.objects.count(), 0)
        self.assertFalse(OrganizationMembership.objects.filter(user__username='alice').exists())

    def test_registration_rejects_numeric_password(self):
        superuser = User.objects.create_superuser(username='root', password='Strong-password-123')
        token = RefreshToken.for_user(superuser)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        response = self.client.post('/api/auth/register/', {
            'username': 'alice',
            'email': 'alice@example.com',
            'password': '12345678',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username='alice').exists())

    def test_regular_user_cannot_register_accounts_or_access_superadmin(self):
        user = User.objects.create_user(username='regular', password='Strong-password-123')
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

        registration = self.client.post('/api/auth/register/', {
            'username': 'blocked',
            'email': 'blocked@example.com',
            'password': 'Strong-password-123',
        }, format='json')
        organizations = self.client.get('/api/superadmin/organizations/')

        self.assertEqual(registration.status_code, 403)
        self.assertEqual(organizations.status_code, 403)
        self.assertFalse(User.objects.filter(username='blocked').exists())

    def test_regular_user_cannot_create_organization(self):
        user = User.objects.create_user(username='alice', password='Strong-password-123')
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        response = self.client.post('/api/organizations/create/', {
            'name': 'Eglise Source',
            'slug': 'eglise-source',
        }, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Organization.objects.filter(slug='eglise-source').exists())

    def test_superadmin_manages_organizations_without_becoming_a_tenant_member(self):
        superuser = User.objects.create_superuser(username='root', password='Strong-password-123')
        token = RefreshToken.for_user(superuser)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

        response = self.client.post('/api/superadmin/organizations/', {
            'name': 'Managed Church',
            'slug': 'managed-church',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        organization = Organization.objects.get(slug='managed-church')
        self.assertIsNone(organization.owner)
        self.assertFalse(OrganizationMembership.objects.filter(user=superuser, organization=organization).exists())
        listing = self.client.get('/api/superadmin/organizations/')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.data), 1)

        church_response = self.client.post(
            f"/api/superadmin/organizations/{organization.tenant_id}/churches/",
            {'name': 'Central Church', 'slug': 'central-church'},
            format='json',
        )
        self.assertEqual(church_response.status_code, 201, church_response.data)
        self.assertTrue(Church.objects.filter(organization=organization, slug='central-church').exists())

    def test_superadmin_can_update_and_delete_organization_without_tenant_membership(self):
        superuser = User.objects.create_superuser(username='root', password='Strong-password-123')
        organization = Organization.objects.create(name='Managed', slug='managed')
        access_token = str(RefreshToken.for_user(superuser).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        update = self.client.put(
            f'/api/organizations/{organization.tenant_id}/update/',
            {'name': 'Updated'},
            format='json',
        )
        self.assertEqual(update.status_code, 200, update.data)
        organization.refresh_from_db()
        self.assertEqual(organization.name, 'Updated')

        delete = self.client.delete(f'/api/organizations/{organization.tenant_id}/delete/')
        self.assertEqual(delete.status_code, 204)
        self.assertFalse(Organization.objects.filter(pk=organization.tenant_id).exists())

    def test_superadmin_can_create_user_and_assign_organization_admin(self):
        superuser = User.objects.create_superuser(username='root', password='Strong-password-123')
        organization = Organization.objects.create(name='Managed', slug='managed')
        token = RefreshToken.for_user(superuser)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

        response = self.client.post('/api/superadmin/users/', {
            'username': 'church-admin',
            'email': 'admin@example.com',
            'password': 'Strong-password-123',
            'tenant_id': str(organization.tenant_id),
            'role': 'admin',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(OrganizationMembership.objects.filter(
            user__username='church-admin', organization=organization, role=OrganizationMembership.Role.ADMIN,
        ).exists())

    def test_organization_list_is_authenticated_and_isolated(self):
        first_user = User.objects.create_user(username='first', password='Strong-password-123')
        second_user = User.objects.create_user(username='second', password='Strong-password-123')
        first_organization = Organization.objects.create(name='First', slug='first', owner=first_user)
        second_organization = Organization.objects.create(name='Second', slug='second', owner=second_user)
        OrganizationMembership.objects.create(user=first_user, organization=first_organization, role='owner')
        OrganizationMembership.objects.create(user=second_user, organization=second_organization, role='owner')

        response = self.client.get('/api/organizations/')
        self.assertEqual(response.status_code, 401)

        access_token = str(RefreshToken.for_user(first_user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        response = self.client.get('/api/organizations/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['slug'] for item in response.data], ['first'])

    def test_member_can_select_active_organization(self):
        user = User.objects.create_user(username='member', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=user)
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=OrganizationMembership.Role.OWNER,
        )
        access_token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post(
            '/api/auth/select-organization/',
            {'tenant_id': str(organization.tenant_id)},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['tenant_id'], str(organization.tenant_id))
        self.assertEqual(response.data['role'], OrganizationMembership.Role.OWNER)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

        refreshed = self.client.post(
            '/api/auth/refresh/',
            {'refresh': response.data['refresh']},
            format='json',
        )
        self.assertEqual(refreshed.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refreshed.data['access']}")
        profile = self.client.get('/api/auth/me/')
        self.assertEqual(
            profile.data['active_organization']['tenant_id'],
            str(organization.tenant_id),
        )
        from infrastructure.tenancy import TenantContext
        self.assertFalse(TenantContext.exists())

    def test_single_active_organization_is_selected_automatically(self):
        user = User.objects.create_user(username='member', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=user)
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=OrganizationMembership.Role.OWNER,
        )
        access_token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post('/api/auth/select-organization/', {}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['requires_selection'])
        self.assertEqual(response.data['tenant_id'], str(organization.tenant_id))
        self.assertIn('access', response.data)

    def test_single_organization_admin_can_create_member_without_manual_selection(self):
        user = User.objects.create_user(username='admin', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=user)
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        church = Church.objects.create(
            organization=organization,
            name='Church A',
            slug='church-a',
            pastor_name='Pastor A',
        )
        campus = Campus.objects.create(
            church=church,
            name='Campus A',
            slug='campus-a',
        )
        access_token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post('/api/members/', {
            'first_name': 'Jean',
            'last_name': 'Dupont',
            'email': 'jean@example.com',
            'phone': '+22501020304',
            'gender': 'M',
            'date_of_birth': '1995-01-15',
            'church': str(church.church_id),
            'campus': str(campus.campus_id),
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertIn('invitation_token', response.data)

    def test_multiple_active_organizations_require_selection(self):
        user = User.objects.create_user(username='member', password='Strong-password-123')
        organizations = [
            Organization.objects.create(name=name, slug=slug, owner=user)
            for name, slug in [('First', 'first'), ('Second', 'second')]
        ]
        for organization in organizations:
            OrganizationMembership.objects.create(
                user=user,
                organization=organization,
                role=OrganizationMembership.Role.OWNER,
            )
        access_token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post('/api/auth/select-organization/', {}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['requires_selection'])
        self.assertEqual(len(response.data['organizations']), 2)

        selected = self.client.post(
            '/api/auth/select-organization/',
            {'tenant_id': str(organizations[1].tenant_id)},
            format='json',
        )
        self.assertEqual(selected.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {selected.data['access']}")
        profile = self.client.get('/api/auth/me/')
        self.assertEqual(
            profile.data['active_organization']['tenant_id'],
            str(organizations[1].tenant_id),
        )
        organization_list = self.client.get('/api/organizations/')
        self.assertEqual(
            [item['tenant_id'] for item in organization_list.data],
            [str(organizations[1].tenant_id)],
        )

    def test_unscoped_token_has_no_active_organization(self):
        user = User.objects.create_user(username='member', password='Strong-password-123')
        access_token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.get('/api/auth/me/')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['active_organization'])

    def test_tenant_access_is_rechecked_on_each_request(self):
        user = User.objects.create_user(username='member', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=user)
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=OrganizationMembership.Role.OWNER,
        )
        refresh = RefreshToken.for_user(user)
        refresh['tenant_id'] = str(organization.tenant_id)
        access_token = str(refresh.access_token)
        OrganizationMembership.objects.filter(user=user, organization=organization).delete()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.get('/api/auth/me/')

        self.assertEqual(response.status_code, 401)

    def test_user_cannot_select_organization_without_membership(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        outsider = User.objects.create_user(username='outsider', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        access_token = str(RefreshToken.for_user(outsider).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post(
            '/api/auth/select-organization/',
            {'tenant_id': str(organization.tenant_id)},
            format='json',
        )

        self.assertEqual(response.status_code, 403)

    def test_owner_can_add_existing_user_without_creating_an_organization(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        member = User.objects.create_user(username='member', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        OrganizationMembership.objects.create(
            user=owner,
            organization=organization,
            role=OrganizationMembership.Role.OWNER,
        )
        refresh = RefreshToken.for_user(owner)
        refresh['tenant_id'] = str(organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        response = self.client.post(
            f'/api/organizations/{organization.tenant_id}/members/',
            {'username': member.username},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['role'], OrganizationMembership.Role.MEMBER)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertTrue(
            OrganizationMembership.objects.filter(user=member, organization=organization).exists()
        )

    def test_non_member_cannot_add_user_to_an_organization(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        outsider = User.objects.create_user(username='outsider', password='Strong-password-123')
        target = User.objects.create_user(username='target', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        refresh = RefreshToken.for_user(outsider)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        response = self.client.post(
            f'/api/organizations/{organization.tenant_id}/members/',
            {'username': target.username},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            OrganizationMembership.objects.filter(user=target, organization=organization).exists()
        )

    def test_member_cannot_update_or_delete_organization(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        member = User.objects.create_user(username='member', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        OrganizationMembership.objects.create(
            user=owner,
            organization=organization,
            role=OrganizationMembership.Role.OWNER,
        )
        OrganizationMembership.objects.create(
            user=member,
            organization=organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        refresh = RefreshToken.for_user(member)
        refresh['tenant_id'] = str(organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        update = self.client.put(
            f'/api/organizations/{organization.tenant_id}/update/',
            {'name': 'Changed'},
            format='json',
        )
        delete = self.client.delete(f'/api/organizations/{organization.tenant_id}/delete/')

        self.assertEqual(update.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        organization.refresh_from_db()
        self.assertEqual(organization.name, 'First')

    def test_organization_admin_cannot_update_or_delete_organization(self):
        admin = User.objects.create_user(username='admin', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first')
        OrganizationMembership.objects.create(
            user=admin,
            organization=organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        refresh = RefreshToken.for_user(admin)
        refresh['tenant_id'] = str(organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        update = self.client.put(
            f'/api/organizations/{organization.tenant_id}/update/',
            {'name': 'Changed'},
            format='json',
        )
        delete = self.client.delete(f'/api/organizations/{organization.tenant_id}/delete/')

        self.assertEqual(update.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        organization.refresh_from_db()
        self.assertEqual(organization.name, 'First')

    def test_member_cannot_create_another_organization(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        member = User.objects.create_user(username='member', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        OrganizationMembership.objects.create(user=owner, organization=organization, role='owner')
        OrganizationMembership.objects.create(user=member, organization=organization, role='member')
        access_token = str(RefreshToken.for_user(member).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        response = self.client.post('/api/organizations/create/', {
            'name': 'Second',
            'slug': 'second',
        }, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Organization.objects.filter(slug='second').exists())

    def test_member_cannot_create_church_in_their_organization(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        member = User.objects.create_user(username='member', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        OrganizationMembership.objects.create(user=owner, organization=organization, role='owner')
        OrganizationMembership.objects.create(user=member, organization=organization, role='member')
        refresh = RefreshToken.for_user(member)
        refresh['tenant_id'] = str(organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        response = self.client.post('/api/churches/create/', {
            'organization': str(organization.tenant_id),
            'name': 'Church',
            'slug': 'church',
        }, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Church.objects.filter(slug='church').exists())

    def test_member_cannot_manage_people_or_families(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        member = User.objects.create_user(username='member', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        OrganizationMembership.objects.create(user=owner, organization=organization, role='owner')
        OrganizationMembership.objects.create(user=member, organization=organization, role='member')
        refresh = RefreshToken.for_user(member)
        refresh['tenant_id'] = str(organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        person = self.client.post('/api/people/', {'first_name': 'Marie', 'last_name': 'Kouassi'}, format='json')
        family = self.client.post('/api/families/', {'name': 'Famille Kouassi'}, format='json')

        self.assertEqual(person.status_code, 403)
        self.assertEqual(family.status_code, 403)

    def test_non_member_cannot_access_or_mutate_church_or_campus(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        outsider = User.objects.create_user(username='outsider', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        OrganizationMembership.objects.create(
            user=owner,
            organization=organization,
            role=OrganizationMembership.Role.OWNER,
        )
        outsider_organization = Organization.objects.create(
            name='Outsider organization',
            slug='outsider-organization',
            owner=outsider,
        )
        OrganizationMembership.objects.create(
            user=outsider,
            organization=outsider_organization,
            role=OrganizationMembership.Role.OWNER,
        )
        church = Church.objects.create(
            organization=organization,
            name='Church',
            slug='church',
        )
        campus = Campus.objects.create(church=church, name='Campus', slug='campus')
        refresh = RefreshToken.for_user(outsider)
        refresh['tenant_id'] = str(outsider_organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        church_detail = self.client.get(f'/api/churches/{church.church_id}/')
        church_update = self.client.put(
            f'/api/churches/{church.church_id}/update/',
            {'name': 'Changed'},
            format='json',
        )
        church_delete = self.client.delete(f'/api/churches/{church.church_id}/delete/')
        campus_detail = self.client.get(f'/api/campuses/{campus.campus_id}/')

        self.assertEqual(church_detail.status_code, 404)
        self.assertEqual(church_update.status_code, 404)
        self.assertEqual(church_delete.status_code, 404)
        self.assertEqual(campus_detail.status_code, 404)
        self.assertTrue(Church.objects.filter(pk=church.pk).exists())
        self.assertTrue(Campus.objects.filter(pk=campus.pk).exists())

    def test_member_can_create_church_in_their_organization(self):
        owner = User.objects.create_user(username='owner', password='Strong-password-123')
        organization = Organization.objects.create(name='First', slug='first', owner=owner)
        OrganizationMembership.objects.create(
            user=owner,
            organization=organization,
            role=OrganizationMembership.Role.OWNER,
        )
        refresh = RefreshToken.for_user(owner)
        refresh['tenant_id'] = str(organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        response = self.client.post(
            '/api/churches/create/',
            {
                'organization': str(organization.tenant_id),
                'name': 'Church',
                'slug': 'church',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Church.objects.filter(organization=organization, slug='church').exists())

    def test_logout_blacklists_refresh_token(self):
        user = User.objects.create_user(username='alice', password='Strong-password-123')
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        response = self.client.post(
            '/api/auth/logout/',
            {'refresh': str(refresh)},
            format='json',
        )

        self.assertEqual(response.status_code, 204)
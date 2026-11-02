from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Campus, Church, Organization, OrganizationMembership
from apps.families.models import Family, FamilyMember
from apps.members.models import Member
from apps.people.models import Person

User = get_user_model()


class FamilyApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner2', email='owner2@example.com', password='Strong-password-123')
        self.organization = Organization.objects.create(name='Org 2', slug='org-2', owner=self.user)
        OrganizationMembership.objects.create(user=self.user, organization=self.organization, role=OrganizationMembership.Role.OWNER)
        refresh = RefreshToken.for_user(self.user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def test_create_family_and_add_member(self):
        family_response = self.client.post('/api/families/', {
            'name': 'Famille Kouassi',
            'address': 'Abidjan',
            'neighborhood': 'Cocody',
            'ministry': 'Louange & Adoration',
            'phone': '+22501010101',
            'email': 'famille@example.com',
        }, format='json')
        self.assertEqual(family_response.status_code, 201)
        self.assertEqual(family_response.data['neighborhood'], 'Cocody')
        self.assertEqual(family_response.data['ministry'], 'Louange & Adoration')

        person = Person.objects.create(organization=self.organization, first_name='Jean', last_name='Kouassi', email='jean@example.com')
        member_response = self.client.post(f"/api/families/{family_response.data['id']}/members/", {
            'person': str(person.id),
            'relationship_type': 'HEAD',
            'is_primary_contact': True,
        }, format='json')

        self.assertEqual(member_response.status_code, 201, member_response.data)
        self.assertTrue(Family.objects.filter(name='Famille Kouassi').exists())
        self.assertTrue(FamilyMember.objects.filter(person=person).exists())

    def test_family_list_and_detail_include_member_summary(self):
        family = Family.objects.create(
            organization=self.organization,
            name='Famille Kouassi',
            phone='+22501010101',
            status=Family.Status.FOLLOW_UP,
        )
        person = Person.objects.create(
            organization=self.organization,
            first_name='Jean',
            last_name='Kouassi',
            phone='+22502020202',
        )
        FamilyMember.objects.create(
            family=family,
            person=person,
            relationship_type='HEAD',
            is_primary_contact=True,
        )

        list_response = self.client.get('/api/families/')
        detail_response = self.client.get(f'/api/families/{family.id}/')

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data[0]['member_count'], 1)
        self.assertEqual(list_response.data[0]['primary_contact_name'], 'Jean Kouassi')
        self.assertEqual(list_response.data[0]['primary_contact_phone'], '+22502020202')
        self.assertEqual(list_response.data[0]['status'], Family.Status.FOLLOW_UP)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data['member_count'], 1)

    def test_delete_family_removes_its_memberships(self):
        family = Family.objects.create(organization=self.organization, name='Famille à supprimer')
        person = Person.objects.create(organization=self.organization, first_name='Jean', last_name='Kouassi')
        FamilyMember.objects.create(family=family, person=person)

        response = self.client.delete(f'/api/families/{family.id}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Family.objects.filter(pk=family.id).exists())
        self.assertFalse(FamilyMember.objects.filter(family_id=family.id).exists())

    def test_adding_person_twice_returns_validation_error_instead_of_server_error(self):
        family = Family.objects.create(organization=self.organization, name='Famille Kouassi')
        person = Person.objects.create(organization=self.organization, first_name='Jean', last_name='Kouassi', email='jean@example.com')
        payload = {
            'person': str(person.id),
            'relationship_type': 'HEAD',
            'is_primary_contact': True,
        }

        first_response = self.client.post(f'/api/families/{family.id}/members/', payload, format='json')
        duplicate_response = self.client.post(f'/api/families/{family.id}/members/', payload, format='json')

        self.assertEqual(first_response.status_code, 201, first_response.data)
        self.assertEqual(duplicate_response.status_code, 400, duplicate_response.data)
        self.assertEqual(FamilyMember.objects.filter(family=family, person=person).count(), 1)

    def test_person_from_another_organization_cannot_be_added_to_family(self):
        family = Family.objects.create(organization=self.organization, name='Famille locale')
        other_organization = Organization.objects.create(name='Autre organisation', slug='autre-organisation')
        foreign_person = Person.objects.create(
            organization=other_organization,
            first_name='Jean',
            last_name='Kouassi',
        )

        response = self.client.post(f'/api/families/{family.id}/members/', {
            'person': str(foreign_person.id),
            'relationship_type': 'HEAD',
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(FamilyMember.objects.filter(family=family, person=foreign_person).exists())

    def test_legacy_cross_organization_family_membership_is_not_exposed(self):
        family = Family.objects.create(organization=self.organization, name='Famille locale')
        other_organization = Organization.objects.create(name='Autre organisation', slug='autre-organisation')
        foreign_person = Person.objects.create(
            organization=other_organization,
            first_name='Jean',
            last_name='Kouassi',
        )
        FamilyMember.objects.create(family=family, person=foreign_person, relationship_type='HEAD')

        family_detail = self.client.get(f'/api/families/{family.id}/')
        members = self.client.get(f'/api/families/{family.id}/members/')

        self.assertEqual(family_detail.status_code, 200)
        self.assertEqual(family_detail.data['member_count'], 0)
        self.assertIsNone(family_detail.data['primary_contact_name'])
        self.assertEqual(members.status_code, 200)
        self.assertEqual(members.data, [])

    def test_add_direct_church_member_to_family(self):
        church = Church.objects.create(organization=self.organization, name='Church', slug='church')
        campus = Campus.objects.create(church=church, name='Campus', slug='campus')
        user = User.objects.create_user(username='member', email='member@example.com', password='Strong-password-123')
        member = Member.objects.create(
            user=user,
            organization=self.organization,
            church=church,
            campus=campus,
            first_name='Jean',
            last_name='Kouassi',
            email='member@example.com',
        )
        family = Family.objects.create(organization=self.organization, name='Famille Kouassi')

        response = self.client.post(f'/api/families/{family.id}/members/', {
            'member': str(member.id),
            'relationship_type': 'HEAD',
            'is_primary_contact': True,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(FamilyMember.objects.filter(family=family, member=member).exists())
        listing = self.client.get(f'/api/families/{family.id}/members/')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data[0]['member_name'], 'Jean Kouassi')
        self.assertEqual(listing.data[0]['email'], 'member@example.com')

    def test_adding_member_twice_returns_validation_error_instead_of_server_error(self):
        church = Church.objects.create(organization=self.organization, name='Church', slug='church')
        user = User.objects.create_user(username='member', email='member@example.com', password='Strong-password-123')
        member = Member.objects.create(
            user=user,
            organization=self.organization,
            church=church,
            first_name='Jean',
            last_name='Kouassi',
            email='member@example.com',
        )
        family = Family.objects.create(organization=self.organization, name='Famille Kouassi')
        payload = {
            'member': str(member.id),
            'relationship_type': 'HEAD',
            'is_primary_contact': True,
        }

        first_response = self.client.post(f'/api/families/{family.id}/members/', payload, format='json')
        duplicate_response = self.client.post(f'/api/families/{family.id}/members/', payload, format='json')

        self.assertEqual(first_response.status_code, 201, first_response.data)
        self.assertEqual(duplicate_response.status_code, 400, duplicate_response.data)
        self.assertEqual(FamilyMember.objects.filter(family=family, member=member).count(), 1)

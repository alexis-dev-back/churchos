from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Campus, Church, Organization, OrganizationMembership
from apps.members.models import Member
from apps.people.models import Person

User = get_user_model()


class PersonApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', email='owner@example.com', password='Strong-password-123')
        self.organization = Organization.objects.create(name='Org', slug='org', owner=self.user)
        OrganizationMembership.objects.create(user=self.user, organization=self.organization, role=OrganizationMembership.Role.OWNER)
        refresh = RefreshToken.for_user(self.user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def test_create_person(self):
        response = self.client.post('/api/persons/', {
            'first_name': 'Marie',
            'last_name': 'Kouassi',
            'email': 'marie@example.com',
            'phone': '+22501020304',
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Person.objects.filter(email='marie@example.com', organization=self.organization).exists())

    def test_people_and_person_details_are_scoped_to_the_active_organization(self):
        other_organization = Organization.objects.create(name='Other Org', slug='other-org')
        foreign_person = Person.objects.create(
            organization=other_organization,
            first_name='Jean',
            last_name='Other',
            email='jean.other@example.com',
        )
        own_person = Person.objects.create(
            organization=self.organization,
            first_name='Marie',
            last_name='Local',
            email='marie.local@example.com',
        )

        listing = self.client.get('/api/people/')
        own_detail = self.client.get(f'/api/people/{own_person.id}/')
        foreign_detail = self.client.get(f'/api/people/{foreign_person.id}/')

        self.assertEqual(listing.status_code, 200)
        self.assertEqual([item['id'] for item in listing.data], [str(own_person.id)])
        self.assertEqual(own_detail.status_code, 200)
        self.assertEqual(foreign_detail.status_code, 404)

    def test_person_creation_does_not_report_duplicates_from_another_organization(self):
        other_organization = Organization.objects.create(name='Other Org', slug='other-org')
        Person.objects.create(
            organization=other_organization,
            first_name='Marie',
            last_name='Kouassi',
            email='marie@example.com',
        )

        response = self.client.post('/api/people/', {
            'first_name': 'Marie',
            'last_name': 'Kouassi',
            'email': 'marie@example.com',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Person.objects.filter(email='marie@example.com').count(), 2)
        self.assertEqual(Person.objects.get(pk=response.data['id']).organization, self.organization)

    def test_convert_person_to_member_creates_user_and_invitation(self):
        church = Church.objects.create(
            organization=self.organization,
            name='Church A',
            slug='church-a',
            pastor_name='Pastor A',
        )
        campus = Campus.objects.create(
            church=church,
            name='Campus A',
            slug='campus-a',
        )
        person = Person.objects.create(
            organization=self.organization,
            first_name='Paul',
            last_name='Kouassi',
            email='paul@example.com',
            phone='+22505060708',
        )

        response = self.client.post(f'/api/persons/{person.id}/create-membership/', {
            'church': str(church.church_id),
            'campus': str(campus.campus_id),
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertIn('invitation_token', response.data)
        self.assertRegex(response.data['invitation_token'], r'^\d{5}$')
        self.assertTrue(Person.objects.filter(pk=person.pk, user__isnull=False).exists())
        self.assertTrue(Member.objects.filter(email='paul@example.com').exists())

    def test_person_created_through_api_can_be_converted(self):
        church = Church.objects.create(organization=self.organization, name='Church B', slug='church-b')
        campus = Campus.objects.create(church=church, name='Campus B', slug='campus-b')
        create_response = self.client.post('/api/people/', {
            'first_name': 'Anne',
            'last_name': 'Kouassi',
            'email': 'anne@example.com',
        }, format='json')

        conversion_response = self.client.post(
            f"/api/persons/{create_response.data['id']}/create-membership/",
            {'church': str(church.church_id), 'campus': str(campus.campus_id)},
            format='json',
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(conversion_response.status_code, 201, conversion_response.data)

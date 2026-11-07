from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Organization, OrganizationMembership
from apps.tasks.models import Task

User = get_user_model()


class TaskApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='pastor', password='Strong-password-123')
        self.other_user = User.objects.create_user(username='other-pastor', password='Strong-password-123')
        self.organization = Organization.objects.create(
            name='Task Org',
            slug='task-org',
            owner=self.user,
        )
        self.other_organization = Organization.objects.create(
            name='Other Task Org',
            slug='other-task-org',
            owner=self.other_user,
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.other_organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        OrganizationMembership.objects.create(
            user=self.other_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        self.authenticate(self.user, self.organization)

    def authenticate(self, user, organization):
        refresh = RefreshToken.for_user(user)
        refresh['tenant_id'] = str(organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def create_task(self, **overrides):
        payload = {
            'organization': self.organization,
            'owner': self.user,
            'title': 'Préparer le sermon',
        }
        payload.update(overrides)
        return Task.objects.create(**payload)

    def test_create_task_assigns_owner_and_organization(self):
        response = self.client.post(
            '/api/tasks/',
            {
                'title': '  Préparer le sermon  ',
                'description': '  Méditation pour dimanche  ',
                'priority': 'HIGH',
                'due_date': (timezone.localdate() + timedelta(days=3)).isoformat(),
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['title'], 'Préparer le sermon')
        self.assertEqual(response.data['description'], 'Méditation pour dimanche')
        self.assertEqual(response.data['status'], Task.Status.TODO)
        self.assertEqual(response.data['status_label'], 'À faire')
        self.assertEqual(response.data['priority'], Task.Priority.HIGH)
        self.assertEqual(response.data['priority_label'], 'Haute')
        self.assertIsNone(response.data['completed_at'])
        self.assertFalse(response.data['is_overdue'])

        task = Task.objects.get(pk=response.data['id'])
        self.assertEqual(task.owner, self.user)
        self.assertEqual(task.organization, self.organization)

    def test_list_is_scoped_to_current_user_and_active_organization(self):
        own_task = self.create_task(title='Ma tâche')
        self.create_task(title='Tâche collègue', owner=self.other_user)
        self.create_task(title='Autre organisation', organization=self.other_organization)

        response = self.client.get('/api/tasks/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item['id'] for item in response.data['results']], [str(own_task.id)])
        self.assertEqual(response.data['summary']['total'], 1)
        self.assertEqual(response.data['summary']['todo'], 1)

    def test_list_filters_and_summary(self):
        overdue = self.create_task(
            title='Appeler la famille',
            due_date=timezone.localdate() - timedelta(days=1),
        )
        in_progress = self.create_task(
            title='Préparer la réunion',
            status=Task.Status.IN_PROGRESS,
            priority=Task.Priority.URGENT,
        )
        done = self.create_task(
            title='Envoyer le message',
            status=Task.Status.DONE,
            completed_at=timezone.now(),
        )

        response = self.client.get('/api/tasks/?status=IN_PROGRESS')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item['id'] for item in response.data['results']], [str(in_progress.id)])
        self.assertEqual(response.data['summary']['total'], 3)
        self.assertEqual(response.data['summary']['done'], 1)
        self.assertEqual(response.data['summary']['overdue'], 1)
        self.assertEqual(response.data['summary']['completion_rate'], 33)

        response = self.client.get('/api/tasks/?search=message')
        self.assertEqual([item['id'] for item in response.data['results']], [str(done.id)])

        response = self.client.get('/api/tasks/?overdue=1')
        self.assertEqual([item['id'] for item in response.data['results']], [str(overdue.id)])

        response = self.client.get('/api/tasks/?priority=URGENT')
        self.assertEqual([item['id'] for item in response.data['results']], [str(in_progress.id)])

    def test_updating_status_manages_completion_timestamp(self):
        task = self.create_task()

        response = self.client.patch(
            f'/api/tasks/{task.id}/',
            {'status': Task.Status.DONE},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data['completed_at'])

        response = self.client.patch(
            f'/api/tasks/{task.id}/',
            {'title': 'Préparer le sermon de dimanche'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data['completed_at'])

        response = self.client.patch(
            f'/api/tasks/{task.id}/',
            {'status': Task.Status.IN_PROGRESS},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['completed_at'])

    def test_task_is_overdue_until_completed_or_cancelled(self):
        task = self.create_task(due_date=timezone.localdate() - timedelta(days=2))

        response = self.client.get(f'/api/tasks/{task.id}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['is_overdue'])

        self.client.patch(f'/api/tasks/{task.id}/', {'status': Task.Status.CANCELLED}, format='json')
        response = self.client.get(f'/api/tasks/{task.id}/')
        self.assertFalse(response.data['is_overdue'])

    def test_delete_task(self):
        task = self.create_task()

        response = self.client.delete(f'/api/tasks/{task.id}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Task.objects.filter(pk=task.id).exists())

    def test_cannot_access_another_users_task(self):
        task = self.create_task(owner=self.other_user, title='Tâche privée')

        self.assertEqual(self.client.get(f'/api/tasks/{task.id}/').status_code, 404)
        self.assertEqual(
            self.client.patch(f'/api/tasks/{task.id}/', {'title': 'Modifiée'}, format='json').status_code,
            404,
        )
        self.assertEqual(self.client.delete(f'/api/tasks/{task.id}/').status_code, 404)

    def test_requires_non_empty_title(self):
        response = self.client.post('/api/tasks/', {'title': '   '}, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('title', response.data)

    def test_requires_authentication(self):
        self.client.credentials()

        response = self.client.get('/api/tasks/')

        self.assertEqual(response.status_code, 401)

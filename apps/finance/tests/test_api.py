from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import (
    Church,
    Organization,
    OrganizationMembership,
)
from apps.finance.models import (
    FinanceAuditLog,
    FinanceAccount,
    FinanceAccountMovement,
    FinanceCategory,
    FinanceChurchAccess,
    FinancePayment,
    FinanceRole,
    FinanceTransaction,
    FinancialBudget,
    FinancialConfiguration,
    FinancialConfigurationVersion,
)
from apps.members.models import Member


User = get_user_model()


class FinanceApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='finance-admin', password='Strong-password-123')
        self.organization = Organization.objects.create(
            name='Finance Org',
            slug='finance-org',
            owner=self.admin,
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.church = Church.objects.create(
            organization=self.organization,
            name='Finance Church',
            slug='finance-church',
        )
        self.other_church = Church.objects.create(
            organization=self.organization,
            name='Other Finance Church',
            slug='other-finance-church',
        )
        self.admin_member = self.create_member('finance-admin-member', self.church)
        self.financial_user = self.create_member('finance-user', self.church)
        self.outside_church_member = self.create_member('other-church-member', self.other_church)
        self.authenticate(self.admin)

    def create_member(self, username, church):
        user = User.objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='Strong-password-123',
        )
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        return Member.objects.create(
            user=user,
            organization=self.organization,
            church=church,
            first_name=username,
            last_name='Test',
            email=f'{username}@example.com',
            status=Member.Status.ACTIVE,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def create_configuration(self, church=None):
        church = church or self.church
        response = self.client.post(
            f'/api/finance/churches/{church.church_id}/configuration/',
            {'level': 2, 'base_currency': 'XOF', 'reason': 'Initialisation validée'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def assign_role(self, member=None, church=None, role=FinanceRole.TREASURER):
        return self.client.post(
            '/api/finance/access/',
            {
                'church_id': str((church or self.church).church_id),
                'member_id': str((member or self.financial_user).pk),
                'role': role,
            },
            format='json',
        )

    def test_admin_initializes_configuration_and_change_creates_version_and_audit(self):
        created = self.create_configuration()
        self.assertEqual(created['version'], 1)
        self.assertEqual(FinancialConfigurationVersion.objects.filter(configuration__church=self.church).count(), 1)
        categories = self.client.get(
            f'/api/finance/categories/?church_id={self.church.church_id}'
        )
        self.assertEqual(categories.status_code, 200, categories.data)
        self.assertEqual(len(categories.data), 6)
        self.assertTrue(all(category['is_active'] for category in categories.data))

        updated = self.client.patch(
            f'/api/finance/churches/{self.church.church_id}/configuration/',
            {'level': 3, 'reason': 'Passage à la comptabilité avancée'},
            format='json',
        )

        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['version'], 2)
        self.assertEqual(updated.data['level'], 3)
        self.assertEqual(FinancialConfigurationVersion.objects.filter(configuration__church=self.church).count(), 2)
        self.assertEqual(FinanceAuditLog.objects.filter(church=self.church).count(), 2)

    def test_access_grant_is_scoped_to_active_member_of_selected_church(self):
        invalid = self.assign_role(member=self.outside_church_member)
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(FinanceChurchAccess.objects.exists())

        granted = self.assign_role()
        self.assertEqual(granted.status_code, 201, granted.data)
        self.assertEqual(granted.data['member_id'], str(self.financial_user.pk))
        self.assertEqual(granted.data['role'], FinanceRole.TREASURER)

    def test_financial_user_reads_only_assigned_church_and_can_create_period(self):
        self.create_configuration()
        granted = self.assign_role()
        self.assertEqual(granted.status_code, 201, granted.data)
        self.authenticate(self.financial_user.user)

        context = self.client.get('/api/finance/context/')
        other_configuration = self.client.get(
            f'/api/finance/churches/{self.other_church.church_id}/configuration/'
        )
        other_journal = self.client.get(
            f'/api/finance/cash-journal/?church_id={self.other_church.church_id}'
        )
        period = self.client.post(
            '/api/finance/periods/',
            {
                'church_id': str(self.church.church_id),
                'name': 'Exercice 2026',
                'starts_on': date.today().isoformat(),
                'ends_on': (date.today() + timedelta(days=364)).isoformat(),
            },
            format='json',
        )

        self.assertEqual(context.status_code, 200, context.data)
        self.assertEqual(len(context.data['churches']), 1)
        self.assertEqual(other_configuration.status_code, 403)
        self.assertEqual(other_journal.status_code, 403)
        self.assertEqual(period.status_code, 201, period.data)

    def test_custom_finance_role_is_listed_in_finance_context(self):
        role_response = self.client.post(
            '/api/configuration/roles/',
            {
                'name': 'Responsable des finances',
                'code': 'responsable-finances',
                'description': 'Gestion complète des finances',
            },
            format='json',
        )
        self.assertEqual(role_response.status_code, 201, role_response.data)
        finance_codes = [
            'finance.read',
            'finance.income.create',
            'finance.expense.create',
            'finance.expense.approve',
            'finance.payment.confirm',
            'finance.budget.manage',
            'finance.report.read',
        ]
        permission_response = self.client.post(
            f"/api/configuration/roles/{role_response.data['id']}/permissions/",
            {'permission_codes': finance_codes},
            format='json',
        )
        self.assertEqual(permission_response.status_code, 200, permission_response.data)
        assignment_response = self.client.post(
            '/api/configuration/user-roles/',
            {
                'membership_id': self.financial_user.user.organization_memberships.get(
                    organization=self.organization,
                ).pk,
                'role_id': role_response.data['id'],
                'scope': 'CHURCH',
                'church_id': str(self.church.church_id),
            },
            format='json',
        )
        self.assertEqual(assignment_response.status_code, 201, assignment_response.data)

        self.authenticate(self.financial_user.user)
        context = self.client.get('/api/finance/context/')

        self.assertEqual(context.status_code, 200, context.data)
        self.assertEqual(len(context.data['churches']), 1)
        self.assertEqual(context.data['churches'][0]['church_id'], str(self.church.church_id))
        for church in context.data['churches']:
            self.assertCountEqual(church['permission_codes'], finance_codes)

    def test_non_admin_cannot_assign_finance_roles_or_change_configuration(self):
        self.create_configuration()
        self.authenticate(self.financial_user.user)

        access_response = self.assign_role(member=self.admin_member, role=FinanceRole.FINANCE_ADMIN)
        configuration_response = self.client.patch(
            f'/api/finance/churches/{self.church.church_id}/configuration/',
            {'level': 1, 'reason': 'Tentative non autorisée'},
            format='json',
        )

        self.assertEqual(access_response.status_code, 403)
        self.assertEqual(configuration_response.status_code, 403)
        self.assertEqual(FinancialConfiguration.objects.get(church=self.church).level, 2)

    def test_audit_entries_cannot_be_edited_or_deleted(self):
        self.create_configuration()
        audit = FinanceAuditLog.objects.get(church=self.church)

        with self.assertRaises(ValidationError):
            audit.save()
        with self.assertRaises(ValidationError):
            audit.delete()
        with self.assertRaises(ValidationError):
            FinanceAuditLog.objects.filter(pk=audit.pk).update(action='TAMPERED')
        with self.assertRaises(ValidationError):
            FinanceAuditLog.objects.filter(pk=audit.pk).delete()

    def test_confirmed_donation_updates_account_ledger_and_summary(self):
        self.create_configuration()
        self.assign_role(role=FinanceRole.REQUESTER)
        period_response = self.client.post(
            '/api/finance/periods/',
            {
                'church_id': str(self.church.church_id),
                'name': 'Mois octobre',
                'starts_on': date.today().replace(day=1).isoformat(),
                'ends_on': date.today().isoformat(),
            },
            format='json',
        )
        self.assertEqual(period_response.status_code, 201, period_response.data)
        income_category = FinanceCategory.objects.get(church=self.church, code='offrande')
        self.authenticate(self.financial_user.user)
        income = self.client.post(
            '/api/finance/transactions/',
            {
                'church_id': str(self.church.church_id),
                'kind': FinanceTransaction.Kind.INCOME,
                'period_id': period_response.data['id'],
                'category_id': str(income_category.pk),
                'amount': '12500.00',
                'currency': 'XOF',
                'transaction_date': date.today().isoformat(),
                'counterparty_name': 'Contributeur externe',
                'reference': 'DON-001',
            },
            format='json',
        )
        self.assertEqual(income.status_code, 201, income.data)
        self.assertEqual(income.data['status'], FinanceTransaction.Status.TO_VERIFY)
        self.assertEqual(FinanceAccountMovement.objects.count(), 0)
        duplicate = self.client.post(
            '/api/finance/transactions/',
            {
                'church_id': str(self.church.church_id),
                'kind': FinanceTransaction.Kind.INCOME,
                'period_id': period_response.data['id'],
                'category_id': str(income_category.pk),
                'amount': '12500.00',
                'currency': 'XOF',
                'transaction_date': date.today().isoformat(),
                'counterparty_name': 'Contributeur externe',
                'reference': 'DON-001',
            },
            format='json',
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        account = FinanceAccount.objects.get(church=self.church)
        self.authenticate(self.admin)
        incompatible_method = self.client.post(
            f"/api/finance/transactions/{income.data['id']}/actions/confirm/",
            {
                'account_id': str(account.pk),
                'amount': '12500.00',
                'method': FinancePayment.Method.BANK_TRANSFER,
            },
            format='json',
        )
        self.assertEqual(incompatible_method.status_code, 400, incompatible_method.data)
        self.assertEqual(FinanceAccountMovement.objects.count(), 0)
        confirmed = self.client.post(
            f"/api/finance/transactions/{income.data['id']}/actions/confirm/",
            {
                'account_id': str(account.pk),
                'amount': '12500.00',
                'method': FinancePayment.Method.CASH,
                'reference': 'RECU-001',
            },
            format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data['status'], FinanceTransaction.Status.PAID)
        self.assertEqual(FinanceAccountMovement.objects.get().balance_after, 12500)
        summary = self.client.get(f'/api/finance/summary/?church_id={self.church.church_id}')
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(summary.data['income_total'], '12500')
        self.assertEqual(summary.data['available_balance'], '12500.00')
        report = self.client.get(
            f'/api/finance/reports/?church_id={self.church.church_id}'
            f'&start_date={date.today().isoformat()}&end_date={date.today().isoformat()}'
            f'&period_id={period_response.data["id"]}'
        )
        journal = self.client.get(
            f'/api/finance/cash-journal/?church_id={self.church.church_id}'
            f'&start_date={date.today().isoformat()}&end_date={date.today().isoformat()}'
            f'&account_id={account.pk}'
        )
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data['income_total'], '12500')
        self.assertEqual(report.data['net_total'], '12500')
        self.assertEqual(report.data['income_by_category'][0]['category_name'], income_category.name)
        self.assertEqual(journal.status_code, 200, journal.data)
        self.assertEqual(journal.data['count'], 1)
        self.assertEqual(journal.data['total_in'], [{'currency': 'XOF', 'amount': '12500.00'}])
        self.assertEqual(journal.data['closing_totals'][0]['balance'], '12500.00')
        self.assertEqual(journal.data['results'][0]['payment_reference'], 'RECU-001')

    def test_expense_requires_separate_approval_and_cashier_confirmation(self):
        self.create_configuration()
        self.assign_role(role=FinanceRole.REQUESTER)
        self.assign_role(role=FinanceRole.TREASURER)
        period_response = self.client.post(
            '/api/finance/periods/',
            {
                'church_id': str(self.church.church_id),
                'name': 'Période dépenses',
                'starts_on': date.today().replace(day=1).isoformat(),
                'ends_on': date.today().isoformat(),
            },
            format='json',
        )
        self.assertEqual(period_response.status_code, 201, period_response.data)
        expense_category = FinanceCategory.objects.get(church=self.church, code='fonctionnement')
        account = FinanceAccount.objects.get(church=self.church)
        account.opening_balance = 50000
        account.save(update_fields=['opening_balance'])
        budget_response = self.client.post(
            '/api/finance/budgets/',
            {
                'church_id': str(self.church.church_id),
                'period_id': period_response.data['id'],
                'category_id': str(expense_category.pk),
                'name': 'Budget fonctionnement',
                'allocated_amount': '30000.00',
                'currency': 'XOF',
            },
            format='json',
        )
        self.assertEqual(budget_response.status_code, 201, budget_response.data)
        self.authenticate(self.financial_user.user)
        expense = self.client.post(
            '/api/finance/transactions/',
            {
                'church_id': str(self.church.church_id),
                'kind': FinanceTransaction.Kind.EXPENSE,
                'period_id': period_response.data['id'],
                'category_id': str(expense_category.pk),
                'amount': '20000.00',
                'currency': 'XOF',
                'transaction_date': date.today().isoformat(),
                'counterparty_name': 'Fournisseur A',
                'description': 'Fournitures',
            },
            format='json',
        )
        self.assertEqual(expense.status_code, 201, expense.data)
        self.assertEqual(expense.data['status'], FinanceTransaction.Status.PENDING_APPROVAL)
        self.authenticate(self.admin)
        approved = self.client.post(
            f"/api/finance/transactions/{expense.data['id']}/actions/approve/",
            {'reason': 'Dépense budgétée'},
            format='json',
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.authenticate(self.financial_user.user)
        pending_payment = self.client.post(
            f"/api/finance/transactions/{expense.data['id']}/payments/",
            {
                'account_id': str(account.pk),
                'amount': '12000.00',
                'method': FinancePayment.Method.CASH,
            },
            format='json',
        )
        self.assertEqual(pending_payment.status_code, 201, pending_payment.data)
        self.authenticate(self.admin)
        conflict = self.client.post(
            f"/api/finance/payments/{pending_payment.data['id']}/actions/",
            {'confirmed': True},
            format='json',
        )
        self.assertEqual(conflict.status_code, 403)
        treasurer = User.objects.create_user(username='separate-treasurer', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=treasurer,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        treasurer_member = Member.objects.create(
            user=treasurer,
            organization=self.organization,
            church=self.church,
            first_name='Separate',
            last_name='Treasurer',
            email='separate-treasurer@example.com',
            status=Member.Status.ACTIVE,
        )
        FinanceChurchAccess.objects.create(
            organization=self.organization,
            church=self.church,
            member=treasurer_member,
            role=FinanceRole.TREASURER,
            granted_by=self.admin,
        )
        self.authenticate(treasurer)
        paid = self.client.post(
            f"/api/finance/payments/{pending_payment.data['id']}/actions/",
            {'confirmed': True},
            format='json',
        )
        self.assertEqual(paid.status_code, 200, paid.data)
        transaction_record = FinanceTransaction.objects.get(pk=expense.data['id'])
        self.assertEqual(transaction_record.status, FinanceTransaction.Status.PARTIALLY_PAID)
        self.assertEqual(FinanceAccountMovement.objects.get().direction, FinanceAccountMovement.Direction.OUT)
        self.authenticate(self.admin)
        overspend = self.client.post(
            f"/api/finance/transactions/{expense.data['id']}/actions/approve/",
            {'reason': 'Already decided'},
            format='json',
        )
        self.assertEqual(overspend.status_code, 400)

    def test_expense_approval_rejects_budget_overrun(self):
        self.create_configuration()
        period_response = self.client.post(
            '/api/finance/periods/',
            {
                'church_id': str(self.church.church_id),
                'name': 'Budget test',
                'starts_on': date.today().replace(day=1).isoformat(),
                'ends_on': date.today().isoformat(),
            },
            format='json',
        )
        category = FinanceCategory.objects.get(church=self.church, code='fonctionnement')
        FinancialBudget.objects.create(
            organization=self.organization,
            church=self.church,
            period_id=period_response.data['id'],
            category=category,
            name='Plafond',
            allocated_amount=100,
            currency='XOF',
            created_by=self.admin,
        )
        transaction_record = FinanceTransaction.objects.create(
            organization=self.organization,
            church=self.church,
            period_id=period_response.data['id'],
            kind=FinanceTransaction.Kind.EXPENSE,
            status=FinanceTransaction.Status.PENDING_APPROVAL,
            category=category,
            amount=101,
            currency='XOF',
            transaction_date=date.today(),
            created_by=self.financial_user.user,
        )
        response = self.client.post(
            f'/api/finance/transactions/{transaction_record.pk}/actions/approve/',
            {'reason': 'Test plafond'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        transaction_record.refresh_from_db()
        self.assertEqual(transaction_record.status, FinanceTransaction.Status.PENDING_APPROVAL)

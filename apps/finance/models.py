import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class FinanceAuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Les entrées du journal financier sont immuables.')

    def delete(self):
        raise ValidationError('Les entrées du journal financier ne peuvent pas être supprimées.')


class ImmutableLedgerQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Les écritures financières validées sont immuables.')

    def delete(self):
        raise ValidationError('Les écritures financières ne peuvent pas être supprimées.')


class FinanceRole(models.TextChoices):
    FINANCE_ADMIN = 'FINANCE_ADMIN', 'Administrateur financier'
    REQUESTER = 'REQUESTER', 'Demandeur'
    APPROVER = 'APPROVER', 'Approbateur'
    TREASURER = 'TREASURER', 'Trésorier / payeur'
    ACCOUNTANT = 'ACCOUNTANT', 'Comptable'
    CONTROLLER = 'CONTROLLER', 'Contrôleur'
    AUDITOR = 'AUDITOR', 'Auditeur en lecture seule'
    CONTRIBUTION_READER = 'CONTRIBUTION_READER', 'Consultation des contributions'


class FinanceCategory(models.Model):
    class Kind(models.TextChoices):
        INCOME = 'INCOME', 'Recette'
        EXPENSE = 'EXPENSE', 'Dépense'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='finance_categories')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, related_name='finance_categories')
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=40)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    requires_approval = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='finance_categories_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['kind', 'name']
        constraints = [
            models.UniqueConstraint(fields=['church', 'code'], name='finance_category_code_per_church'),
            models.CheckConstraint(condition=Q(kind__in=['INCOME', 'EXPENSE']), name='finance_category_kind_valid'),
        ]


class FinanceFund(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='finance_funds')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, related_name='finance_funds')
    name = models.CharField(max_length=120)
    purpose = models.CharField(max_length=500, blank=True)
    is_restricted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='finance_funds_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['church', 'name'], name='finance_fund_name_per_church'),
        ]


class FinanceAccount(models.Model):
    class Kind(models.TextChoices):
        CASH = 'CASH', 'Caisse espèces'
        BANK = 'BANK', 'Compte bancaire'
        MOBILE_MONEY = 'MOBILE_MONEY', 'Mobile Money'
        OTHER = 'OTHER', 'Autre'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='finance_accounts')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, related_name='finance_accounts')
    campus = models.ForeignKey(
        'core.Campus',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='finance_accounts',
    )
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    currency = models.CharField(max_length=3, default='XOF')
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='finance_accounts_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['church', 'name'], name='finance_account_name_per_church'),
            models.CheckConstraint(condition=Q(opening_balance__gte=0), name='finance_account_opening_balance_nonnegative'),
        ]


class FinanceTransaction(models.Model):
    class Kind(models.TextChoices):
        INCOME = 'INCOME', 'Recette'
        EXPENSE = 'EXPENSE', 'Dépense'

    class Status(models.TextChoices):
        TO_VERIFY = 'TO_VERIFY', 'À vérifier'
        PENDING_APPROVAL = 'PENDING_APPROVAL', 'En attente d’approbation'
        APPROVED = 'APPROVED', 'Approuvée'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partiellement payée'
        PAID = 'PAID', 'Payée / confirmée'
        REJECTED = 'REJECTED', 'Rejetée'
        CANCELLED = 'CANCELLED', 'Annulée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='finance_transactions')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, related_name='finance_transactions')
    period = models.ForeignKey('FinancialPeriod', on_delete=models.PROTECT, related_name='transactions')
    kind = models.CharField(max_length=8, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices)
    category = models.ForeignKey(FinanceCategory, on_delete=models.PROTECT, related_name='transactions')
    fund = models.ForeignKey(FinanceFund, on_delete=models.PROTECT, null=True, blank=True, related_name='transactions')
    campus = models.ForeignKey('core.Campus', on_delete=models.PROTECT, null=True, blank=True, related_name='finance_transactions')
    member = models.ForeignKey('members.Member', on_delete=models.PROTECT, null=True, blank=True, related_name='finance_transactions')
    counterparty_name = models.CharField(max_length=160, blank=True)
    is_anonymous = models.BooleanField(default=False)
    description = models.CharField(max_length=500, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    exchange_rate_to_base = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal('1'))
    transaction_date = models.DateField()
    reference = models.CharField(max_length=80, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='finance_transactions_created')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='finance_transactions_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-transaction_date', '-created_at']
        indexes = [
            models.Index(fields=['organization', 'church', 'kind', 'status']),
            models.Index(fields=['church', 'transaction_date']),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name='finance_transaction_amount_positive'),
            models.CheckConstraint(condition=Q(exchange_rate_to_base__gt=0), name='finance_transaction_exchange_rate_positive'),
            models.UniqueConstraint(
                fields=['church', 'kind', 'reference'],
                condition=~Q(reference=''),
                name='finance_transaction_reference_unique',
            ),
        ]


class FinancePayment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'En attente'
        CONFIRMED = 'CONFIRMED', 'Confirmé'
        REJECTED = 'REJECTED', 'Rejeté'
        CANCELLED = 'CANCELLED', 'Annulé'

    class Method(models.TextChoices):
        CASH = 'CASH', 'Espèces'
        BANK_TRANSFER = 'BANK_TRANSFER', 'Virement'
        MOBILE_MONEY = 'MOBILE_MONEY', 'Mobile Money'
        CHEQUE = 'CHEQUE', 'Chèque'
        ONLINE = 'ONLINE', 'Paiement en ligne'
        CARD = 'CARD', 'Carte'
        OTHER = 'OTHER', 'Autre'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.ForeignKey(FinanceTransaction, on_delete=models.PROTECT, related_name='payments')
    account = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reference = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='finance_payments_created')
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='finance_payments_confirmed',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name='finance_payment_amount_positive'),
            models.UniqueConstraint(fields=['transaction', 'reference'], condition=~Q(reference=''), name='finance_payment_reference_unique'),
        ]


class FinanceAccountMovement(models.Model):
    class Direction(models.TextChoices):
        IN = 'IN', 'Entrée'
        OUT = 'OUT', 'Sortie'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='finance_account_movements')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, related_name='finance_account_movements')
    account = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='movements')
    payment = models.OneToOneField(FinancePayment, on_delete=models.PROTECT, related_name='movement')
    direction = models.CharField(max_length=3, choices=Direction.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'church', 'account', 'created_at'])]

    objects = ImmutableLedgerQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Les mouvements de trésorerie sont immuables.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Les mouvements de trésorerie ne peuvent pas être supprimés.')


class FinancialBudget(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='financial_budgets')
    church = models.ForeignKey('core.Church', on_delete=models.PROTECT, related_name='financial_budgets')
    period = models.ForeignKey('FinancialPeriod', on_delete=models.PROTECT, related_name='budgets')
    category = models.ForeignKey(FinanceCategory, on_delete=models.PROTECT, related_name='budgets')
    campus = models.ForeignKey('core.Campus', on_delete=models.PROTECT, null=True, blank=True, related_name='financial_budgets')
    name = models.CharField(max_length=160)
    allocated_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='financial_budgets_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['period', 'category', 'campus'], name='finance_budget_period_category_campus'),
            models.CheckConstraint(condition=Q(allocated_amount__gt=0), name='finance_budget_amount_positive'),
        ]


class FinancialBudgetRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(FinancialBudget, on_delete=models.PROTECT, related_name='revisions')
    previous_amount = models.DecimalField(max_digits=14, decimal_places=2)
    new_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=500)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='financial_budget_revisions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class FinancialConfiguration(models.Model):
    class Level(models.IntegerChoices):
        TREASURY = 1, 'Trésorerie simple'
        INTERMEDIATE = 2, 'Gestion intermédiaire'
        ACCOUNTING = 3, 'Comptabilité avancée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='financial_configurations',
    )
    church = models.OneToOneField(
        'core.Church',
        on_delete=models.PROTECT,
        related_name='financial_configuration',
    )
    level = models.PositiveSmallIntegerField(choices=Level.choices)
    base_currency = models.CharField(max_length=3, default='XOF')
    version = models.PositiveIntegerField(default=1)
    is_suspended = models.BooleanField(default=False)
    suspension_reason = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='financial_configurations_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='financial_configurations_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['organization', 'church'])]


class FinancialConfigurationVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    configuration = models.ForeignKey(
        FinancialConfiguration,
        on_delete=models.PROTECT,
        related_name='versions',
    )
    version = models.PositiveIntegerField()
    level = models.PositiveSmallIntegerField(choices=FinancialConfiguration.Level.choices)
    base_currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=500)
    effective_at = models.DateTimeField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='financial_configuration_versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['configuration_id', 'version']
        constraints = [
            models.UniqueConstraint(
                fields=['configuration', 'version'],
                name='finance_config_version_unique',
            ),
        ]


class FinanceChurchAccess(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='finance_church_access',
    )
    church = models.ForeignKey(
        'core.Church',
        on_delete=models.PROTECT,
        related_name='finance_access',
    )
    member = models.ForeignKey(
        'members.Member',
        on_delete=models.PROTECT,
        related_name='finance_access_assignments',
    )
    role = models.CharField(max_length=24, choices=FinanceRole.choices)
    is_active = models.BooleanField(default=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='finance_access_granted',
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='finance_access_revoked',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['member__last_name', 'member__first_name', 'role']
        constraints = [
            models.UniqueConstraint(
                fields=['church', 'member', 'role'],
                name='finance_access_church_member_role_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'church', 'is_active']),
            models.Index(fields=['member', 'is_active']),
        ]


class FinancialPeriod(models.Model):
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Ouverte'
        CLOSED = 'CLOSED', 'Clôturée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='financial_periods',
    )
    church = models.ForeignKey(
        'core.Church',
        on_delete=models.PROTECT,
        related_name='financial_periods',
    )
    name = models.CharField(max_length=100)
    starts_on = models.DateField()
    ends_on = models.DateField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='financial_periods_created',
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='financial_periods_closed',
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-starts_on', 'name']
        constraints = [
            models.UniqueConstraint(fields=['church', 'name'], name='finance_period_name_per_church'),
            models.CheckConstraint(
                condition=Q(ends_on__gte=F('starts_on')),
                name='finance_period_end_after_start',
            ),
        ]
        indexes = [models.Index(fields=['organization', 'church', 'starts_on'])]


class FinanceAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='finance_audit_logs',
    )
    church = models.ForeignKey(
        'core.Church',
        on_delete=models.PROTECT,
        related_name='finance_audit_logs',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='finance_audit_logs',
    )
    action = models.CharField(max_length=60)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'church', 'created_at']),
            models.Index(fields=['object_type', 'object_id']),
        ]

    objects = FinanceAuditLogQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Les entrées du journal financier sont immuables.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Les entrées du journal financier ne peuvent pas être supprimées.')

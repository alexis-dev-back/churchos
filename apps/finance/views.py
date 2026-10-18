from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import AppPermission, Campus, Church
from apps.finance.models import (
    FinanceAuditLog,
    FinanceAccount,
    FinanceAccountMovement,
    FinanceCategory,
    FinanceChurchAccess,
    FinanceFund,
    FinancePayment,
    FinanceRole,
    FinanceTransaction,
    FinancialBudget,
    FinancialBudgetRevision,
    FinancialConfiguration,
    FinancialConfigurationVersion,
    FinancialPeriod,
)
from apps.finance.permissions import (
    FINANCE_READ_ROLES,
    finance_accesses,
    has_finance_role,
    is_finance_organization_admin,
)
from apps.core.services.configuration_access import has_configuration_permission
from apps.finance.serializers import (
    FinanceAccountSerializer,
    FinanceCashJournalFilterSerializer,
    FinanceCategorySerializer,
    FinanceChurchAccessCreateSerializer,
    FinanceChurchAccessSerializer,
    FinanceFundSerializer,
    FinancePaymentConfirmSerializer,
    FinancePaymentCreateSerializer,
    FinancePaymentSerializer,
    FinanceReportFilterSerializer,
    FinanceTransactionCreateSerializer,
    FinanceTransactionSerializer,
    FinancialBudgetCreateSerializer,
    FinancialBudgetRevisionCreateSerializer,
    FinancialBudgetRevisionSerializer,
    FinancialBudgetSerializer,
    FinancialConfigurationChangeSerializer,
    FinancialConfigurationCreateSerializer,
    FinancialConfigurationSerializer,
    FinancialConfigurationVersionSerializer,
    FinancialPeriodCreateSerializer,
    FinancialPeriodSerializer,
)
from apps.members.models import Member
from infrastructure.tenancy import TenantContext


def _organization():
    organization = TenantContext.get()
    if organization is None:
        raise PermissionDenied('Aucune organisation active dans le contexte de la requête.')
    return organization


def _can_read_church(request, church):
    organization = _organization()
    return (
        is_finance_organization_admin(request.user, organization)
        or finance_accesses(request.user, organization).filter(church=church, role__in=FINANCE_READ_ROLES).exists()
        or has_configuration_permission(request.user, 'finance.read', organization, church=church)
    )


def _record_audit(organization, church, actor, action, object_type, object_id, detail=None):
    FinanceAuditLog.objects.create(
        organization=organization,
        church=church,
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        detail=detail or {},
    )


def _assert_finance_write_enabled(organization, church):
    configuration = FinancialConfiguration.objects.filter(organization=organization, church=church).first()
    if configuration is None:
        raise ValidationError({'church_id': 'La configuration financière doit être initialisée avant les opérations.'})
    if configuration.is_suspended:
        raise ValidationError({'church_id': 'La gestion financière de cette église est suspendue.'})
    return configuration


def _transaction_queryset(organization, church_id=None):
    queryset = FinanceTransaction.objects.filter(
        organization=organization,
        church__organization=organization,
    ).select_related('category', 'fund', 'campus', 'member', 'period', 'created_by', 'approved_by').annotate(
        paid_amount=Coalesce(
            Sum(
                'payments__amount',
                filter=Q(payments__status=FinancePayment.Status.CONFIRMED),
            ),
            Value(Decimal('0')),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )
    if church_id:
        queryset = queryset.filter(church_id=church_id)
    return queryset


def _can_view_transactions(user, organization, church):
    if is_finance_organization_admin(user, organization):
        return True
    if has_configuration_permission(user, 'finance.read', organization, church=church):
        return True
    if has_configuration_permission(user, 'finance.income.create', organization, church=church):
        return True
    if has_configuration_permission(user, 'finance.expense.create', organization, church=church):
        return True
    if has_configuration_permission(user, 'finance.expense.approve', organization, church=church):
        return True
    if has_configuration_permission(user, 'finance.payment.confirm', organization, church=church):
        return True
    roles = set(finance_accesses(user, organization).filter(church=church).values_list('role', flat=True))
    return bool(roles.intersection({
        FinanceRole.FINANCE_ADMIN,
        FinanceRole.REQUESTER,
        FinanceRole.APPROVER,
        FinanceRole.TREASURER,
        FinanceRole.ACCOUNTANT,
        FinanceRole.CONTROLLER,
        FinanceRole.AUDITOR,
        FinanceRole.CONTRIBUTION_READER,
    }))


def _can_view_contributors(user, organization, church):
    return (
        is_finance_organization_admin(user, organization)
        or has_configuration_permission(user, 'finance.read', organization, church=church)
        or finance_accesses(user, organization).filter(
        church=church,
        role__in={
            FinanceRole.FINANCE_ADMIN,
            FinanceRole.ACCOUNTANT,
            FinanceRole.AUDITOR,
            FinanceRole.CONTRIBUTION_READER,
        },
        ).exists()
    )


def _account_balance(account):
    movements = FinanceAccountMovement.objects.filter(account=account)
    incoming = movements.filter(direction=FinanceAccountMovement.Direction.IN).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    outgoing = movements.filter(direction=FinanceAccountMovement.Direction.OUT).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    return account.opening_balance + incoming - outgoing


def _validate_payment_method(account, method):
    allowed_accounts = {
        FinancePayment.Method.CASH: {FinanceAccount.Kind.CASH},
        FinancePayment.Method.BANK_TRANSFER: {FinanceAccount.Kind.BANK},
        FinancePayment.Method.CHEQUE: {FinanceAccount.Kind.BANK},
        FinancePayment.Method.MOBILE_MONEY: {FinanceAccount.Kind.MOBILE_MONEY},
        FinancePayment.Method.ONLINE: {FinanceAccount.Kind.BANK, FinanceAccount.Kind.OTHER},
        FinancePayment.Method.CARD: {FinanceAccount.Kind.BANK, FinanceAccount.Kind.OTHER},
        FinancePayment.Method.OTHER: set(FinanceAccount.Kind.values),
    }
    if account.kind not in allowed_accounts[method]:
        raise ValidationError({'method': 'Le moyen de paiement ne correspond pas au type de compte sélectionné.'})


class FinanceCatalogListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    catalog = ''

    def get(self, request):
        organization = _organization()
        church_id = request.query_params.get('church_id')
        church = get_object_or_404(Church, church_id=church_id, organization=organization, is_active=True)
        if not _can_read_church(request, church):
            raise PermissionDenied('Vous ne disposez pas d’un accès financier à cette église.')
        if self.catalog == 'categories':
            queryset = FinanceCategory.objects.filter(organization=organization, church=church, is_active=True)
            return Response(FinanceCategorySerializer(queryset, many=True).data)
        if self.catalog == 'funds':
            queryset = FinanceFund.objects.filter(organization=organization, church=church, is_active=True)
            return Response(FinanceFundSerializer(queryset, many=True).data)
        accounts = FinanceAccount.objects.filter(
            organization=organization,
            church=church,
            is_active=True,
        ).select_related('campus')
        return Response(FinanceAccountSerializer(accounts, many=True).data)

    @transaction.atomic
    def post(self, request):
        organization = _organization()
        church = get_object_or_404(
            Church,
            church_id=request.data.get('church_id'),
            organization=organization,
            is_active=True,
        )
        if self.catalog == 'accounts':
            allowed = {FinanceRole.FINANCE_ADMIN, FinanceRole.TREASURER}
        else:
            allowed = {FinanceRole.FINANCE_ADMIN, FinanceRole.ACCOUNTANT}
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                church,
                allowed,
                organization,
                permission_code='finance.budget.manage',
            )
        ):
            raise PermissionDenied('Vous ne disposez pas du rôle requis pour gérer ce référentiel.')
        _assert_finance_write_enabled(organization, church)
        if self.catalog == 'categories':
            serializer = FinanceCategorySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if FinanceCategory.objects.filter(church=church, code=serializer.validated_data['code']).exists():
                raise ValidationError({'code': 'Cette catégorie existe déjà pour cette église.'})
            item = serializer.save(organization=organization, church=church, created_by=request.user)
            response_serializer = FinanceCategorySerializer
            action = 'FINANCE_CATEGORY_CREATED'
        elif self.catalog == 'funds':
            serializer = FinanceFundSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if FinanceFund.objects.filter(church=church, name=serializer.validated_data['name']).exists():
                raise ValidationError({'name': 'Ce fonds existe déjà pour cette église.'})
            item = serializer.save(organization=organization, church=church, created_by=request.user)
            response_serializer = FinanceFundSerializer
            action = 'FINANCE_FUND_CREATED'
        else:
            data = request.data.copy()
            campus_id = data.pop('campus_id', None)
            serializer = FinanceAccountSerializer(data={**data, 'campus': campus_id})
            serializer.is_valid(raise_exception=True)
            if FinanceAccount.objects.filter(church=church, name=serializer.validated_data['name']).exists():
                raise ValidationError({'name': 'Ce compte existe déjà pour cette église.'})
            campus = serializer.validated_data.get('campus')
            if campus and campus.church_id != church.church_id:
                raise ValidationError({'campus_id': 'Le campus doit appartenir à cette église.'})
            item = serializer.save(organization=organization, church=church, created_by=request.user)
            response_serializer = FinanceAccountSerializer
            action = 'FINANCE_ACCOUNT_CREATED'
        audit_detail = {}
        if isinstance(item, FinanceAccount):
            audit_detail = {
                'currency': item.currency,
                'opening_balance': str(item.opening_balance),
                'kind': item.kind,
            }
        _record_audit(organization, church, request.user, action, item.__class__.__name__, item.pk, audit_detail)
        return Response(response_serializer(item).data, status=status.HTTP_201_CREATED)


class FinanceCategoryListCreateView(FinanceCatalogListCreateView):
    catalog = 'categories'


class FinanceFundListCreateView(FinanceCatalogListCreateView):
    catalog = 'funds'


class FinanceAccountListCreateView(FinanceCatalogListCreateView):
    catalog = 'accounts'


class FinanceTransactionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        church_id = request.query_params.get('church_id')
        church = get_object_or_404(Church, church_id=church_id, organization=organization, is_active=True)
        if not _can_view_transactions(request.user, organization, church):
            raise PermissionDenied('Vous ne disposez pas d’un accès aux opérations financières.')
        queryset = _transaction_queryset(organization, church.church_id)
        roles = set(finance_accesses(request.user, organization).filter(church=church).values_list('role', flat=True))
        if not is_finance_organization_admin(request.user, organization) and roles == {FinanceRole.REQUESTER}:
            queryset = queryset.filter(created_by=request.user)
        elif not is_finance_organization_admin(request.user, organization) and not roles and not has_configuration_permission(
            request.user,
            'finance.read',
            organization,
            church=church,
        ):
            can_create = (
                has_configuration_permission(request.user, 'finance.income.create', organization, church=church)
                or has_configuration_permission(request.user, 'finance.expense.create', organization, church=church)
            )
            if can_create:
                queryset = queryset.filter(created_by=request.user)
            elif has_configuration_permission(request.user, 'finance.expense.approve', organization, church=church):
                queryset = queryset.filter(
                    kind=FinanceTransaction.Kind.EXPENSE,
                    status=FinanceTransaction.Status.PENDING_APPROVAL,
                )
            elif has_configuration_permission(request.user, 'finance.payment.confirm', organization, church=church):
                queryset = queryset.filter(
                    status__in=[FinanceTransaction.Status.APPROVED, FinanceTransaction.Status.PARTIALLY_PAID],
                )
        kind = request.query_params.get('kind')
        state = request.query_params.get('status')
        if kind:
            queryset = queryset.filter(kind=kind)
        if state:
            queryset = queryset.filter(status=state)
        limit = min(max(int(request.query_params.get('limit', '100')), 1), 200)
        serializer = FinanceTransactionSerializer(
            queryset[:limit],
            many=True,
            context={'can_view_contributors': _can_view_contributors(request.user, organization, church)},
        )
        return Response(serializer.data)

    @transaction.atomic
    def post(self, request):
        organization = _organization()
        church_id = request.data.get('church_id')
        church = get_object_or_404(Church, church_id=church_id, organization=organization, is_active=True)
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.REQUESTER, FinanceRole.TREASURER},
                organization,
                permission_code=(
                    'finance.income.create'
                    if request.data.get('kind') == FinanceTransaction.Kind.INCOME
                    else 'finance.expense.create'
                ),
            )
        ):
            raise PermissionDenied('Vous ne disposez pas du rôle requis pour enregistrer une opération.')
        configuration = _assert_finance_write_enabled(organization, church)
        serializer = FinanceTransactionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if (
            values['currency'] == configuration.base_currency
            and values['exchange_rate_to_base'] != Decimal('1')
        ):
            raise ValidationError({
                'exchange_rate_to_base': 'Le taux de conversion doit être égal à 1 lorsque la devise est celle de base.'
            })
        period = get_object_or_404(
            FinancialPeriod,
            pk=values['period_id'],
            organization=organization,
            church=church,
            status=FinancialPeriod.Status.OPEN,
        )
        category = get_object_or_404(
            FinanceCategory,
            pk=values['category_id'],
            organization=organization,
            church=church,
            is_active=True,
        )
        if (values['kind'] == FinanceTransaction.Kind.INCOME) != (category.kind == FinanceCategory.Kind.INCOME):
            raise ValidationError({'category_id': 'La catégorie ne correspond pas au type de transaction.'})
        reference = values.get('reference', '').strip()
        if reference and FinanceTransaction.objects.filter(
            church=church,
            kind=values['kind'],
            reference=reference,
        ).exists():
            raise ValidationError({'reference': 'Cette référence est déjà utilisée par une opération de même nature.'})
        fund = get_object_or_404(
            FinanceFund,
            pk=values['fund_id'],
            organization=organization,
            church=church,
            is_active=True,
        ) if values.get('fund_id') else None
        campus = get_object_or_404(
            Campus,
            pk=values['campus_id'],
            church=church,
            is_active=True,
        ) if values.get('campus_id') else None
        member = get_object_or_404(
            Member,
            pk=values['member_id'],
            organization=organization,
            church=church,
            status=Member.Status.ACTIVE,
        ) if values.get('member_id') else None
        if values['kind'] == FinanceTransaction.Kind.INCOME and member and not _can_view_contributors(request.user, organization, church):
            raise PermissionDenied('Le rattachement nominatif d’une contribution requiert une permission spécifique.')
        if fund and fund.is_restricted and values['kind'] == FinanceTransaction.Kind.EXPENSE and category.kind != FinanceCategory.Kind.EXPENSE:
            raise ValidationError({'category_id': 'La dépense doit être imputée à une catégorie de dépense.'})
        transaction_status = (
            FinanceTransaction.Status.TO_VERIFY
            if values['kind'] == FinanceTransaction.Kind.INCOME
            else FinanceTransaction.Status.PENDING_APPROVAL
        )
        item = FinanceTransaction.objects.create(
            organization=organization,
            church=church,
            period=period,
            kind=values['kind'],
            status=transaction_status,
            category=category,
            fund=fund,
            campus=campus,
            member=member,
            counterparty_name='' if values.get('is_anonymous') else values.get('counterparty_name', ''),
            is_anonymous=values.get('is_anonymous', False),
            description=values.get('description', ''),
            amount=values['amount'],
            currency=values['currency'],
            exchange_rate_to_base=values['exchange_rate_to_base'],
            transaction_date=values['transaction_date'],
            reference=reference,
            created_by=request.user,
        )
        _record_audit(
            organization,
            church,
            request.user,
            'FINANCE_TRANSACTION_CREATED',
            'FinanceTransaction',
            item.pk,
            {
                'kind': item.kind,
                'amount': str(item.amount),
                'currency': item.currency,
                'exchange_rate_to_base': str(item.exchange_rate_to_base),
                'status': item.status,
            },
        )
        item.paid_amount = Decimal('0')
        return Response(
            FinanceTransactionSerializer(
                item,
                context={'can_view_contributors': _can_view_contributors(request.user, organization, church)},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class FinanceTransactionDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, transaction_id, decision):
        organization = _organization()
        item = get_object_or_404(
            FinanceTransaction.objects.select_for_update().select_related('church', 'category'),
            pk=transaction_id,
            organization=organization,
            church__organization=organization,
        )
        _assert_finance_write_enabled(organization, item.church)
        if item.kind == FinanceTransaction.Kind.INCOME and decision == 'confirm':
            return self._confirm_income(request, organization, item)
        if item.kind != FinanceTransaction.Kind.EXPENSE or item.status != FinanceTransaction.Status.PENDING_APPROVAL:
            raise ValidationError({'status': 'Cette opération ne peut pas être décidée dans son état actuel.'})
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                item.church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.APPROVER},
                organization,
                permission_code='finance.expense.approve',
            )
        ):
            raise PermissionDenied('Seul un approbateur financier peut décider de cette dépense.')
        if request.user.pk == item.created_by_id:
            raise PermissionDenied('Le demandeur ne peut pas approuver sa propre dépense.')
        serializer = FinancePaymentConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if decision == 'approve':
            self._validate_budget(item)
            item.status = FinanceTransaction.Status.APPROVED
            item.approved_by = request.user
            item.approved_at = timezone.now()
            item.decision_reason = values.get('reason', '')
            action = 'FINANCE_EXPENSE_APPROVED'
        elif decision == 'reject':
            if not values.get('reason', '').strip():
                raise ValidationError({'reason': 'Un motif est obligatoire pour rejeter une dépense.'})
            item.status = FinanceTransaction.Status.REJECTED
            item.decision_reason = values['reason']
            action = 'FINANCE_EXPENSE_REJECTED'
        else:
            raise ValidationError({'decision': 'Décision financière inconnue.'})
        item.save(update_fields=['status', 'approved_by', 'approved_at', 'decision_reason', 'updated_at'])
        _record_audit(
            organization,
            item.church,
            request.user,
            action,
            'FinanceTransaction',
            item.pk,
            {'reason': item.decision_reason},
        )
        return Response(self._serialize_transaction(request, organization, item))

    def _confirm_income(self, request, organization, item):
        if item.status != FinanceTransaction.Status.TO_VERIFY:
            raise ValidationError({'status': 'Cette recette a déjà été traitée.'})
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                item.church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.TREASURER},
                organization,
                permission_code='finance.payment.confirm',
            )
        ):
            raise PermissionDenied('Seul un trésorier habilité peut confirmer l’encaissement.')
        if request.user.pk == item.created_by_id:
            raise PermissionDenied('La personne ayant déclaré la collecte ne peut pas confirmer son propre encaissement.')
        values = FinancePaymentCreateSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        payment_data = values.validated_data
        account = get_object_or_404(
            FinanceAccount.objects.select_for_update(),
            pk=payment_data['account_id'],
            organization=organization,
            church=item.church,
            is_active=True,
        )
        if account.currency != item.currency:
            raise ValidationError({'account_id': 'La devise de la caisse doit correspondre à celle de la transaction.'})
        _validate_payment_method(account, payment_data['method'])
        payment_reference = payment_data.get('reference', '').strip()
        if payment_reference and item.payments.filter(reference=payment_reference).exists():
            raise ValidationError({'reference': 'Cette référence de paiement est déjà utilisée pour cette opération.'})
        if payment_data['amount'] != item.amount:
            raise ValidationError({'amount': 'Le montant confirmé doit être égal au montant de la recette déclarée.'})
        payment = FinancePayment.objects.create(
            transaction=item,
            account=account,
            amount=payment_data['amount'],
            method=payment_data['method'],
            reference=payment_reference,
            status=FinancePayment.Status.CONFIRMED,
            created_by=request.user,
            confirmed_by=request.user,
            confirmed_at=timezone.now(),
        )
        self._post_account_movement(organization, item, payment, account)
        item.status = FinanceTransaction.Status.PAID
        item.save(update_fields=['status', 'updated_at'])
        _record_audit(
            organization,
            item.church,
            request.user,
            'FINANCE_INCOME_CONFIRMED',
            'FinanceTransaction',
            item.pk,
            {'payment_id': str(payment.pk), 'amount': str(payment.amount), 'account_id': str(account.pk)},
        )
        return Response(self._serialize_transaction(request, organization, item))

    @staticmethod
    def _validate_budget(item):
        budgets = FinancialBudget.objects.select_for_update().filter(
            organization=item.organization,
            church=item.church,
            period=item.period,
            category=item.category,
        ).filter(Q(campus=item.campus) | Q(campus__isnull=True))
        budget = budgets.filter(campus=item.campus).first() or budgets.filter(campus__isnull=True).first()
        if budget is None:
            return
        expenses = FinanceTransaction.objects.filter(
            organization=item.organization,
            church=item.church,
            period=item.period,
            category=item.category,
            status__in=[
                FinanceTransaction.Status.APPROVED,
                FinanceTransaction.Status.PARTIALLY_PAID,
                FinanceTransaction.Status.PAID,
            ],
        ).exclude(pk=item.pk)
        if budget.campus:
            expenses = expenses.filter(campus=budget.campus)
        committed = expenses.annotate(
            base_amount=F('amount') * F('exchange_rate_to_base'),
        ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0')
        candidate = item.amount * item.exchange_rate_to_base
        if committed + candidate > budget.allocated_amount:
            raise ValidationError({
                'amount': f'Le budget « {budget.name} » serait dépassé. Disponible : {budget.allocated_amount - committed}.',
            })

    @staticmethod
    def _post_account_movement(organization, item, payment, account):
        signed_change = payment.amount if item.kind == FinanceTransaction.Kind.INCOME else -payment.amount
        new_balance = _account_balance(account) + signed_change
        if new_balance < 0:
            raise ValidationError({'amount': 'Le solde de la caisse est insuffisant pour confirmer cette sortie.'})
        FinanceAccountMovement.objects.create(
            organization=organization,
            church=item.church,
            account=account,
            payment=payment,
            direction=(
                FinanceAccountMovement.Direction.IN
                if signed_change > 0
                else FinanceAccountMovement.Direction.OUT
            ),
            amount=payment.amount,
            balance_after=new_balance,
        )
        _record_audit(
            organization,
            item.church,
            payment.confirmed_by,
            'FINANCE_ACCOUNT_MOVEMENT_POSTED',
            'FinancePayment',
            payment.pk,
            {'account_id': str(account.pk), 'amount': str(payment.amount), 'balance_after': str(new_balance)},
        )

    @staticmethod
    def _serialize_transaction(request, organization, item):
        item = _transaction_queryset(organization, item.church_id).get(pk=item.pk)
        return FinanceTransactionSerializer(
            item,
            context={'can_view_contributors': _can_view_contributors(request.user, organization, item.church)},
        ).data


class FinanceTransactionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, transaction_id):
        organization = _organization()
        item = get_object_or_404(
            FinanceTransaction.objects.select_for_update().select_related('church'),
            pk=transaction_id,
            organization=organization,
            church__organization=organization,
        )
        if item.kind != FinanceTransaction.Kind.EXPENSE or item.status not in {
            FinanceTransaction.Status.APPROVED,
            FinanceTransaction.Status.PARTIALLY_PAID,
        }:
            raise ValidationError({'status': 'Un paiement ne peut être engagé que pour une dépense approuvée.'})
        _assert_finance_write_enabled(organization, item.church)
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                item.church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.TREASURER},
                organization,
                permission_code='finance.payment.confirm',
            )
        ):
            raise PermissionDenied('Seul un trésorier habilité peut initier un paiement.')
        serializer = FinancePaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        account = get_object_or_404(
            FinanceAccount,
            pk=values['account_id'],
            organization=organization,
            church=item.church,
            is_active=True,
        )
        if account.currency != item.currency:
            raise ValidationError({'account_id': 'La devise de la caisse doit correspondre à celle de la dépense.'})
        _validate_payment_method(account, values['method'])
        reference = values.get('reference', '').strip()
        if reference and item.payments.filter(reference=reference).exists():
            raise ValidationError({'reference': 'Cette référence de paiement est déjà utilisée pour cette opération.'})
        paid = item.payments.filter(status=FinancePayment.Status.CONFIRMED).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        pending = item.payments.filter(status=FinancePayment.Status.PENDING).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        if values['amount'] + paid + pending > item.amount:
            raise ValidationError({'amount': 'Le total des paiements dépasse le montant restant dû.'})
        payment = FinancePayment.objects.create(
            transaction=item,
            account=account,
            amount=values['amount'],
            method=values['method'],
            reference=reference,
            created_by=request.user,
        )
        _record_audit(
            organization,
            item.church,
            request.user,
            'FINANCE_PAYMENT_INITIATED',
            'FinancePayment',
            payment.pk,
            {'transaction_id': str(item.pk), 'amount': str(payment.amount), 'status': payment.status},
        )
        return Response({
            'id': str(payment.pk),
            'transaction_id': str(item.pk),
            'amount': payment.amount,
            'currency': item.currency,
            'status': payment.status,
            'method': payment.method,
            'account_id': str(account.pk),
        }, status=status.HTTP_201_CREATED)


class FinancePaymentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        church = get_object_or_404(
            Church,
            church_id=request.query_params.get('church_id'),
            organization=organization,
            is_active=True,
        )
        if not _can_view_transactions(request.user, organization, church):
            raise PermissionDenied('Vous ne disposez pas d’un accès aux paiements financiers.')
        queryset = FinancePayment.objects.filter(
            transaction__organization=organization,
            transaction__church=church,
            transaction__church__organization=organization,
        ).select_related('transaction', 'account', 'created_by', 'confirmed_by')
        payment_status = request.query_params.get('status')
        if payment_status:
            queryset = queryset.filter(status=payment_status)
        return Response(FinancePaymentSerializer(queryset[:200], many=True).data)

    @transaction.atomic
    def post(self, request, payment_id):
        organization = _organization()
        payment = get_object_or_404(
            FinancePayment.objects.select_for_update().select_related('transaction__church', 'transaction__created_by', 'transaction__approved_by'),
            pk=payment_id,
            transaction__organization=organization,
            transaction__church__organization=organization,
        )
        if payment.status != FinancePayment.Status.PENDING:
            raise ValidationError({'status': 'Ce paiement a déjà été traité.'})
        item = payment.transaction
        _assert_finance_write_enabled(organization, item.church)
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                item.church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.TREASURER},
                organization,
                permission_code='finance.payment.confirm',
            )
        ):
            raise PermissionDenied('Seul un trésorier habilité peut confirmer ou rejeter un paiement.')
        serializer = FinancePaymentConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if not values['confirmed']:
            if not values.get('reason', '').strip():
                raise ValidationError({'reason': 'Un motif est obligatoire pour rejeter un paiement.'})
            payment.status = FinancePayment.Status.REJECTED
            payment.save(update_fields=['status'])
            _record_audit(
                organization,
                item.church,
                request.user,
                'FINANCE_PAYMENT_REJECTED',
                'FinancePayment',
                payment.pk,
                {'reason': values['reason']},
            )
            return Response({'id': str(payment.pk), 'status': payment.status})
        if request.user.pk in {item.created_by_id, item.approved_by_id}:
            raise PermissionDenied('Le demandeur et l’approbateur ne peuvent pas confirmer l’exécution du paiement.')
        account = FinanceAccount.objects.select_for_update().get(pk=payment.account_id)
        payment.status = FinancePayment.Status.CONFIRMED
        payment.confirmed_by = request.user
        payment.confirmed_at = timezone.now()
        payment.save(update_fields=['status', 'confirmed_by', 'confirmed_at'])
        FinanceTransactionDecisionView._post_account_movement(organization, item, payment, account)
        paid = item.payments.filter(status=FinancePayment.Status.CONFIRMED).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        item.status = (
            FinanceTransaction.Status.PAID
            if paid >= item.amount
            else FinanceTransaction.Status.PARTIALLY_PAID
        )
        item.save(update_fields=['status', 'updated_at'])
        _record_audit(
            organization,
            item.church,
            request.user,
            'FINANCE_PAYMENT_CONFIRMED',
            'FinancePayment',
            payment.pk,
            {'transaction_id': str(item.pk), 'amount': str(payment.amount)},
        )
        return Response({'id': str(payment.pk), 'status': payment.status, 'transaction_status': item.status})


class FinancialBudgetListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        church = get_object_or_404(
            Church,
            church_id=request.query_params.get('church_id'),
            organization=organization,
            is_active=True,
        )
        if not _can_read_church(request, church):
            raise PermissionDenied('Vous ne disposez pas d’un accès financier à cette église.')
        budgets = FinancialBudget.objects.filter(
            organization=organization,
            church=church,
            period__church=church,
            category__kind=FinanceCategory.Kind.EXPENSE,
        ).select_related('period', 'category', 'campus')
        result = []
        for budget in budgets:
            expenses = FinanceTransaction.objects.filter(
                organization=organization,
                church=church,
                period=budget.period,
                category=budget.category,
                status__in=[
                    FinanceTransaction.Status.APPROVED,
                    FinanceTransaction.Status.PARTIALLY_PAID,
                    FinanceTransaction.Status.PAID,
                ],
            )
            if budget.campus:
                expenses = expenses.filter(campus=budget.campus)
            committed = expenses.annotate(
                base_amount=F('amount') * F('exchange_rate_to_base'),
            ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0')
            paid = FinancePayment.objects.filter(
                transaction__in=expenses,
                status=FinancePayment.Status.CONFIRMED,
            ).annotate(
                base_amount=F('amount') * F('transaction__exchange_rate_to_base'),
            ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0')
            budget.committed_amount = committed
            budget.paid_amount = paid
            result.append(budget)
        return Response(FinancialBudgetSerializer(result, many=True).data)

    @transaction.atomic
    def post(self, request):
        organization = _organization()
        church = get_object_or_404(
            Church,
            church_id=request.data.get('church_id'),
            organization=organization,
            is_active=True,
        )
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.ACCOUNTANT, FinanceRole.APPROVER},
                organization,
                permission_code='finance.budget.manage',
            )
        ):
            raise PermissionDenied('Vous ne disposez pas des droits requis pour créer un budget.')
        _assert_finance_write_enabled(organization, church)
        serializer = FinancialBudgetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        period = get_object_or_404(
            FinancialPeriod,
            pk=values['period_id'],
            organization=organization,
            church=church,
            status=FinancialPeriod.Status.OPEN,
        )
        category = get_object_or_404(
            FinanceCategory,
            pk=values['category_id'],
            organization=organization,
            church=church,
            kind=FinanceCategory.Kind.EXPENSE,
            is_active=True,
        )
        campus_id = values.get('campus_id')
        campus = get_object_or_404(
            Campus,
            pk=campus_id,
            church=church,
            is_active=True,
        ) if campus_id else None
        configuration = FinancialConfiguration.objects.get(organization=organization, church=church)
        if values['currency'] != configuration.base_currency:
            raise ValidationError({'currency': 'Un budget doit être exprimé dans la devise de base de l’église.'})
        if FinancialBudget.objects.filter(
            period=period,
            category=category,
            campus=campus,
        ).exists():
            raise ValidationError({'category_id': 'Un budget existe déjà pour cette période et cette catégorie.'})
        budget = FinancialBudget.objects.create(
            organization=organization,
            church=church,
            period=period,
            category=category,
            campus=campus,
            name=values['name'],
            allocated_amount=values['allocated_amount'],
            currency=values['currency'],
            created_by=request.user,
        )
        _record_audit(
            organization,
            church,
            request.user,
            'FINANCE_BUDGET_CREATED',
            'FinancialBudget',
            budget.pk,
            {'allocated_amount': str(budget.allocated_amount), 'currency': budget.currency, 'period_id': str(period.pk)},
        )
        budget.committed_amount = Decimal('0')
        budget.paid_amount = Decimal('0')
        return Response(FinancialBudgetSerializer(budget).data, status=status.HTTP_201_CREATED)


class FinancialBudgetRevisionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, budget_id):
        organization = _organization()
        budget = get_object_or_404(
            FinancialBudget.objects.select_for_update().select_related('church'),
            pk=budget_id,
            organization=organization,
            church__organization=organization,
        )
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                budget.church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.ACCOUNTANT, FinanceRole.APPROVER},
                organization,
                permission_code='finance.budget.manage',
            )
        ):
            raise PermissionDenied('Vous ne disposez pas des droits requis pour réviser ce budget.')
        _assert_finance_write_enabled(organization, budget.church)
        serializer = FinancialBudgetRevisionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        expenses = FinanceTransaction.objects.filter(
            organization=organization,
            church=budget.church,
            period=budget.period,
            category=budget.category,
            status__in=[
                FinanceTransaction.Status.APPROVED,
                FinanceTransaction.Status.PARTIALLY_PAID,
                FinanceTransaction.Status.PAID,
            ],
        )
        if budget.campus:
            expenses = expenses.filter(campus=budget.campus)
        committed = expenses.annotate(
            base_amount=F('amount') * F('exchange_rate_to_base'),
        ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0')
        if values['allocated_amount'] < committed:
            raise ValidationError({'allocated_amount': f'Le budget révisé ne peut être inférieur aux dépenses engagées ({committed}).'})
        previous = budget.allocated_amount
        budget.allocated_amount = values['allocated_amount']
        budget.save(update_fields=['allocated_amount'])
        revision = FinancialBudgetRevision.objects.create(
            budget=budget,
            previous_amount=previous,
            new_amount=budget.allocated_amount,
            reason=values['reason'],
            changed_by=request.user,
        )
        _record_audit(
            organization,
            budget.church,
            request.user,
            'FINANCE_BUDGET_REVISED',
            'FinancialBudget',
            budget.pk,
            {'previous_amount': str(previous), 'new_amount': str(budget.allocated_amount), 'reason': revision.reason},
        )
        return Response(FinancialBudgetRevisionSerializer(revision).data)


class FinanceSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        church = get_object_or_404(
            Church,
            church_id=request.query_params.get('church_id'),
            organization=organization,
            is_active=True,
        )
        if not _can_read_church(request, church):
            raise PermissionDenied('Vous ne disposez pas d’un accès financier à cette église.')
        configuration = FinancialConfiguration.objects.filter(organization=organization, church=church).first()
        base_currency = configuration.base_currency if configuration else 'XOF'
        payments = FinancePayment.objects.filter(
            transaction__organization=organization,
            transaction__church=church,
            transaction__church__organization=organization,
            status=FinancePayment.Status.CONFIRMED,
        ).select_related('transaction')
        converted = payments.annotate(
            base_amount=F('amount') * F('transaction__exchange_rate_to_base'),
        )
        income = converted.filter(transaction__kind=FinanceTransaction.Kind.INCOME).aggregate(total=Sum('base_amount'))['total'] or Decimal('0')
        expenses = converted.filter(transaction__kind=FinanceTransaction.Kind.EXPENSE).aggregate(total=Sum('base_amount'))['total'] or Decimal('0')
        accounts = FinanceAccount.objects.filter(
            organization=organization,
            church=church,
            is_active=True,
        ).order_by('name')
        account_data = []
        available = Decimal('0')
        for account in accounts:
            balance = _account_balance(account)
            if account.currency == base_currency:
                available += balance
            account_data.append({
                'id': str(account.pk),
                'name': account.name,
                'kind': account.kind,
                'currency': account.currency,
                'balance': str(balance),
            })
        pending_transactions = FinanceTransaction.objects.filter(
            organization=organization,
            church=church,
            status__in=[
                FinanceTransaction.Status.TO_VERIFY,
                FinanceTransaction.Status.PENDING_APPROVAL,
                FinanceTransaction.Status.APPROVED,
                FinanceTransaction.Status.PARTIALLY_PAID,
            ],
        ).count()
        recent = _transaction_queryset(organization, church.church_id)[:8]
        monthly_income = converted.filter(
            transaction__kind=FinanceTransaction.Kind.INCOME,
        ).annotate(month=TruncMonth('confirmed_at')).values('month').annotate(
            total=Sum('base_amount'),
        ).order_by('month')
        monthly_expenses = converted.filter(
            transaction__kind=FinanceTransaction.Kind.EXPENSE,
        ).annotate(month=TruncMonth('confirmed_at')).values('month').annotate(
            total=Sum('base_amount'),
        ).order_by('month')
        monthly_values = {}
        for row in monthly_income:
            if row['month']:
                monthly_values[row['month'].strftime('%Y-%m')] = {
                    'month': row['month'].strftime('%Y-%m'),
                    'income': str(row['total'] or 0),
                    'expenses': '0',
                }
        for row in monthly_expenses:
            if row['month']:
                month_key = row['month'].strftime('%Y-%m')
                monthly_values.setdefault(month_key, {
                    'month': month_key,
                    'income': '0',
                    'expenses': '0',
                })['expenses'] = str(row['total'] or 0)
        income_by_category = converted.filter(
            transaction__kind=FinanceTransaction.Kind.INCOME,
        ).values(
            'transaction__category_id',
            'transaction__category__name',
        ).annotate(total=Sum('base_amount')).order_by('-total')
        return Response({
            'church_id': str(church.church_id),
            'currency': base_currency,
            'income_total': str(income),
            'expense_total': str(expenses),
            'available_balance': str(available),
            'pending_transactions': pending_transactions,
            'monthly': list(monthly_values.values())[-12:],
            'income_by_category': [
                {'category_id': row['transaction__category_id'], 'category_name': row['transaction__category__name'], 'amount': str(row['total'] or 0)}
                for row in income_by_category
            ],
            'accounts': account_data,
            'recent_transactions': FinanceTransactionSerializer(
                recent,
                many=True,
                context={'can_view_contributors': _can_view_contributors(request.user, organization, church)},
            ).data,
        })


class FinanceReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        filters = FinanceReportFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        values = filters.validated_data
        church = get_object_or_404(
            Church,
            church_id=values['church_id'],
            organization=organization,
            is_active=True,
        )
        if not (
            _can_read_church(request, church)
            or has_configuration_permission(request.user, 'finance.report.read', organization, church=church)
        ):
            raise PermissionDenied('Vous ne disposez pas d’un accès financier à cette église.')

        payments = FinancePayment.objects.filter(
            transaction__organization=organization,
            transaction__church=church,
            transaction__church__organization=organization,
            status=FinancePayment.Status.CONFIRMED,
        )
        if values.get('start_date'):
            payments = payments.filter(confirmed_at__date__gte=values['start_date'])
        if values.get('end_date'):
            payments = payments.filter(confirmed_at__date__lte=values['end_date'])
        if values.get('period_id'):
            period = get_object_or_404(
                FinancialPeriod,
                pk=values['period_id'],
                organization=organization,
                church=church,
            )
            payments = payments.filter(transaction__period=period)

        converted = payments.annotate(
            base_amount=F('amount') * F('transaction__exchange_rate_to_base'),
        )
        totals = converted.aggregate(
            income=Sum('base_amount', filter=Q(transaction__kind=FinanceTransaction.Kind.INCOME)),
            expenses=Sum('base_amount', filter=Q(transaction__kind=FinanceTransaction.Kind.EXPENSE)),
        )
        income_by_category = converted.filter(
            transaction__kind=FinanceTransaction.Kind.INCOME,
        ).values(
            'transaction__category_id',
            'transaction__category__name',
        ).annotate(total=Sum('base_amount')).order_by('-total')
        expenses_by_category = converted.filter(
            transaction__kind=FinanceTransaction.Kind.EXPENSE,
        ).values(
            'transaction__category_id',
            'transaction__category__name',
        ).annotate(total=Sum('base_amount')).order_by('-total')

        configuration = FinancialConfiguration.objects.filter(church=church).first()
        currency = configuration.base_currency if configuration else 'XOF'
        accounts = FinanceAccount.objects.filter(
            organization=organization,
            church=church,
            is_active=True,
        ).order_by('name')
        account_report = [
            {
                'id': str(account.pk),
                'name': account.name,
                'kind': account.kind,
                'currency': account.currency,
                'opening_balance': str(account.opening_balance),
                'current_balance': str(_account_balance(account)),
            }
            for account in accounts
        ]

        budgets = FinancialBudget.objects.filter(
            organization=organization,
            church=church,
        ).select_related('period', 'category', 'campus')
        if values.get('period_id'):
            budgets = budgets.filter(period_id=values['period_id'])
        else:
            if values.get('start_date'):
                budgets = budgets.filter(period__ends_on__gte=values['start_date'])
            if values.get('end_date'):
                budgets = budgets.filter(period__starts_on__lte=values['end_date'])
        budget_report = []
        for budget in budgets:
            expenses = FinanceTransaction.objects.filter(
                organization=organization,
                church=church,
                period=budget.period,
                category=budget.category,
                kind=FinanceTransaction.Kind.EXPENSE,
                status__in=[
                    FinanceTransaction.Status.APPROVED,
                    FinanceTransaction.Status.PARTIALLY_PAID,
                    FinanceTransaction.Status.PAID,
                ],
            )
            if budget.campus:
                expenses = expenses.filter(campus=budget.campus)
            if values.get('start_date'):
                expenses = expenses.filter(transaction_date__gte=values['start_date'])
            if values.get('end_date'):
                expenses = expenses.filter(transaction_date__lte=values['end_date'])
            committed = expenses.annotate(
                base_amount=F('amount') * F('exchange_rate_to_base'),
            ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0')
            paid = FinancePayment.objects.filter(
                transaction__in=expenses,
                status=FinancePayment.Status.CONFIRMED,
            )
            if values.get('start_date'):
                paid = paid.filter(confirmed_at__date__gte=values['start_date'])
            if values.get('end_date'):
                paid = paid.filter(confirmed_at__date__lte=values['end_date'])
            paid_total = paid.annotate(
                base_amount=F('amount') * F('transaction__exchange_rate_to_base'),
            ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0')
            budget_report.append({
                'id': str(budget.pk),
                'name': budget.name,
                'period_name': budget.period.name,
                'category_name': budget.category.name,
                'campus_name': budget.campus.name if budget.campus else None,
                'currency': budget.currency,
                'allocated_amount': str(budget.allocated_amount),
                'committed_amount': str(committed),
                'paid_amount': str(paid_total),
                'available_amount': str(budget.allocated_amount - committed),
            })

        income = totals['income'] or Decimal('0')
        expenses = totals['expenses'] or Decimal('0')
        return Response({
            'church_id': str(church.church_id),
            'church_name': church.name,
            'currency': currency,
            'start_date': values.get('start_date'),
            'end_date': values.get('end_date'),
            'generated_at': timezone.now(),
            'income_total': str(income),
            'expense_total': str(expenses),
            'net_total': str(income - expenses),
            'income_by_category': [
                {
                    'category_id': row['transaction__category_id'],
                    'category_name': row['transaction__category__name'],
                    'amount': str(row['total'] or 0),
                }
                for row in income_by_category
            ],
            'expenses_by_category': [
                {
                    'category_id': row['transaction__category_id'],
                    'category_name': row['transaction__category__name'],
                    'amount': str(row['total'] or 0),
                }
                for row in expenses_by_category
            ],
            'accounts': account_report,
            'budgets': budget_report,
        })


class FinanceCashJournalView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        filters = FinanceCashJournalFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        values = filters.validated_data
        church = get_object_or_404(
            Church,
            church_id=values['church_id'],
            organization=organization,
            is_active=True,
        )
        if not (
            _can_read_church(request, church)
            or has_configuration_permission(request.user, 'finance.report.read', organization, church=church)
        ):
            raise PermissionDenied('Vous ne disposez pas d’un accès au journal de trésorerie de cette église.')
        movements = FinanceAccountMovement.objects.filter(
            organization=organization,
            church=church,
            payment__status=FinancePayment.Status.CONFIRMED,
        ).select_related(
            'account',
            'payment',
            'payment__transaction',
            'payment__transaction__category',
            'payment__transaction__member',
        )
        if values.get('start_date'):
            movements = movements.filter(created_at__date__gte=values['start_date'])
        if values.get('end_date'):
            movements = movements.filter(created_at__date__lte=values['end_date'])
        if values.get('account_id'):
            account = get_object_or_404(
                FinanceAccount,
                pk=values['account_id'],
                organization=organization,
                church=church,
            )
            movements = movements.filter(account=account)
        if values.get('period_id'):
            period = get_object_or_404(
                FinancialPeriod,
                pk=values['period_id'],
                organization=organization,
                church=church,
            )
            movements = movements.filter(payment__transaction__period=period)

        account_balances = FinanceAccount.objects.filter(
            organization=organization,
            church=church,
            is_active=True,
        )
        if values.get('account_id'):
            account_balances = account_balances.filter(pk=values['account_id'])
        opening_balances = []
        for account in account_balances.order_by('name'):
            starting_balance = account.opening_balance
            if values.get('start_date'):
                earlier_movements = FinanceAccountMovement.objects.filter(
                    account=account,
                    created_at__date__lt=values['start_date'],
                )
                incoming = earlier_movements.filter(
                    direction=FinanceAccountMovement.Direction.IN,
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                outgoing = earlier_movements.filter(
                    direction=FinanceAccountMovement.Direction.OUT,
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                starting_balance += incoming - outgoing
            opening_balances.append({
                'account_id': str(account.pk),
                'account_name': account.name,
                'currency': account.currency,
                'balance': str(starting_balance),
            })

        can_view_contributors = _can_view_contributors(request.user, organization, church)
        results = []
        for movement in movements.order_by('created_at', 'pk'):
            item = movement.payment.transaction
            counterparty = None
            if can_view_contributors and not item.is_anonymous:
                if item.member_id:
                    counterparty = f'{item.member.first_name} {item.member.last_name}'.strip()
                else:
                    counterparty = item.counterparty_name or None
            results.append({
                'id': str(movement.pk),
                'created_at': movement.created_at,
                'transaction_date': item.transaction_date,
                'account_id': str(movement.account_id),
                'account_name': movement.account.name,
                'currency': movement.account.currency,
                'direction': movement.direction,
                'amount': str(movement.amount),
                'balance_after': str(movement.balance_after),
                'transaction_id': str(item.pk),
                'transaction_kind': item.kind,
                'category_name': item.category.name,
                'description': item.description,
                'counterparty_name': counterparty,
                'transaction_reference': item.reference,
                'payment_reference': movement.payment.reference,
                'payment_method': movement.payment.method,
            })
        totals_by_currency = {}
        for item in results:
            currency_totals = totals_by_currency.setdefault(
                item['currency'],
                {'in': Decimal('0'), 'out': Decimal('0')},
            )
            currency_totals['in' if item['direction'] == FinanceAccountMovement.Direction.IN else 'out'] += Decimal(
                item['amount']
            )
        return Response({
            'church_id': str(church.church_id),
            'church_name': church.name,
            'start_date': values.get('start_date'),
            'end_date': values.get('end_date'),
            'opening_balances': opening_balances,
            'total_in': [
                {'currency': currency, 'amount': str(amounts['in'])}
                for currency, amounts in sorted(totals_by_currency.items())
            ],
            'total_out': [
                {'currency': currency, 'amount': str(amounts['out'])}
                for currency, amounts in sorted(totals_by_currency.items())
            ],
            'closing_totals': [
                {
                    'account_id': balance['account_id'],
                    'account_name': balance['account_name'],
                    'currency': balance['currency'],
                    'balance': str(
                        Decimal(balance['balance'])
                        + sum(
                            (
                                Decimal(item['amount']) if item['direction'] == FinanceAccountMovement.Direction.IN
                                else -Decimal(item['amount'])
                                for item in results
                                if item['account_id'] == balance['account_id']
                            ),
                            Decimal('0'),
                        )
                    ),
                }
                for balance in opening_balances
            ],
            'count': len(results),
            'results': results,
        })


class FinancialPeriodActionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, period_id, action):
        organization = _organization()
        period = get_object_or_404(
            FinancialPeriod.objects.select_for_update(),
            pk=period_id,
            organization=organization,
            church__organization=organization,
        )
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                period.church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.ACCOUNTANT, FinanceRole.CONTROLLER},
                organization,
                permission_code='finance.budget.manage',
            )
        ):
            raise PermissionDenied('Vous ne disposez pas des droits requis pour clôturer cette période.')
        reason = str(request.data.get('reason', '')).strip()
        if action == 'close':
            if period.status != FinancialPeriod.Status.OPEN:
                raise ValidationError({'status': 'Cette période est déjà clôturée.'})
            pending = FinanceTransaction.objects.filter(
                organization=organization,
                church=period.church,
                period=period,
                status__in=[
                    FinanceTransaction.Status.TO_VERIFY,
                    FinanceTransaction.Status.PENDING_APPROVAL,
                    FinanceTransaction.Status.APPROVED,
                    FinanceTransaction.Status.PARTIALLY_PAID,
                ],
            ).count()
            unconfirmed = FinancePayment.objects.filter(
                transaction__organization=organization,
                transaction__church=period.church,
                transaction__period=period,
                status=FinancePayment.Status.PENDING,
            ).count()
            if pending or unconfirmed:
                raise ValidationError({
                    'period': f'Clôture impossible : {pending} opération(s) et {unconfirmed} paiement(s) restent en attente.',
                })
            period.status = FinancialPeriod.Status.CLOSED
            period.closed_by = request.user
            period.closed_at = timezone.now()
            action_name = 'FINANCE_PERIOD_CLOSED'
        elif action == 'reopen':
            if period.status != FinancialPeriod.Status.CLOSED:
                raise ValidationError({'status': 'Seule une période clôturée peut être réouverte.'})
            if not reason:
                raise ValidationError({'reason': 'Un motif est obligatoire pour réouvrir une période.'})
            period.status = FinancialPeriod.Status.OPEN
            period.closed_by = None
            period.closed_at = None
            action_name = 'FINANCE_PERIOD_REOPENED'
        else:
            raise ValidationError({'action': 'Action de période inconnue.'})
        period.save(update_fields=['status', 'closed_by', 'closed_at', 'updated_at'])
        _record_audit(
            organization,
            period.church,
            request.user,
            action_name,
            'FinancialPeriod',
            period.pk,
            {'reason': reason},
        )
        return Response(FinancialPeriodSerializer(period).data)


class FinanceContextView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        is_admin = is_finance_organization_admin(request.user, organization)
        accesses = finance_accesses(request.user, organization).select_related('church')
        permission_codes = list(
            AppPermission.objects.filter(module='finance', is_active=True)
            .order_by('code')
            .values_list('code', flat=True)
        )
        churches = Church.objects.filter(
            organization=organization,
            is_active=True,
        ).order_by('name')
        if not is_admin:
            accesses = accesses.filter(role__in=FINANCE_READ_ROLES)
            custom_organization_access = any(
                has_configuration_permission(request.user, code, organization)
                for code in permission_codes
            )
            if not custom_organization_access:
                custom_church_ids = [
                    church.church_id
                    for church in Church.objects.filter(
                        organization=organization,
                        is_active=True,
                    )
                    if any(
                        has_configuration_permission(
                            request.user,
                            code,
                            organization,
                            church=church,
                        )
                        for code in permission_codes
                    )
                ]
                churches = churches.filter(
                    Q(church_id__in=accesses.values('church_id'))
                    | Q(church_id__in=custom_church_ids)
                )
        return Response({
            'organization_id': str(organization.tenant_id),
            'organization_name': organization.name,
            'is_organization_admin': is_admin,
            'churches': [
                {
                    'church_id': str(church.church_id),
                    'name': church.name,
                    'roles': list(accesses.filter(church=church).values_list('role', flat=True)),
                    'permission_codes': [
                        code for code in permission_codes
                        if is_admin or has_configuration_permission(
                            request.user,
                            code,
                            organization,
                            church=church,
                        )
                    ],
                }
                for church in churches
            ],
        })


class FinanceChurchAccessListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        if not is_finance_organization_admin(request.user, organization):
            raise PermissionDenied('Seul un administrateur de l’organisation peut consulter les affectations.')
        queryset = FinanceChurchAccess.objects.filter(
            organization=organization,
            church__organization=organization,
        ).select_related('church', 'member')
        church_id = request.query_params.get('church_id')
        if church_id:
            queryset = queryset.filter(church_id=church_id)
        return Response(FinanceChurchAccessSerializer(queryset, many=True).data)

    @transaction.atomic
    def post(self, request):
        organization = _organization()
        if not is_finance_organization_admin(request.user, organization):
            raise PermissionDenied('Seul un administrateur de l’organisation peut attribuer les rôles financiers.')
        serializer = FinanceChurchAccessCreateSerializer(
            data=request.data,
            context={'organization': organization},
        )
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        access, created = FinanceChurchAccess.objects.get_or_create(
            organization=organization,
            church=values['church'],
            member=values['member'],
            role=values['role'],
            defaults={'granted_by': request.user},
        )
        if not created and access.is_active:
            raise ValidationError({'role': 'Ce rôle financier est déjà attribué à ce membre.'})
        if not created:
            access.is_active = True
            access.granted_by = request.user
            access.revoked_by = None
            access.save(update_fields=['is_active', 'granted_by', 'revoked_by', 'updated_at'])
        _record_audit(
            organization,
            values['church'],
            request.user,
            'FINANCE_ACCESS_GRANTED',
            'FinanceChurchAccess',
            access.pk,
            {'member_id': str(values['member'].pk), 'role': values['role']},
        )
        return Response(
            FinanceChurchAccessSerializer(access).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class FinanceChurchAccessRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, access_id):
        organization = _organization()
        if not is_finance_organization_admin(request.user, organization):
            raise PermissionDenied('Seul un administrateur de l’organisation peut révoquer un rôle financier.')
        access = get_object_or_404(
            FinanceChurchAccess.objects.select_related('church', 'member'),
            pk=access_id,
            organization=organization,
            church__organization=organization,
        )
        if access.is_active:
            access.is_active = False
            access.revoked_by = request.user
            access.save(update_fields=['is_active', 'revoked_by', 'updated_at'])
            _record_audit(
                organization,
                access.church,
                request.user,
                'FINANCE_ACCESS_REVOKED',
                'FinanceChurchAccess',
                access.pk,
                {'member_id': str(access.member_id), 'role': access.role},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class FinancialConfigurationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, church_id):
        organization = _organization()
        church = get_object_or_404(Church, church_id=church_id, organization=organization, is_active=True)
        if not _can_read_church(request, church):
            raise PermissionDenied('Vous ne disposez pas d’un accès financier à cette église.')
        configuration = get_object_or_404(
            FinancialConfiguration.objects.select_related('church'),
            organization=organization,
            church=church,
        )
        return Response(FinancialConfigurationSerializer(configuration).data)

    @transaction.atomic
    def post(self, request, church_id):
        organization = _organization()
        if not is_finance_organization_admin(request.user, organization):
            raise PermissionDenied('Seul un administrateur de l’organisation peut initialiser la configuration financière.')
        serializer = FinancialConfigurationCreateSerializer(data={**request.data, 'church_id': church_id})
        serializer.is_valid(raise_exception=True)
        church = get_object_or_404(
            Church,
            church_id=church_id,
            organization=organization,
            is_active=True,
        )
        if FinancialConfiguration.objects.filter(organization=organization, church=church).exists():
            raise ValidationError({'church_id': 'La configuration financière de cette église existe déjà.'})
        values = serializer.validated_data
        configuration = FinancialConfiguration.objects.create(
            organization=organization,
            church=church,
            level=values['level'],
            base_currency=values['base_currency'],
            created_by=request.user,
            updated_by=request.user,
        )
        FinancialConfigurationVersion.objects.create(
            configuration=configuration,
            version=configuration.version,
            level=configuration.level,
            base_currency=configuration.base_currency,
            reason=values['reason'],
            effective_at=timezone.now(),
            changed_by=request.user,
        )
        for name, code, kind, approval_required in [
            ('Dîme', 'dime', FinanceCategory.Kind.INCOME, False),
            ('Offrande', 'offrande', FinanceCategory.Kind.INCOME, False),
            ('Don', 'don', FinanceCategory.Kind.INCOME, False),
            ('Collecte spéciale', 'collecte-speciale', FinanceCategory.Kind.INCOME, False),
            ('Fonctionnement', 'fonctionnement', FinanceCategory.Kind.EXPENSE, True),
            ('Achat matériel', 'achat-materiel', FinanceCategory.Kind.EXPENSE, True),
        ]:
            FinanceCategory.objects.get_or_create(
                church=church,
                code=code,
                defaults={
                    'organization': organization,
                    'name': name,
                    'kind': kind,
                    'requires_approval': approval_required,
                    'is_system': True,
                    'created_by': request.user,
                },
            )
        FinanceAccount.objects.get_or_create(
            church=church,
            name='Caisse principale',
            defaults={
                'organization': organization,
                'kind': FinanceAccount.Kind.CASH,
                'currency': values['base_currency'],
                'created_by': request.user,
            },
        )
        _record_audit(
            organization,
            church,
            request.user,
            'FINANCE_CONFIGURATION_CREATED',
            'FinancialConfiguration',
            configuration.pk,
            {
                'version': configuration.version,
                'level': configuration.level,
                'base_currency': configuration.base_currency,
                'reason': values['reason'],
            },
        )
        return Response(
            FinancialConfigurationSerializer(configuration).data,
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def patch(self, request, church_id):
        organization = _organization()
        if not is_finance_organization_admin(request.user, organization):
            raise PermissionDenied('Seul un administrateur de l’organisation peut modifier la configuration financière.')
        church = get_object_or_404(Church, church_id=church_id, organization=organization, is_active=True)
        configuration = get_object_or_404(
            FinancialConfiguration.objects.select_for_update(),
            organization=organization,
            church=church,
        )
        serializer = FinancialConfigurationChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        changes = {
            field: values[field]
            for field in ('level', 'base_currency')
            if field in values and values[field] != getattr(configuration, field)
        }
        if not changes:
            raise ValidationError({'detail': 'Aucune modification de configuration à enregistrer.'})
        for field, value in changes.items():
            setattr(configuration, field, value)
        configuration.version += 1
        configuration.updated_by = request.user
        configuration.save()
        FinancialConfigurationVersion.objects.create(
            configuration=configuration,
            version=configuration.version,
            level=configuration.level,
            base_currency=configuration.base_currency,
            reason=values['reason'],
            effective_at=timezone.now(),
            changed_by=request.user,
        )
        _record_audit(
            organization,
            church,
            request.user,
            'FINANCE_CONFIGURATION_UPDATED',
            'FinancialConfiguration',
            configuration.pk,
            {
                'version': configuration.version,
                'changes': changes,
                'reason': values['reason'],
            },
        )
        return Response(FinancialConfigurationSerializer(configuration).data)


class FinancialConfigurationHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, church_id):
        organization = _organization()
        church = get_object_or_404(Church, church_id=church_id, organization=organization, is_active=True)
        if not _can_read_church(request, church):
            raise PermissionDenied('Vous ne disposez pas d’un accès financier à cette église.')
        configuration = get_object_or_404(
            FinancialConfiguration,
            organization=organization,
            church=church,
        )
        versions = configuration.versions.select_related('changed_by').all()
        return Response(FinancialConfigurationVersionSerializer(versions, many=True).data)


class FinancialPeriodListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _organization()
        queryset = FinancialPeriod.objects.filter(
            organization=organization,
            church__organization=organization,
        ).select_related('church')
        if not is_finance_organization_admin(request.user, organization):
            queryset = queryset.filter(
                church_id__in=finance_accesses(
                    request.user,
                    organization,
                ).filter(role__in=FINANCE_READ_ROLES).values('church_id')
            )
        church_id = request.query_params.get('church_id')
        if church_id:
            queryset = queryset.filter(church_id=church_id)
        return Response(FinancialPeriodSerializer(queryset, many=True).data)

    @transaction.atomic
    def post(self, request):
        organization = _organization()
        serializer = FinancialPeriodCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        church = get_object_or_404(
            Church,
            church_id=values['church_id'],
            organization=organization,
            is_active=True,
        )
        if not (
            is_finance_organization_admin(request.user, organization)
            or has_finance_role(
                request.user,
                church,
                {FinanceRole.FINANCE_ADMIN, FinanceRole.TREASURER, FinanceRole.ACCOUNTANT},
                organization,
                permission_code='finance.budget.manage',
            )
        ):
            raise PermissionDenied('Vous ne disposez pas du rôle requis pour créer une période financière.')
        configuration = FinancialConfiguration.objects.filter(
            organization=organization,
            church=church,
        ).first()
        if configuration is None:
            raise ValidationError({'church_id': 'La configuration financière doit être initialisée avant les périodes.'})
        if configuration.is_suspended:
            raise ValidationError({'church_id': 'La gestion financière de cette église est suspendue.'})
        try:
            with transaction.atomic():
                period = FinancialPeriod.objects.create(
                    organization=organization,
                    church=church,
                    name=values['name'],
                    starts_on=values['starts_on'],
                    ends_on=values['ends_on'],
                    created_by=request.user,
                )
        except IntegrityError as exc:
            raise ValidationError({'name': 'Une période porte déjà ce nom pour cette église.'}) from exc
        _record_audit(
            organization,
            church,
            request.user,
            'FINANCE_PERIOD_CREATED',
            'FinancialPeriod',
            period.pk,
            {'name': period.name, 'starts_on': str(period.starts_on), 'ends_on': str(period.ends_on)},
        )
        return Response(FinancialPeriodSerializer(period).data, status=status.HTTP_201_CREATED)

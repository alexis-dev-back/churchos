from rest_framework import serializers

from apps.core.models import Church
from apps.finance.models import (
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
from apps.members.models import Member
from django.db.models import Sum
from decimal import Decimal


class FinanceChurchAccessSerializer(serializers.ModelSerializer):
    member_name = serializers.SerializerMethodField()
    member_id = serializers.UUIDField(read_only=True)
    church_id = serializers.UUIDField(source='church.church_id', read_only=True)

    class Meta:
        model = FinanceChurchAccess
        fields = [
            'id', 'church_id', 'member_id', 'member_name', 'role',
            'is_active', 'granted_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church_id', 'member_id', 'member_name', 'is_active', 'granted_by', 'created_at', 'updated_at']

    def get_member_name(self, instance):
        return f'{instance.member.first_name} {instance.member.last_name}'.strip()


class FinanceChurchAccessCreateSerializer(serializers.Serializer):
    church_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=FinanceRole.choices)

    def validate(self, attrs):
        organization = self.context['organization']
        church = Church.objects.filter(
            church_id=attrs['church_id'],
            organization=organization,
            is_active=True,
        ).first()
        if church is None:
            raise serializers.ValidationError({'church_id': 'Église introuvable ou inactive dans cette organisation.'})
        member = Member.objects.filter(
            pk=attrs['member_id'],
            organization=organization,
            church=church,
            status=Member.Status.ACTIVE,
            user__is_active=True,
        ).select_related('user').first()
        if member is None:
            raise serializers.ValidationError({'member_id': 'Le membre doit être actif et appartenir à cette église.'})
        attrs['church'] = church
        attrs['member'] = member
        return attrs


class FinancialConfigurationSerializer(serializers.ModelSerializer):
    church_id = serializers.UUIDField(source='church.church_id', read_only=True)
    church_name = serializers.CharField(source='church.name', read_only=True)

    class Meta:
        model = FinancialConfiguration
        fields = [
            'id', 'church_id', 'church_name', 'level', 'base_currency',
            'version', 'is_suspended', 'suspension_reason', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church_id', 'church_name', 'version', 'is_suspended', 'suspension_reason', 'created_at', 'updated_at']

    def validate_base_currency(self, value):
        value = value.upper()
        if len(value) != 3 or not value.isascii() or not value.isalpha():
            raise serializers.ValidationError('La devise doit être un code alphabétique de trois lettres.')
        return value


class FinancialConfigurationChangeSerializer(serializers.Serializer):
    level = serializers.ChoiceField(choices=FinancialConfiguration.Level.choices, required=False)
    base_currency = serializers.CharField(min_length=3, max_length=3, required=False)
    reason = serializers.CharField(max_length=500)

    def validate_base_currency(self, value):
        value = value.upper()
        if not value.isascii() or not value.isalpha():
            raise serializers.ValidationError('La devise doit être un code alphabétique de trois lettres.')
        return value


class FinancialConfigurationCreateSerializer(serializers.Serializer):
    church_id = serializers.UUIDField()
    level = serializers.ChoiceField(choices=FinancialConfiguration.Level.choices)
    base_currency = serializers.CharField(min_length=3, max_length=3, default='XOF')
    reason = serializers.CharField(max_length=500)

    def validate_base_currency(self, value):
        value = value.upper()
        if not value.isascii() or not value.isalpha():
            raise serializers.ValidationError('La devise doit être un code alphabétique de trois lettres.')
        return value


class FinancialConfigurationVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinancialConfigurationVersion
        fields = ['version', 'level', 'base_currency', 'reason', 'effective_at', 'changed_by', 'created_at']
        read_only_fields = fields


class FinancialPeriodSerializer(serializers.ModelSerializer):
    church_id = serializers.UUIDField(source='church.church_id', read_only=True)

    class Meta:
        model = FinancialPeriod
        fields = [
            'id', 'church_id', 'name', 'starts_on', 'ends_on', 'status',
            'created_by', 'closed_by', 'closed_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church_id', 'status', 'created_by', 'closed_by', 'closed_at', 'created_at', 'updated_at']

    def validate(self, attrs):
        starts_on = attrs.get('starts_on', getattr(self.instance, 'starts_on', None))
        ends_on = attrs.get('ends_on', getattr(self.instance, 'ends_on', None))
        if starts_on and ends_on and ends_on < starts_on:
            raise serializers.ValidationError({'ends_on': 'La fin de période doit être postérieure ou égale au début.'})
        return attrs


class FinancialPeriodCreateSerializer(serializers.Serializer):
    church_id = serializers.UUIDField()
    name = serializers.CharField(max_length=100)
    starts_on = serializers.DateField()
    ends_on = serializers.DateField()

    def validate(self, attrs):
        if attrs['ends_on'] < attrs['starts_on']:
            raise serializers.ValidationError({'ends_on': 'La fin de période doit être postérieure ou égale au début.'})
        return attrs


class FinanceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = FinanceCategory
        fields = ['id', 'name', 'code', 'kind', 'requires_approval', 'is_active', 'is_system']
        read_only_fields = ['id', 'is_system']


class FinanceFundSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinanceFund
        fields = ['id', 'name', 'purpose', 'is_restricted', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class FinanceAccountSerializer(serializers.ModelSerializer):
    church_id = serializers.UUIDField(source='church.church_id', read_only=True)
    balance = serializers.SerializerMethodField()

    class Meta:
        model = FinanceAccount
        fields = [
            'id', 'church_id', 'campus', 'name', 'kind', 'currency',
            'opening_balance', 'balance', 'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'church_id', 'balance', 'created_at']

    def get_balance(self, instance):
        movements = FinanceAccountMovement.objects.filter(account=instance)
        incoming = movements.filter(direction=FinanceAccountMovement.Direction.IN).aggregate(total=Sum('amount'))['total'] or 0
        outgoing = movements.filter(direction=FinanceAccountMovement.Direction.OUT).aggregate(total=Sum('amount'))['total'] or 0
        return instance.opening_balance + incoming - outgoing


class FinanceTransactionCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=FinanceTransaction.Kind.choices)
    period_id = serializers.UUIDField()
    category_id = serializers.UUIDField()
    fund_id = serializers.UUIDField(required=False, allow_null=True)
    campus_id = serializers.UUIDField(required=False, allow_null=True)
    member_id = serializers.UUIDField(required=False, allow_null=True)
    counterparty_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    is_anonymous = serializers.BooleanField(required=False, default=False)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    currency = serializers.CharField(min_length=3, max_length=3, default='XOF')
    exchange_rate_to_base = serializers.DecimalField(max_digits=14, decimal_places=6, min_value=Decimal('0.000001'), default=1)
    transaction_date = serializers.DateField()
    reference = serializers.CharField(max_length=80, required=False, allow_blank=True)

    def validate_currency(self, value):
        value = value.upper()
        if not value.isascii() or not value.isalpha():
            raise serializers.ValidationError('La devise doit être un code alphabétique de trois lettres.')
        return value


class FinanceTransactionSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    fund_name = serializers.CharField(source='fund.name', read_only=True, allow_null=True)
    campus_name = serializers.CharField(source='campus.name', read_only=True, allow_null=True)
    member_name = serializers.SerializerMethodField()
    paid_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, default=0)
    remaining_amount = serializers.SerializerMethodField()

    class Meta:
        model = FinanceTransaction
        fields = [
            'id', 'kind', 'status', 'period_id', 'category_id', 'category_name',
            'fund_id', 'fund_name', 'campus_id', 'campus_name', 'member_id',
            'member_name', 'counterparty_name', 'is_anonymous', 'description',
            'amount', 'paid_amount', 'remaining_amount', 'currency',
            'exchange_rate_to_base', 'transaction_date', 'reference',
            'created_by', 'approved_by', 'approved_at', 'decision_reason',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_member_name(self, instance):
        if instance.is_anonymous or not self.context.get('can_view_contributors', False):
            return None
        if instance.member_id:
            return f'{instance.member.first_name} {instance.member.last_name}'.strip()
        return instance.counterparty_name or None

    def get_remaining_amount(self, instance):
        return max(instance.amount - getattr(instance, 'paid_amount', 0), 0)


class FinancePaymentCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    method = serializers.ChoiceField(choices=FinancePayment.Method.choices)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True)


class FinancePaymentSerializer(serializers.ModelSerializer):
    transaction_id = serializers.UUIDField(read_only=True)
    transaction_kind = serializers.CharField(source='transaction.kind', read_only=True)
    transaction_description = serializers.CharField(source='transaction.description', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)
    currency = serializers.CharField(source='transaction.currency', read_only=True)

    class Meta:
        model = FinancePayment
        fields = [
            'id', 'transaction_id', 'transaction_kind', 'transaction_description',
            'account', 'account_name', 'amount', 'currency', 'method', 'status',
            'reference', 'created_by', 'confirmed_by', 'confirmed_at', 'created_at',
        ]
        read_only_fields = fields


class FinancePaymentConfirmSerializer(serializers.Serializer):
    confirmed = serializers.BooleanField(default=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class FinanceReportFilterSerializer(serializers.Serializer):
    church_id = serializers.UUIDField()
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    period_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if attrs.get('start_date') and attrs.get('end_date') and attrs['start_date'] > attrs['end_date']:
            raise serializers.ValidationError({'end_date': 'La date de fin doit être postérieure ou égale à la date de début.'})
        return attrs


class FinanceCashJournalFilterSerializer(FinanceReportFilterSerializer):
    account_id = serializers.UUIDField(required=False)


class FinancialBudgetCreateSerializer(serializers.Serializer):
    period_id = serializers.UUIDField()
    category_id = serializers.UUIDField()
    campus_id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(max_length=160)
    allocated_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    currency = serializers.CharField(min_length=3, max_length=3, default='XOF')

    def validate_currency(self, value):
        value = value.upper()
        if not value.isascii() or not value.isalpha():
            raise serializers.ValidationError('La devise doit être un code alphabétique de trois lettres.')
        return value


class FinancialBudgetRevisionCreateSerializer(serializers.Serializer):
    allocated_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    reason = serializers.CharField(max_length=500)


class FinancialBudgetSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    campus_name = serializers.CharField(source='campus.name', read_only=True, allow_null=True)
    committed_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, default=0)
    paid_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, default=0)
    available_amount = serializers.SerializerMethodField()

    class Meta:
        model = FinancialBudget
        fields = [
            'id', 'period_id', 'category_id', 'category_name', 'campus_id',
            'campus_name', 'name', 'allocated_amount', 'currency',
            'committed_amount', 'paid_amount', 'available_amount', 'created_at',
        ]
        read_only_fields = fields

    def get_available_amount(self, instance):
        return max(instance.allocated_amount - getattr(instance, 'committed_amount', 0), 0)


class FinancialBudgetRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinancialBudgetRevision
        fields = ['id', 'previous_amount', 'new_amount', 'reason', 'changed_by', 'created_at']
        read_only_fields = fields

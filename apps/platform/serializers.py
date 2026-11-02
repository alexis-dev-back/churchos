from rest_framework import serializers

from .models import Invoice, Subscription, SubscriptionPlan, SupportTicket


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'name', 'tier', 'price', 'currency', 'period', 'features', 'is_active', 'created_at', 'updated_at']


class SubscriptionSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    plan_name = serializers.CharField(source='plan.name', read_only=True)
    plan_tier = serializers.CharField(source='plan.tier', read_only=True)

    class Meta:
        model = Subscription
        fields = ['id', 'organization', 'organization_name', 'plan', 'plan_name', 'plan_tier', 'status', 'started_at', 'renewal_at', 'cancelled_at', 'created_at', 'updated_at']


class InvoiceSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)

    class Meta:
        model = Invoice
        fields = ['id', 'number', 'organization', 'organization_name', 'subscription', 'amount', 'currency', 'issued_at', 'due_at', 'status', 'created_at', 'updated_at']


class SupportTicketSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)

    class Meta:
        model = SupportTicket
        fields = ['id', 'number', 'organization', 'organization_name', 'subject', 'priority', 'status', 'created_at', 'updated_at']
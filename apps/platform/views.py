from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Church, Organization

from .models import Invoice, PlatformSetting, Subscription, SubscriptionPlan, SupportTicket
from .serializers import InvoiceSerializer, SubscriptionPlanSerializer, SubscriptionSerializer, SupportTicketSerializer


class IsSuperUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class SubscriptionPlanListCreateView(generics.ListCreateAPIView):
    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [IsSuperUser]


class SubscriptionPlanDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [IsSuperUser]


class SubscriptionListCreateView(generics.ListCreateAPIView):
    queryset = Subscription.objects.select_related('organization', 'plan').all()
    serializer_class = SubscriptionSerializer
    permission_classes = [IsSuperUser]

    def get_queryset(self):
        qs = super().get_queryset()
        org = self.request.query_params.get('organization')
        if org:
            qs = qs.filter(organization_id=org)
        return qs


class SubscriptionDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Subscription.objects.select_related('organization', 'plan').all()
    serializer_class = SubscriptionSerializer
    permission_classes = [IsSuperUser]


class InvoiceListCreateView(generics.ListCreateAPIView):
    queryset = Invoice.objects.select_related('organization', 'subscription').all()
    serializer_class = InvoiceSerializer
    permission_classes = [IsSuperUser]

    def get_queryset(self):
        qs = super().get_queryset()
        org = self.request.query_params.get('organization')
        if org:
            qs = qs.filter(organization_id=org)
        return qs


class InvoiceDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Invoice.objects.select_related('organization', 'subscription').all()
    serializer_class = InvoiceSerializer
    permission_classes = [IsSuperUser]


class SupportTicketListCreateView(generics.ListCreateAPIView):
    queryset = SupportTicket.objects.select_related('organization').all()
    serializer_class = SupportTicketSerializer
    permission_classes = [IsSuperUser]

    def get_queryset(self):
        qs = super().get_queryset()
        org = self.request.query_params.get('organization')
        if org:
            qs = qs.filter(organization_id=org)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs


class SupportTicketDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = SupportTicket.objects.select_related('organization').all()
    serializer_class = SupportTicketSerializer
    permission_classes = [IsSuperUser]


class PlatformStatsView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        from apps.members.models import Member

        organizations = Organization.objects.count()
        churches = Church.objects.count()
        subscriptions = Subscription.objects.filter(status=Subscription.Status.ACTIVE).count()
        trials = Subscription.objects.filter(status=Subscription.Status.TRIAL).count()
        suspended = Subscription.objects.filter(status=Subscription.Status.SUSPENDED).count()
        invoices_pending = Invoice.objects.filter(status=Invoice.Status.PENDING).count()
        tickets_open = SupportTicket.objects.filter(status__in=[SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS]).count()
        users = User.objects.count()
        users_active = User.objects.filter(is_active=True).count()
        members = Member.objects.count()
        mrr = sum(
            (sub.plan.price for sub in Subscription.objects.filter(status=Subscription.Status.ACTIVE).select_related('plan')),
            start=0,
        )
        pending_amount = sum(
            (invoice.amount for invoice in Invoice.objects.filter(status=Invoice.Status.PENDING)),
            start=0,
        )
        return Response({
            'organizations': organizations,
            'churches': churches,
            'subscriptions_active': subscriptions,
            'subscriptions_trial': trials,
            'subscriptions_suspended': suspended,
            'invoices_pending': invoices_pending,
            'invoices_pending_amount': str(pending_amount),
            'tickets_open': tickets_open,
            'users': users,
            'users_active': users_active,
            'members': members,
            'mrr': str(mrr),
            'arr': str(mrr * 12),
        })


class PlatformSettingsView(APIView):
    permission_classes = [IsSuperUser]
    defaults = {
        'platform_name': 'ChurchOS',
        'support_email': 'support@churchos.io',
        'default_currency': 'XAF',
        'default_language': 'fr',
    }

    def get(self, request):
        stored = {item.key: item.value for item in PlatformSetting.objects.filter(key__in=self.defaults)}
        return Response({key: stored.get(key, value) for key, value in self.defaults.items()})

    def put(self, request):
        for key in self.defaults:
            if key in request.data and request.data[key] is not None:
                PlatformSetting.objects.update_or_create(key=key, defaults={'value': str(request.data[key])})
        return self.get(request)
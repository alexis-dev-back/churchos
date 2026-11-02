from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Organization

from .models import Invoice, Subscription, SubscriptionPlan, SupportTicket
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
        organizations = Organization.objects.count()
        subscriptions = Subscription.objects.filter(status=Subscription.Status.ACTIVE).count()
        trials = Subscription.objects.filter(status=Subscription.Status.TRIAL).count()
        invoices_pending = Invoice.objects.filter(status=Invoice.Status.PENDING).count()
        tickets_open = SupportTicket.objects.filter(status=SupportTicket.Status.OPEN).count()
        users = User.objects.count()
        return Response({
            'organizations': organizations,
            'subscriptions_active': subscriptions,
            'subscriptions_trial': trials,
            'invoices_pending': invoices_pending,
            'tickets_open': tickets_open,
            'users': users,
        })
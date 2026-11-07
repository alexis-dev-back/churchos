from django.urls import path

from .views import (
    InvoiceDetailView,
    InvoiceListCreateView,
    PlatformSettingsView,
    PlatformStatsView,
    SubscriptionDetailView,
    SubscriptionListCreateView,
    SubscriptionPlanDetailView,
    SubscriptionPlanListCreateView,
    SupportTicketDetailView,
    SupportTicketListCreateView,
)

urlpatterns = [
    path('plans/', SubscriptionPlanListCreateView.as_view(), name='platform-plans'),
    path('plans/<uuid:pk>/', SubscriptionPlanDetailView.as_view(), name='platform-plan-detail'),
    path('subscriptions/', SubscriptionListCreateView.as_view(), name='platform-subscriptions'),
    path('subscriptions/<uuid:pk>/', SubscriptionDetailView.as_view(), name='platform-subscription-detail'),
    path('invoices/', InvoiceListCreateView.as_view(), name='platform-invoices'),
    path('invoices/<uuid:pk>/', InvoiceDetailView.as_view(), name='platform-invoice-detail'),
    path('tickets/', SupportTicketListCreateView.as_view(), name='platform-tickets'),
    path('tickets/<uuid:pk>/', SupportTicketDetailView.as_view(), name='platform-ticket-detail'),
    path('stats/', PlatformStatsView.as_view(), name='platform-stats'),
    path('settings/', PlatformSettingsView.as_view(), name='platform-settings'),
]
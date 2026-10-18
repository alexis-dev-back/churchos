from django.contrib.auth import authenticate
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.fields import UUIDField
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core.models.organizations import OrganizationMembership
from apps.core.serializers import RegistrationSerializer
from apps.core.permissions import IsSuperuser
from infrastructure.tenancy import TenantContext


class RegistrationView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'registration'
    serializer_class = RegistrationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user_id': user.pk,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        user = authenticate(
            request,
            username=request.data.get('username'),
            password=request.data.get('password'),
        )
        if user is None:
            return Response({'detail': 'Identifiants invalides.'}, status=status.HTTP_400_BAD_REQUEST)
        refresh = RefreshToken.for_user(user)
        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user_id': user.pk,
        })


class AuthTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'token_refresh'


class LogoutView(APIView):
    def post(self, request):
        try:
            refresh = RefreshToken(request.data['refresh'])
            refresh.blacklist()
        except KeyError:
            return Response(
                {'refresh': 'Ce champ est obligatoire.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except TokenError:
            return Response(
                {'refresh': 'Ce refresh token est invalide ou déjà révoqué.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        memberships = OrganizationMembership.objects.filter(
            user=request.user,
            is_active=True,
            organization__is_active=True,
        ).select_related('organization')
        active_organization = TenantContext.get()
        return Response({
            'id': request.user.pk,
            'username': request.user.get_username(),
            'email': request.user.email,
            'is_superuser': request.user.is_superuser,
            'active_organization': (
                {
                    'tenant_id': str(active_organization.tenant_id),
                    'name': active_organization.name,
                }
                if active_organization is not None else None
            ),
            'organizations': [
                {
                    'tenant_id': item.organization.tenant_id,
                    'name': item.organization.name,
                    'role': item.role,
                }
                for item in memberships
            ],
        })

class SelectOrganizationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        memberships = list(
            OrganizationMembership.objects.filter(
                user=request.user,
                organization__is_active=True,
                is_active=True,
            ).select_related('organization').order_by('organization__name')
        )
        if not memberships:
            return Response(
                {'detail': "Aucune organisation active ne vous est associée."},
                status=status.HTTP_403_FORBIDDEN,
            )

        tenant_id = request.data.get('tenant_id')
        if tenant_id is None:
            if len(memberships) > 1:
                return Response({
                    'requires_selection': True,
                    'organizations': [
                        {
                            'tenant_id': str(item.organization.tenant_id),
                            'name': item.organization.name,
                            'role': item.role,
                        }
                        for item in memberships
                    ],
                })
            membership = memberships[0]
        else:
            try:
                tenant_id = UUIDField().run_validation(tenant_id)
            except ValidationError as error:
                return Response(
                    {'tenant_id': error.detail},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            membership = next(
                (
                    item for item in memberships
                    if item.organization.tenant_id == tenant_id
                ),
                None,
            )
            if membership is None:
                return Response(
                    {"detail": "Vous n'avez pas accès à cette organisation."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        refresh = RefreshToken.for_user(request.user)
        refresh['tenant_id'] = str(membership.organization.tenant_id)

        return Response({
            'requires_selection': False,
            'tenant_id': str(membership.organization.tenant_id),
            'organization': membership.organization.name,
            'role': membership.role,
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        })
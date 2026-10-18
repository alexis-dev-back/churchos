from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.core.models.campus import Campus
from apps.core.serializers import CampusSerializer
from infrastructure.tenancy import TenantContext
from infrastructure.tenancy.permissions import HasActiveTenant, HasTenantAdminRole


#Affichage de toutes les campus associés à l'utilisateur
class CampusView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant]

    def get(self, request):
        campuses = Campus.objects.filter(
            church__organization=TenantContext.get(),
            church__organization__memberships__user=request.user,
        ).distinct()
        serializer = CampusSerializer(campuses, many=True)
        return Response(serializer.data)

#Création d'un nouveau campus
class CampusCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant, HasTenantAdminRole]

    def post(self, request):
        serializer = CampusSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            campus = serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

#Détails d'un campus
class CampusDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant]

    def get(self, request, campus_id):
        campus = get_object_or_404(
            Campus.objects.filter(
                church__organization=TenantContext.get(),
                church__organization__memberships__user=request.user,
            ),
            campus_id=campus_id,
        )
        serializer = CampusSerializer(campus)
        return Response(serializer.data)

#Suppression d'un campus
class CampusDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant, HasTenantAdminRole]

    def delete(self, request, campus_id):
        campus = get_object_or_404(
            Campus.objects.filter(
                church__organization=TenantContext.get(),
                church__organization__memberships__user=request.user,
            ),
            campus_id=campus_id,
        )
        campus.delete()
        return Response(status=204)

#Mise à jour d'un campus
class CampusUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant, HasTenantAdminRole]

    def put(self, request, campus_id):
        campus = get_object_or_404(
            Campus.objects.filter(
                church__organization=TenantContext.get(),
                church__organization__memberships__user=request.user,
            ),
            campus_id=campus_id,
        )
        serializer = CampusSerializer(
            campus,
            data=request.data,
            partial=True,
            context={'request': request},
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)
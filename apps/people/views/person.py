from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.people.models import Person
from apps.people.serializers import PersonSerializer
from apps.people.services.person_service import PersonService
from apps.members.permissions import IsTenantAdmin


class PersonListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request):
        q = request.query_params.get('q', '').strip()
        qs = Person.objects.all()
        if q:
            qs = qs.filter(
                Q(first_name__icontains=q) |
                Q(last_name__icontains=q) |
                Q(email__icontains=q) |
                Q(phone__icontains=q)
            )
        serializer = PersonSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        data = request.data.copy()
        duplicates = PersonService.find_duplicates(data)
        if duplicates.exists():
            return Response(
                {
                    'detail': 'Une personne similaire existe déjà.',
                    'duplicates': PersonSerializer(duplicates, many=True).data,
                },
                status=status.HTTP_200_OK,
            )

        serializer = PersonSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        person = PersonService.create_person(serializer.validated_data)
        return Response(PersonSerializer(person).data, status=status.HTTP_201_CREATED)


class PersonDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get_object(self, pk):
        return Person.objects.filter(pk=pk).first()

    def get(self, request, pk):
        person = self.get_object(pk)
        if person is None:
            return Response({'detail': 'Personne introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(PersonSerializer(person).data)

    def patch(self, request, pk):
        person = self.get_object(pk)
        if person is None:
            return Response({'detail': 'Personne introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = PersonSerializer(person, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(PersonSerializer(person).data)


class PersonCreateMembershipView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def post(self, request, pk):
        person = Person.objects.filter(pk=pk).first()
        if person is None:
            return Response({'detail': 'Personne introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            member, invitation = PersonService.convert_to_member(person, request.user, request.data)
        except ValidationError as exc:
            error_detail = exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)}
            return Response(error_detail, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                'detail': 'La personne a bien été convertie en membre.',
                'member_id': str(member.id),
                'person_id': str(person.id),
                'user_id': str(member.user_id),
                'invitation_token': invitation.token,
            },
            status=status.HTTP_201_CREATED,
        )

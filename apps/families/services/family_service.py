from apps.core.models import Organization
from apps.families.models import Family
from infrastructure.tenancy import TenantContext


class FamilyService:
    @staticmethod
    def current_organization():
        organization = TenantContext.get()
        if organization is None:
            raise ValueError('Aucune organisation active n’a été sélectionnée.')
        return organization

    @staticmethod
    def create_family(data, user=None):
        organization = FamilyService.current_organization()
        family = Family.objects.create(
            organization=organization,
            name=(data.get('name') or '').strip(),
            address=data.get('address'),
            neighborhood=(data.get('neighborhood') or '').strip(),
            ministry=(data.get('ministry') or '').strip(),
            phone=data.get('phone'),
            email=(data.get('email') or '').strip().lower() or None,
            notes=data.get('notes'),
            status=data.get('status') or Family.Status.ACTIVE,
        )
        return family

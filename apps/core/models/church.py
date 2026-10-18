import uuid

from django.db import models

from apps.core.models.organizations import Organization


class Church(models.Model):
    church_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='churches')
    name = models.CharField(max_length=255)
    pastor_name = models.CharField(max_length=255, blank=True, null=True)
    slug = models.SlugField(max_length=255, unique=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    device = models.CharField(max_length=10, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    fuseau_horaire = models.CharField(max_length=100, blank=True, null=True)
    date_created = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Church"
        verbose_name_plural = "Churches"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} - {self.pastor_name} - {self.phone}"
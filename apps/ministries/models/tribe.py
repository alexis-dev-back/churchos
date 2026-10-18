from django.db import models

from .structure import ChurchStructure


class Tribe(ChurchStructure):
    church = models.ForeignKey(
        'core.Church',
        on_delete=models.CASCADE,
        related_name='tribes',
    )

    class Meta(ChurchStructure.Meta):
        verbose_name_plural = 'tribes'

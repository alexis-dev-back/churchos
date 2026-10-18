from django.db import models

from .structure import ChurchStructure


class Ministry(ChurchStructure):
    church = models.ForeignKey(
        'core.Church',
        on_delete=models.CASCADE,
        related_name='ministries',
    )

    class Meta(ChurchStructure.Meta):
        verbose_name_plural = 'ministries'

from django.conf import settings
from django.db import models


class Store(models.Model):
    name = models.CharField(max_length=150)
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_store',
    )

    def __str__(self):
        return self.name


class StoreMembership(models.Model):
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='store_memberships',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['store', 'user'], name='unique_store_membership'),
        ]

    def __str__(self):
        return f'{self.user} - {self.store}'

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user from day one — swapping later means a painful migration.

    There is no sign-up anywhere in this project: accounts exist only because
    `createsuperuser` made them. Every writing surface additionally requires
    is_staff plus a verified TOTP device.
    """

    display_name = models.CharField(
        max_length=60,
        blank=True,
        help_text="Byline shown on posts. Falls back to the username.",
    )

    def __str__(self):
        return self.display_name or self.get_username()

    @property
    def byline(self):
        return self.display_name or self.get_username()

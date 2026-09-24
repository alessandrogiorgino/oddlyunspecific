from django.db import models

from .crypto import decrypt, encrypt


class EncryptedTextField(models.TextField):
    """
    TextField that is ciphertext at rest and plaintext in Python.

    Deliberate limitation: because Fernet is randomised, `filter(body=...)`,
    `icontains`, ordering and database indexes are meaningless on this column.
    Anything that needs searching must live in a separate plaintext field, and
    must therefore be something you are willing to leak.
    """

    def from_db_value(self, value, expression, connection):  # noqa: ARG002
        if value is None or value == "":
            return value
        return decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return encrypt(str(value))

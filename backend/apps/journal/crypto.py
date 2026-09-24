"""
Envelope for the private journal.

Fernet = AES-128-CBC for confidentiality + HMAC-SHA256 for integrity, with a
random IV per message, from `cryptography`. Two consequences worth knowing:

  * The same plaintext encrypts to a different token every time, so the
    database cannot be searched or filtered on these columns. That is the
    price of the guarantee and it is paid on purpose.
  * The key never touches the database. A stolen `pg_dump`, a leaked backup
    file or a compromised Postgres container yields ciphertext and nothing
    else.

MultiFernet takes a list: the FIRST key encrypts, ANY key decrypts. Rotation is
therefore: prepend a new key to JOURNAL_ENCRYPTION_KEYS, restart, run
`make prod-rotate-journal`, then delete the old key.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_cache = {}


def get_fernet() -> MultiFernet:
    keys = tuple(getattr(settings, "JOURNAL_ENCRYPTION_KEYS", ()) or ())
    if not keys:
        raise ImproperlyConfigured(
            "JOURNAL_ENCRYPTION_KEYS is empty — the journal cannot be read or "
            "written. Generate a key with `make secrets`."
        )
    if keys not in _cache:
        _cache[keys] = MultiFernet([Fernet(key) for key in keys])
    return _cache[keys]


def encrypt(text: str) -> str:
    return get_fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise ImproperlyConfigured(
            "A journal row could not be decrypted with any key in "
            "JOURNAL_ENCRYPTION_KEYS. Either the wrong key is deployed or a key "
            "was dropped before `make prod-rotate-journal` had re-encrypted "
            "everything. Restore the old key — the data is not damaged."
        ) from exc

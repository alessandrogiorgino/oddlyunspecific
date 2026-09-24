"""
Re-encrypt every journal entry with the first key in JOURNAL_ENCRYPTION_KEYS.

Rotation procedure:

  1. make secrets                      # take only the JOURNAL_ENCRYPTION_KEYS line
  2. edit .env so it reads:  JOURNAL_ENCRYPTION_KEYS=<new>,<old>
  3. make prod-up                      # both keys loaded: new encrypts, old still decrypts
  4. make prod-rotate-journal          # rewrites every row with the new key
  5. edit .env down to:      JOURNAL_ENCRYPTION_KEYS=<new>
  6. make prod-up

Doing step 5 before step 4 makes every entry unreadable until you put the old
key back. The data is fine; the key is just missing.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.journal.models import JournalEntry


class Command(BaseCommand):
    help = "Re-encrypt journal entries with the current primary key."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Count rows, change nothing."
        )

    def handle(self, *args, **options):
        total = JournalEntry.objects.count()
        keys = len(settings.JOURNAL_ENCRYPTION_KEYS)
        self.stdout.write(f"{total} entrie(s), {keys} key(s) loaded.")

        if keys < 2:
            self.stdout.write(
                self.style.WARNING(
                    "Only one key is loaded. If you already removed the old key, "
                    "stop — put it back as the second entry first."
                )
            )

        if options["dry_run"]:
            self.stdout.write("Dry run, nothing written.")
            return

        done = 0
        with transaction.atomic():
            # No .iterator(): every row is read, decrypted with whichever key
            # works, then written back through the field, which always encrypts
            # with the primary key.
            for entry in JournalEntry.objects.all():
                entry.save(update_fields=["title", "body", "updated_at"])
                done += 1

        self.stdout.write(self.style.SUCCESS(f"Re-encrypted {done} entrie(s)."))
        self.stdout.write("Now drop the old key from JOURNAL_ENCRYPTION_KEYS.")

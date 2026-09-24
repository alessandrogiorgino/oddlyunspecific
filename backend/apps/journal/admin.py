from django.contrib import admin

from .models import JournalEntry


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    """
    Deliberately thin.

    No `search_fields` and no `list_filter` on title/body: those columns are
    randomised ciphertext, so a LIKE against them would silently match nothing
    and look like data loss. Searching the journal is done by reading it.
    """

    list_display = ("__str__", "entry_date", "pinned", "updated_at")
    list_filter = ("pinned", "entry_date")
    date_hierarchy = "entry_date"
    readonly_fields = ("created_at", "updated_at")
    fields = ("entry_date", "pinned", "title", "body", "author", "created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(author=request.user)

    def save_model(self, request, obj, form, change):
        if not obj.author_id:
            obj.author = request.user
        super().save_model(request, obj, form, change)

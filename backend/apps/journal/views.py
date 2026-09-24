from collections import OrderedDict

from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe

from apps.accounts.decorators import staff_otp_required

from .models import JournalEntry


@require_safe
@staff_otp_required
def entry_list(request):
    # Scoped to the requesting author as well as being staff-gated: if a second
    # staff account ever exists, it does not inherit the first one's journal.
    entries = JournalEntry.objects.filter(author=request.user)
    grouped = OrderedDict()
    for entry in entries:
        grouped.setdefault(entry.entry_date.year, []).append(entry)
    return render(
        request,
        "journal/entry_list.html",
        {"years": grouped, "count": len(entries)},
    )


@require_safe
@staff_otp_required
def entry_detail(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, author=request.user)
    return render(request, "journal/entry_detail.html", {"entry": entry})

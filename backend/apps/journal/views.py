import re
from collections import OrderedDict

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST, require_safe

from apps.accounts.decorators import staff_otp_required
from apps.blog.rendering import set_task

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
    return render(
        request,
        "journal/entry_detail.html",
        {"entry": entry, "body_html": _clickable_tasks(entry.html)},
    )


_TASK_LI_RE = re.compile(r'<li class="task( task--done)?">')


def _clickable_tasks(html):
    """
    Give each checklist item a submit button for the form around the body.

    No JavaScript: this page runs under the public CSP (no script at all), so
    a tick is a plain POST and a redirect back. The button's value is the item
    and the state it should end up in, so a double submit is harmless.
    `html` is already sanitised; the only markup added here is our own.
    """
    counter = iter(range(10**6))

    def repl(m):
        n, done = next(counter), bool(m.group(1))
        label = "untick" if done else "tick"
        return (
            f'<li class="task{m.group(1) or ""}" id="task-{n}">'
            f'<button class="task__box" name="task" value="{n}:{"" if done else "x"}" '
            f'aria-label="{label}" aria-pressed="{"true" if done else "false"}"></button>'
        )

    return mark_safe(_TASK_LI_RE.sub(repl, html))


@require_POST
@staff_otp_required
def toggle_task(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, author=request.user)
    url = reverse("journal:entry_detail", args=[pk])
    n, _, state = request.POST.get("task", "").partition(":")
    if not n.isdigit() or state not in ("", "x"):
        return redirect(url)
    body = set_task(entry.body, int(n), done=state == "x")
    if body is None:
        messages.error(request, "could not tick that item — edit it in the console")
        return redirect(url)
    if body != entry.body:
        entry.body = body
        entry.save(update_fields=["body", "updated_at"])
    return redirect(f"{url}#task-{n}")

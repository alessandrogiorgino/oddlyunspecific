from django.urls import path

from . import views

app_name = "journal"

urlpatterns = [
    path("", views.entry_list, name="entry_list"),
    path("<int:pk>/", views.entry_detail, name="entry_detail"),
    path("<int:pk>/task/", views.toggle_task, name="toggle_task"),
]

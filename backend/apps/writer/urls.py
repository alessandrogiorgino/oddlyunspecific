from django.urls import path

from . import api, views

app_name = "writer"

urlpatterns = [
    path("", views.console, name="console"),
    path("login/", views.ConsoleLoginView.as_view(), name="login"),
    path("logout/", views.ConsoleLogoutView.as_view(), name="logout"),
    # JSON API for the console.
    path("api/state/", api.state, name="api_state"),
    path("api/posts/", api.posts, name="api_posts"),
    path("api/posts/<int:pk>/", api.post_detail, name="api_post_detail"),
    path("api/posts/<int:pk>/publish/", api.post_publish, name="api_post_publish"),
    path("api/posts/<int:pk>/unpublish/", api.post_unpublish, name="api_post_unpublish"),
    path("api/journal/", api.journal, name="api_journal"),
    path("api/journal/<int:pk>/", api.journal_detail, name="api_journal_detail"),
    path("api/preview/", api.preview, name="api_preview"),
    path("api/upload/", api.upload, name="api_upload"),
]

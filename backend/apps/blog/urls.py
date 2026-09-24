from django.urls import path

from . import views
from .feeds import PostFeed

app_name = "blog"

urlpatterns = [
    path("", views.post_list, name="post_list"),
    path("feed/", PostFeed(), name="feed"),
    path("tag/<slug:slug>/", views.tag, name="tag"),
    # Last, and at the site root: posts get clean URLs like /on-quiet-code/.
    # RESERVED_SLUGS in models.py stops a post from shadowing a real route.
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]

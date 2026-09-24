from django.contrib import admin
from django.utils import timezone

from .models import Post, Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "post_count")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)

    @admin.display(description="posts")
    def post_count(self, obj):
        return obj.posts.count()


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "published_at", "words", "author")
    list_filter = ("status", "tags", "author")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("tags",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "words")
    actions = ("publish", "unpublish")
    fieldsets = (
        (None, {"fields": ("title", "slug", "summary", "body")}),
        ("Publishing", {"fields": ("status", "published_at", "author", "tags")}),
        ("Meta", {"fields": ("words", "created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        if not change and not obj.author_id:
            obj.author = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Publish selected posts")
    def publish(self, request, queryset):
        now = timezone.now()
        # Looped rather than .update() so Post.save() re-renders the markdown
        # and stamps published_at.
        for post in queryset:
            post.status = Post.Status.PUBLISHED
            if post.published_at is None:
                post.published_at = now
            post.save()
        self.message_user(request, f"Published {queryset.count()} post(s).")

    @admin.action(description="Move selected posts back to draft")
    def unpublish(self, request, queryset):
        for post in queryset:
            post.status = Post.Status.DRAFT
            post.save()
        self.message_user(request, f"Unpublished {queryset.count()} post(s).")

from django.apps import AppConfig


class WriterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.writer"
    label = "writer"
    verbose_name = "writer console"

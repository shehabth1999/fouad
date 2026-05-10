from django.apps import AppConfig


class ElFouadConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'el_fouad'
    verbose_name = 'El Fouad'

    def ready(self):
        super().ready()
        from . import extensions  # noqa: F401

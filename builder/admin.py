from django.contrib import admin

from .models import GenerationJob


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'phase', 'created_at', 'error_message')
    list_filter = ('status',)
    readonly_fields = ('prompt', 'output_dir', 'blender_exe', 'blend_file', 'render_file',
                       'exit_code', 'error_message')

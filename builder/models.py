from django.db import models


class GenerationJob(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_QUEUED, STATUS_QUEUED),
        (STATUS_RUNNING, STATUS_RUNNING),
        (STATUS_SUCCESS, STATUS_SUCCESS),
        (STATUS_FAILED, STATUS_FAILED),
    ]

    prompt = models.TextField()
    plan_json = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    phase = models.CharField(max_length=100, blank=True, default='')
    output_dir = models.CharField(max_length=500, blank=True, default='')
    blender_exe = models.CharField(max_length=500, blank=True, default='')
    blend_file = models.CharField(max_length=260, blank=True, default='')
    render_file = models.CharField(max_length=260, blank=True, default='')
    exit_code = models.IntegerField(null=True, blank=True)
    result_json = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Job {self.id}: {self.status}'

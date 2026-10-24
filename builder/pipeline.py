"""Coordinates the full generation pipeline: create a job, run it, store results."""
import json
import threading
import traceback
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from .blender import detect
from .blender import runner
from .models import GenerationJob


def _update_job(job, **fields):
    for field, value in fields.items():
        setattr(job, field, value)
    job.save(update_fields=list(fields) + ['updated_at'])


def _fail_job(job, message):
    _update_job(
        job,
        status=GenerationJob.STATUS_FAILED,
        phase='failed',
        error_message=message,
    )


def start_job(prompt, plan=None, spawn=True):
    """Create a GenerationJob row (and optionally start it in a background thread)."""
    job = GenerationJob.objects.create(prompt=prompt, plan_json=plan)
    job.output_dir = str(Path(settings.GENERATED_DIR) / ('job_%d' % job.id))
    job.save(update_fields=['output_dir', 'updated_at'])

    if spawn:
        thread = threading.Thread(target=execute_generation, args=(job.id,), daemon=True)
        thread.start()
    return job


def execute_generation(job_id):
    """Run the Blender pipeline for a job and update its status.

    Runs in a background thread in production; called directly (synchronously)
    in tests so failures can be asserted deterministically.
    """
    close_old_connections()
    job = GenerationJob.objects.get(pk=job_id)

    try:
        blender_exe, detection_error = detect.find_blender()
        if not blender_exe:
            _fail_job(job, detection_error)
            return

        _update_job(
            job,
            status=GenerationJob.STATUS_RUNNING,
            phase='running_blender',
            blender_exe=blender_exe,
        )

        plan_file = None
        if job.plan_json:
            plan_path = Path(job.output_dir) / 'plan.json'
            Path(job.output_dir).mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(job.plan_json), encoding='utf-8')
            plan_file = plan_path

        result = runner.run_blender_generation(
            exe=blender_exe,
            out_dir=Path(job.output_dir),
            plan_file=plan_file,
        )

        _update_job(
            job,
            status=GenerationJob.STATUS_SUCCESS,
            phase='done',
            exit_code=result.get('exit_code'),
            result_json=result,
            blend_file=result.get('blend_file', ''),
            render_file=result.get('render_file', ''),
            error_message='',
        )
    except Exception as exc:
        _fail_job(job, str(exc) or repr(exc))
        print('Job %s failed: %s' % (job_id, traceback.format_exc()))
    finally:
        close_old_connections()


def job_status_payload(job: GenerationJob):
    """Serialize a job for the status endpoint."""
    result = job.result_json or {}
    out_dir = Path(job.output_dir)
    has_render = bool(job.render_file) and (out_dir / job.render_file).is_file()
    has_blend = bool(job.blend_file) and (out_dir / job.blend_file).is_file()
    has_plan = bool(job.plan_json)
    base = ('/api/job/%d/' % job.id)
    objects = result.get('objects') or []
    return {
        'job_id': job.id,
        'status': job.status,
        'phase': job.phase,
        'prompt': job.prompt,
        'created_at': timezone.localtime(job.created_at).isoformat(),
        'error_message': job.error_message,
        'has_plan': has_plan,
        'result': {
            'render_url': base + 'render/' if has_render else None,
            'blend_url': base + 'download/' if has_blend else None,
            'plan_url': base + 'plan/' if has_plan else None,
            'objects': objects[:12],
            'object_count': result.get('object_count', len(objects)),
            'summary': result.get('summary'),
            'engine': result.get('engine'),
            'resolution': result.get('resolution'),
            'generation_seconds': result.get('generation_seconds'),
        },
    }

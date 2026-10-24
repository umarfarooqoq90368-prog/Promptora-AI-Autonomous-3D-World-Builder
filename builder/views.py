import json
from pathlib import Path

from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST, require_GET

from . import pipeline
from .blender.detect import find_blender
from .models import GenerationJob
from .plan.schema import PlanValidationError, validate_plan

MAX_PROMPT_LENGTH = 2000


def home(request):
    blender_exe, blender_error = find_blender()
    return render(request, 'builder/index.html', {
        'app_version': 'Milestone 3: cube pipeline',
        'blender_found': bool(blender_exe),
        'blender_note': (blender_exe or blender_error).splitlines()[0],
    })


def _read_prompt(request):
    if request.content_type == 'application/json':
        try:
            body = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None, None
        if not isinstance(body, dict):
            return None, None
        return body.get('prompt'), body.get('plan')
    return request.POST.get('prompt'), request.POST.get('plan')


@require_POST
def api_generate(request):
    prompt, plan = _read_prompt(request)
    prompt = (prompt or '').strip()
    if not prompt:
        return JsonResponse({'error': 'Prompt must not be empty.'}, status=400)
    if len(prompt) > MAX_PROMPT_LENGTH:
        return JsonResponse({
            'error': 'Prompt is too long (max %d characters).' % MAX_PROMPT_LENGTH,
        }, status=400)

    if plan is not None:
        try:
            plan = validate_plan(plan)
        except PlanValidationError as validation_error:
            return JsonResponse({
                'error': 'Invalid scene plan.',
                'plan_errors': list(validation_error.args[0]),
            }, status=400)

    blender_exe, blender_error = find_blender()
    if not blender_exe:
        return JsonResponse({
            'error': 'Blender executable not found — generation cannot start.',
            'instructions': blender_error,
        }, status=400)

    job = pipeline.start_job(prompt, plan=plan, spawn=True)
    return JsonResponse({
        'job_id': job.id,
        'status': job.status,
        'status_url': '/api/job/%d/status/' % job.id,
    }, status=201)


@require_GET
def api_job_status(request, job_id):
    job = get_object_or_404(GenerationJob, pk=job_id)
    return JsonResponse(pipeline.job_status_payload(job))


@require_GET
def api_job_plan(request, job_id):
    job = get_object_or_404(GenerationJob, pk=job_id)
    if not job.plan_json:
        raise Http404('No scene plan stored for this job.')
    content = json.dumps(job.plan_json, indent=2)
    response = HttpResponse(content, content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="promptora_plan_%d.json"' % job.id
    return response


@require_GET
def api_job_render(request, job_id):
    job = get_object_or_404(GenerationJob, pk=job_id)
    if not job.render_file:
        raise Http404('No render available for this job.')
    path = Path(job.output_dir) / job.render_file
    if not path.is_file():
        raise Http404('Render file no longer exists.')
    with path.open('rb') as handle:
        content = handle.read()
    return HttpResponse(content, content_type='image/png')


@require_GET
def api_job_download(request, job_id):
    job = get_object_or_404(GenerationJob, pk=job_id)
    if not job.blend_file:
        raise Http404('No .blend file available for this job.')
    path = Path(job.output_dir) / job.blend_file
    if not path.is_file():
        raise Http404('.blend file no longer exists.')
    filename = 'promptora_scene_%d.blend' % job.id
    return _serve_binary(path, filename)


def _serve_binary(path: Path, filename: str):
    """Read a small file fully, close the handle (Windows locks open files), and return it."""
    with path.open('rb') as handle:
        content = handle.read()
    response = HttpResponse(content, content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="%s"' % filename
    return response

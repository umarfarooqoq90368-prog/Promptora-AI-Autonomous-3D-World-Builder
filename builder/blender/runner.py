"""Runs Blender as a background subprocess and validates its outputs."""
import json
import subprocess
from pathlib import Path

from django.conf import settings

SCRIPT_PATH = Path(__file__).resolve().parent / 'scripts' / 'build_scene.py'

CREATE_NO_WINDOW = 0x08000000


class BlenderError(Exception):
    """Raised with a user-friendly message whenever a generation step fails."""


def run_blender_generation(exe: str, out_dir: Path, plan_file: Path = None):
    """Launch Blender in background mode, then validate the produced files.

    Returns the parsed result.json content on success; raises BlenderError otherwise.
    """
    cmd = [exe, '-b', '-P', str(SCRIPT_PATH), '--', '--output', str(out_dir)]
    if plan_file is not None:
        cmd += ['--plan', str(plan_file)]
    timeout = int(getattr(settings, 'BLENDER_TIMEOUT', 480))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
    except subprocess.TimeoutExpired:
        raise BlenderError(
            'Blender timed out after %s seconds. The process was stopped.' % timeout
        ) from None

    exit_code = proc.returncode if proc.returncode is not None else -1
    if exit_code != 0:
        message = 'Blender exited with code %s. ' % exit_code
        message += 'Error details: %s' % (proc.stderr or proc.stdout or 'no output captured').strip()[-1500:]
        raise BlenderError(message)

    return validate_outputs(out_dir, exit_code)


def validate_outputs(out_dir: Path, exit_code: int):
    """Check that Blender produced a usable result.json, .blend file and render PNG."""
    out_dir = Path(out_dir)
    result_file = out_dir / 'result.json'
    if not result_file.is_file():
        raise BlenderError('Blender finished but result.json is missing in %s' % out_dir)

    try:
        result = json.loads(result_file.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise BlenderError('result.json is not valid JSON: %s' % exc) from None

    if result.get('status') != 'ok':
        raise BlenderError('Blender reported an error: %s' % result.get('error', 'unknown'))

    blend_path = out_dir / result.get('blend_file', '')
    if not blend_path.is_file() or blend_path.stat().st_size < 500:
        raise BlenderError('The .blend file is missing or empty: %s' % blend_path)

    render_path = out_dir / result.get('render_file', '')
    if not render_path.is_file():
        raise BlenderError('Preview render is missing: %s' % render_path)
    if render_path.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
        raise BlenderError('Preview render is not a valid PNG: %s' % render_path)

    if not result.get('objects'):
        raise BlenderError('result.json contains no objects')

    result['exit_code'] = exit_code
    return result

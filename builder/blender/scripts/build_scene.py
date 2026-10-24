"""Builds scenes inside Blender.

Executed by Blender itself (never by Django):
    blender.exe -b -P build_scene.py -- --output "C:/path/to/job_dir" [--plan plan.json]

Without --plan it builds the classic demo cube scene.
With --plan it builds a full procedural city from the validated plan JSON.
It always saves scene.blend, renders render.png and writes result.json.
"""

import json
import os
import sys
import time
from pathlib import Path

import bpy

# Blender does not put this script's folder on sys.path in background mode.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PREVIEW_ENGINES = ['BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'BLENDER_WORKBENCH']


def parse_args():
    out_dir = Path.cwd() / 'generated'
    plan_file = None
    if '--' in sys.argv:
        tail = sys.argv[sys.argv.index('--') + 1:]
        if '--output' in tail:
            out_dir = Path(tail[tail.index('--output') + 1])
        if '--plan' in tail:
            plan_file = Path(tail[tail.index('--plan') + 1])
    return out_dir, plan_file


def build_demo_cube():
    """The original single-cube demo, unchanged in behavior."""
    import city_templates
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    world = bpy.data.worlds.new('World')
    world.use_nodes = True
    world.node_tree.nodes.get('Background').inputs['Color'].default_value = (0.04, 0.05, 0.08, 1.0)
    scene.world = world

    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
    cube = bpy.context.active_object
    cube.name = 'DemoCube'
    cube.data.materials.append(city_templates.make_material(
        'CubeMaterial', (0.05, 0.35, 0.9), metallic=0.1, roughness=0.35))

    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = 'Ground'

    sun = bpy.data.lights.new('SunLight', type='SUN')
    sun.energy = 2.5
    sun.angle = 0.15
    sun_obj = bpy.data.objects.new('Sun', sun)
    sun_obj.rotation_euler = (0.785, 0.15, 0.57)
    scene.collection.objects.link(sun_obj)

    area = bpy.data.lights.new('FillLight', type='AREA')
    area.energy = 300
    area.size = 8
    fill_obj = bpy.data.objects.new('FillLight', area)
    fill_obj.location = (5, -3, 7)
    fill_obj.rotation_euler = (1.0, 0.3, 0.8)
    scene.collection.objects.link(fill_obj)

    camera_data = bpy.data.cameras.new('Camera')
    camera_data.lens = 50
    camera_obj = bpy.data.objects.new('Camera', camera_data)
    camera_obj.location = (11, -11, 7)
    camera_obj.rotation_euler = (1.11, 0.0, 0.785)
    scene.collection.objects.link(camera_obj)
    scene.camera = camera_obj

    names = [obj.name for obj in scene.collection.objects]
    summary = {
        'scene_type': 'cube_demo',
        'total_objects': len(names),
        'camera': camera_obj.name,
        'lights': ['Sun', 'FillLight'],
    }
    return scene, names, summary


def build_plan_scene(plan_data):
    import city_templates
    return city_templates.build_city_scene(plan_data)


def configure_render(scene, out_dir, resolution_x, resolution_y, samples):
    scene.render.resolution_x = resolution_x
    scene.render.resolution_y = resolution_y
    scene.render.image_settings.file_format = 'PNG'
    scene.render.use_file_extension = False
    scene.render.filepath = str(out_dir / 'render.png')
    if samples:
        try:
            scene.eevee.taa_render_samples = int(samples)
        except (AttributeError, TypeError):
            pass

    engine = None
    for candidate in PREVIEW_ENGINES:
        try:
            scene.render.engine = candidate
            bpy.ops.render.render(write_still=True)
            engine = candidate
            break
        except (RuntimeError, TypeError):
            continue
    if engine is None:
        raise RuntimeError('No available render engine (tried: %s)' % ', '.join(PREVIEW_ENGINES))
    return engine


def main():
    out_dir, plan_file = parse_args()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {'status': 'error', 'error': '', 'objects': []}

    started = time.time()
    try:
        if plan_file is not None:
            plan_data = json.loads(Path(plan_file).read_text(encoding='utf-8'))
            object_names, summary, _city_seconds = build_plan_scene(plan_data)
            resolution_x = (plan_data.get('render') or {}).get('resolution_x', 960)
            resolution_y = (plan_data.get('render') or {}).get('resolution_y', 540)
            samples = (plan_data.get('render') or {}).get('samples', 32)
        else:
            scene, object_names, summary = build_demo_cube()
            resolution_x, resolution_y, samples = 960, 540, 32

        scene = bpy.context.scene
        bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / 'scene.blend'))
        engine = configure_render(scene, out_dir, resolution_x, resolution_y, samples)

        blend_path = out_dir / 'scene.blend'
        if not blend_path.is_file():
            raise RuntimeError('scene.blend was not written')
        render_files = list(out_dir.glob('render*.png'))
        if not render_files:
            raise RuntimeError('render PNG was not written')

        result = {
            'status': 'ok',
            'objects': object_names,
            'object_count': len(object_names),
            'summary': summary,
            'scene_type': summary.get('scene_type'),
            'blend_file': blend_path.name,
            'render_file': render_files[0].name,
            'engine': engine,
            'resolution': [resolution_x, resolution_y],
            'generation_seconds': round(time.time() - started, 1),
        }
    except Exception as exc:
        result['error'] = str(exc)

    (out_dir / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if result['status'] != 'ok':
        print('RESULT_ERROR: %s' % result['error'], file=sys.stderr)
        sys.exit(1)
    print('Generation complete: %d objects in %s s' % (
        result['object_count'], result['generation_seconds']))


main()

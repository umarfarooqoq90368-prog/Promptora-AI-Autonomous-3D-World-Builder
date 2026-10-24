import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings

from . import pipeline
from .blender import detect
from .blender import runner
from .models import GenerationJob
from .plan.schema import PlanValidationError, validate_plan

PNG_MAGIC = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40


def make_valid_output(job_dir: Path):
    """Create the three files a successful Blender run is expected to leave behind."""
    job_dir.mkdir(parents=True, exist_ok=True)
    blend = job_dir / 'scene.blend'
    blend.write_bytes(b'BLENDER-V450' + b'x' * 600)
    render = job_dir / 'render.png'
    render.write_bytes(PNG_MAGIC)
    result = {
        'status': 'ok',
        'objects': ['DemoCube', 'Ground', 'Sun', 'FillLight', 'Camera'],
        'blend_file': 'scene.blend',
        'render_file': 'render.png',
        'engine': 'BLENDER_EEVEE_NEXT',
    }
    (job_dir / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    return result


class DetectBlenderTests(TestCase):
    def test_configured_env_var_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'blender.exe'
            exe.write_bytes(b'MZ')
            with override_settings(BLENDER_EXE=str(exe)):
                with mock.patch.object(detect, 'COMMON_ROOTS', []):
                    found, error = detect.find_blender()
                    self.assertEqual(found, str(exe))
                    self.assertEqual(error, '')

    def test_configured_env_var_missing_reports_setup_error(self):
        with override_settings(BLENDER_EXE=r'C:\definitely\not\here\blender.exe'):
            with mock.patch.object(detect, 'COMMON_ROOTS', []):
                found, error = detect.find_blender()
                self.assertIsNone(found)
                self.assertIn('BLENDER_EXE', error)
                self.assertIn('blender.org', error)

    def test_scans_common_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Blender 3.6' / 'blender.exe').parent.mkdir(parents=True)
            (root / 'Blender 3.6' / 'blender.exe').write_bytes(b'MZ')
            (root / 'Blender 9.0' / 'blender.exe').parent.mkdir(parents=True)
            (root / 'Blender 9.0' / 'blender.exe').write_bytes(b'MZ')
            with mock.patch.object(detect, 'COMMON_ROOTS', [str(root)]):
                with override_settings(BLENDER_EXE=''):
                    found, error = detect.find_blender()
                    self.assertEqual(found, str(root / 'Blender 9.0' / 'blender.exe'))
                    self.assertEqual(error, '')

    def test_not_installed_anywhere(self):
        with override_settings(BLENDER_EXE=''):
            with mock.patch.object(detect, 'COMMON_ROOTS', [r'C:\no\blender\here']):
                found, error = detect.find_blender()
                self.assertIsNone(found)
                self.assertIn('winget install BlenderFoundation.Blender', error)


class RunnerValidationTests(TestCase):
    def test_valid_outputs_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = make_valid_output(Path(tmp))
            validated = runner.validate_outputs(Path(tmp), exit_code=0)
            self.assertEqual(validated['status'], 'ok')
            self.assertIn('exit_code', validated)

    def test_missing_result_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesMessage(runner.BlenderError, 'result.json'):
                runner.validate_outputs(Path(tmp), exit_code=0)

    def test_invalid_result_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'result.json').write_bytes(b'{not json')
            with self.assertRaisesMessage(runner.BlenderError, 'not valid JSON'):
                runner.validate_outputs(Path(tmp), exit_code=0)

    def test_result_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'result.json').write_text(json.dumps({
                'status': 'error', 'error': 'Render engine exploded',
            }))
            with self.assertRaisesMessage(runner.BlenderError, 'Render engine exploded'):
                runner.validate_outputs(Path(tmp), exit_code=0)

    def test_missing_render_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_valid_output(job_dir)
            (job_dir / 'render.png').unlink()
            with self.assertRaisesMessage(runner.BlenderError, 'render'):
                runner.validate_outputs(job_dir, exit_code=0)

    def test_invalid_png_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_valid_output(job_dir)
            (job_dir / 'render.png').write_bytes(b'not a png at all')
            with self.assertRaisesMessage(runner.BlenderError, 'not a valid PNG'):
                runner.validate_outputs(job_dir, exit_code=0)

    def test_missing_blend_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_valid_output(job_dir)
            (job_dir / 'scene.blend').unlink()
            with self.assertRaisesMessage(runner.BlenderError, 'missing or empty'):
                runner.validate_outputs(job_dir, exit_code=0)


class PipelineFailureTests(TestCase):
    def make_job(self) -> GenerationJob:
        return GenerationJob.objects.create(prompt='demo prompt')

    def test_missing_blender_marks_job_failed_with_instructions(self):
        job = self.make_job()
        with mock.patch.object(detect, 'find_blender', return_value=(None, 'INSTALL INSTRUCTIONS HERE')):
            with tempfile.TemporaryDirectory() as tmp:
                with override_settings(GENERATED_DIR=tmp):
                    job.output_dir = Path(tmp) / 'job_1'
                    job.save()
                    pipeline.execute_generation(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, GenerationJob.STATUS_FAILED)
        self.assertIn('INSTALL INSTRUCTIONS HERE', job.error_message)

    def test_nonzero_exit_code_captures_blender_stderr(self):
        job = self.make_job()
        fake_run = mock.Mock(returncode=1, stdout='some progress', stderr='Engine: failure on material X')
        with mock.patch.object(detect, 'find_blender', return_value=(r'C:\fake\blender.exe', '')):
            with mock.patch.object(runner.subprocess, 'run', return_value=fake_run):
                pipeline.execute_generation(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, GenerationJob.STATUS_FAILED)
        self.assertIn('Engine: failure on material X', job.error_message)
        self.assertIn('code 1', job.error_message)
        self.assertEqual(job.phase, 'failed')

    def test_timeout_stops_job_with_clear_message(self):
        job = self.make_job()
        with mock.patch.object(detect, 'find_blender', return_value=(r'C:\fake\blender.exe', '')):
            with mock.patch.object(
                runner.subprocess, 'run',
                side_effect=subprocess.TimeoutExpired(cmd='blender', timeout=240),
            ):
                pipeline.execute_generation(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, GenerationJob.STATUS_FAILED)
        self.assertIn('timed out', job.error_message)

    def test_successful_run_marks_job_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.settings(GENERATED_DIR=tmp):
                job = pipeline.start_job('demo prompt', spawn=False)
                self.assertEqual(job.status, GenerationJob.STATUS_QUEUED)
                make_valid_output(Path(job.output_dir))
                with mock.patch.object(detect, 'find_blender', return_value=(r'C:\fake\blender.exe', '')):
                    with mock.patch.object(
                        runner.subprocess, 'run',
                        return_value=mock.Mock(returncode=0, stdout='', stderr=''),
                    ):
                        pipeline.execute_generation(job.id)
                    job.refresh_from_db()
                    self.assertEqual(job.status, GenerationJob.STATUS_SUCCESS)
                    self.assertEqual(job.blend_file, 'scene.blend')
                    self.assertEqual(job.render_file, 'render.png')
                    self.assertEqual(job.result_json['engine'], 'BLENDER_EEVEE_NEXT')


class ScenePlanSchemaTests(TestCase):
    def test_minimal_plan_gets_sane_defaults(self):
        plan = validate_plan({'scene_type': 'city', 'style': 'futuristic'})
        self.assertEqual(plan['buildings']['count'], 10)
        self.assertEqual(plan['ground']['size'], 60.0)
        self.assertEqual(plan['roads']['spacing'], 14.0)
        self.assertEqual(plan['seed'], 1234)

    def test_rejects_unknown_keys(self):
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'scene_type': 'city', 'exec_code': 'import os'})
        self.assertIn('Unknown plan keys', str(ctx.exception))
        self.assertIn('exec_code', str(ctx.exception))

    def test_rejects_bad_scene_type_and_style(self):
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'scene_type': 'galaxy'})
        self.assertIn('scene_type', str(ctx.exception))
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'style': 'cybergrunge'})
        self.assertIn('style', str(ctx.exception))

    def test_enforces_building_count_limit(self):
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'buildings': {'count': 41}})
        self.assertIn('buildings.count', str(ctx.exception))

    def test_enforces_height_order(self):
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'buildings': {'height_min': 10, 'height_max': 5}})
        self.assertIn('height_min', str(ctx.exception))

    def test_enforces_footprint_fits_between_roads(self):
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'roads': {'spacing': 10}, 'buildings': {'footprint_max': 9}})
        self.assertIn('footprint_max', str(ctx.exception))

    def test_rejects_bad_hex_colors(self):
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'palette': {'building_colors': ['#GGHHII', 'blue']}})
        self.assertIn('hex color', str(ctx.exception))

    def test_rejects_resolution_over_pixel_budget(self):
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'render': {'resolution_x': 1280, 'resolution_y': 720 + 1}})
        self.assertIn('resolution', str(ctx.exception))

    def test_floats_are_normalized(self):
        plan = validate_plan({
            'scene_type': 'city',
            'buildings': {'count': 12.6, 'height_min': 3, 'height_max': 9.05},
            'robots': {'count': 4.0},
            'palette': {'building_colors': ['#324A5E']},
        })
        self.assertEqual(plan['buildings']['count'], 13)
        self.assertEqual(plan['robots']['count'], 4)
        self.assertEqual(plan['palette']['building_colors'], ['#324a5e'])
        self.assertEqual(plan['buildings']['height_min'], 3.0)

    def test_negative_robot_count_rejected(self):
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan({'robots': {'count': -1}})
        self.assertIn('robots.count', str(ctx.exception))

    def test_non_dict_plan_rejected(self):
        with self.assertRaises(PlanValidationError):
            validate_plan(['not', 'a', 'dict'])


class PipelinePlanFlowTests(TestCase):
    def test_execute_generation_writes_plan_and_passes_it_to_runner(self):
        valid_plan = validate_plan({'scene_type': 'city', 'buildings': {'count': 10}})
        captured = {}

        def fake_run(exe, out_dir, plan_file=None):
            captured['plan_file'] = plan_file
            if plan_file is not None:
                captured['plan_content'] = json.loads(Path(plan_file).read_text(encoding='utf-8'))
            return {
                'status': 'ok', 'objects': ['Building_0'], 'object_count': 1,
                'summary': {'buildings': 1}, 'blend_file': 'scene.blend',
                'render_file': 'render.png', 'engine': 'BLENDER_EEVEE_NEXT',
                'generation_seconds': 10.0, 'resolution': [960, 540], 'exit_code': 0,
            }

        with tempfile.TemporaryDirectory() as tmp:
            with self.settings(GENERATED_DIR=tmp):
                job = pipeline.start_job('city job', plan=valid_plan, spawn=False)
                with mock.patch.object(detect, 'find_blender', return_value=(r'C:\fake\blender.exe', '')):
                    with mock.patch(
                        'builder.pipeline.runner.run_blender_generation',
                        side_effect=fake_run,
                    ):
                        pipeline.execute_generation(job.id)
                job.refresh_from_db()
                plan_existed = captured['plan_file'].is_file()

        self.assertEqual(job.status, GenerationJob.STATUS_SUCCESS)
        self.assertTrue(plan_existed)
        self.assertEqual(captured['plan_content']['buildings']['count'], 10)
        self.assertEqual(job.result_json['summary']['buildings'], 1)
        self.assertEqual(job.blend_file, 'scene.blend')
        self.assertEqual(job.render_file, 'render.png')

    def test_execute_generation_without_plan_keeps_cube_flow(self):
        def fake_run(exe, out_dir, plan_file=None):
            return {
                'status': 'ok', 'objects': ['DemoCube'], 'object_count': 5,
                'summary': {'scene_type': 'cube_demo'}, 'blend_file': 'scene.blend',
                'render_file': 'render.png', 'engine': 'BLENDER_EEVEE_NEXT',
                'exit_code': 0,
            }

        with tempfile.TemporaryDirectory() as tmp:
            with self.settings(GENERATED_DIR=tmp):
                job = pipeline.start_job('cube demo', spawn=False)
                with mock.patch.object(detect, 'find_blender', return_value=(r'C:\fake\blender.exe', '')):
                    with mock.patch(
                        'builder.pipeline.runner.run_blender_generation',
                        side_effect=fake_run,
                    ) as run_mock:
                        pipeline.execute_generation(job.id)
                job.refresh_from_db()

        run_mock.assert_called_once()
        self.assertIsNone(run_mock.call_args.kwargs.get('plan_file'))
        self.assertEqual(job.status, GenerationJob.STATUS_SUCCESS)
    def setUp(self):
        self.client = Client()

    def test_generate_creates_job_and_returns_201(self):
        with mock.patch('builder.views.find_blender', return_value=(r'C:\fake\blender.exe', '')):
            with mock.patch.object(
                pipeline, 'start_job',
                side_effect=lambda prompt, **kwargs: GenerationJob.objects.create(
                    prompt=prompt,
                    output_dir='C:\\tmp\\job_dummy',
                ),
            ) as start_job_mock:
                response = self.client.post('/api/generate/', {'prompt': 'make a cube'})
                self.assertEqual(response.status_code, 201)
                data = response.json()
                self.assertIn('job_id', data)
                self.assertIn('status_url', data)
            start_job_mock.assert_called_once()
            self.assertTrue(start_job_mock.call_args.kwargs.get('spawn', False))

    def test_generate_with_valid_plan_normalizes_and_passes_it_on(self):
        with mock.patch('builder.views.find_blender', return_value=(r'C:\fake\blender.exe', '')):
            with mock.patch.object(
                pipeline, 'start_job',
                side_effect=lambda prompt, **kwargs: GenerationJob.objects.create(
                    prompt=prompt, output_dir='C:\\tmp\\job_dummy'),
            ) as start_job_mock:
                response = self.client.post(
                    '/api/generate/',
                    data=json.dumps({
                        'prompt': 'city please',
                        'plan': {'scene_type': 'city', 'style': 'robotic',
                                 'buildings': {'count': 12.6, 'height_min': 3, 'height_max': 9}},
                    }),
                    content_type='application/json',
                )
            self.assertEqual(response.status_code, 201)
            passed_plan = start_job_mock.call_args.kwargs.get('plan')
            self.assertEqual(passed_plan['buildings']['count'], 13)
            self.assertEqual(passed_plan['style'], 'robotic')

    def test_generate_with_invalid_plan_returns_400_and_no_job(self):
        with mock.patch('builder.views.find_blender', return_value=(r'C:\fake\blender.exe', '')):
            with mock.patch.object(pipeline, 'start_job') as start_job_mock:
                response = self.client.post(
                    '/api/generate/',
                    data=json.dumps({
                        'prompt': 'city please',
                        'plan': {'scene_type': 'city', 'buildings': {'count': 500},
                                 'sneaky_key': 'x'},
                    }),
                    content_type='application/json',
                )
            self.assertEqual(response.status_code, 400)
            data = response.json()
            self.assertEqual(data['error'], 'Invalid scene plan.')
            self.assertTrue(any('sneaky_key' in line for line in data['plan_errors']))
            start_job_mock.assert_not_called()
            self.assertEqual(GenerationJob.objects.count(), 0)

    def test_plan_endpoint_serves_validated_plan(self):
        job = GenerationJob.objects.create(prompt='p', plan_json={'scene_type': 'city'})
        response = self.client.get('/api/job/%d/plan/' % job.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'scene_type': 'city'})

    def test_plan_endpoint_404_without_plan(self):
        job = GenerationJob.objects.create(prompt='p')
        response = self.client.get('/api/job/%d/plan/' % job.id)
        self.assertEqual(response.status_code, 404)

    def test_status_payload_includes_summary_and_caps_objects(self):
        objects = ['Obj_%d' % i for i in range(15)]
        result = {
            'status': 'ok', 'objects': objects, 'object_count': 15,
            'summary': {'buildings': 12, 'robots': 4},
            'engine': 'BLENDER_EEVEE_NEXT', 'generation_seconds': 41.2,
            'resolution': [960, 540],
        }
        job = GenerationJob.objects.create(
            prompt='p', status=GenerationJob.STATUS_SUCCESS,
            blend_file='scene.blend', render_file='render.png', result_json=result)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp).mkdir(parents=True, exist_ok=True)
            job.output_dir = tmp
            job.save()
            make_dummy_files(job)
            data = self.client.get('/api/job/%d/status/' % job.id).json()
        self.assertEqual(len(data['result']['objects']), 12)
        self.assertEqual(data['result']['object_count'], 15)
        self.assertEqual(data['result']['summary']['buildings'], 12)
        self.assertEqual(data['result']['generation_seconds'], 41.2)


def make_dummy_files(job):
    job_dir = Path(job.output_dir)
    (job_dir / 'scene.blend').write_bytes(b'BLENDER-V450' + b'x' * 600)
    (job_dir / 'render.png').write_bytes(PNG_MAGIC)

    def test_generate_empty_prompt_returns_400(self):
        response = self.client.post('/api/generate/', {'prompt': '   '})
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_generate_missing_blender_returns_400_with_instructions(self):
        with mock.patch('builder.views.find_blender', return_value=(None, 'SETUP INSTRUCTIONS')):
            response = self.client.post('/api/generate/', {'prompt': 'make a cube'})
            self.assertEqual(response.status_code, 400)
            data = response.json()
            self.assertIn('Blender executable not found', data['error'])
            self.assertIn('instructions', data)

    def test_status_endpoint_reports_job_state(self):
        job = GenerationJob.objects.create(prompt='p', status=GenerationJob.STATUS_RUNNING, phase='running_blender')
        response = self.client.get('/api/job/%d/status/' % job.id)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'running')
        self.assertEqual(data['phase'], 'running_blender')

    def test_status_endpoint_404_for_unknown_job(self):
        response = self.client.get('/api/job/99999/status/')
        self.assertEqual(response.status_code, 404)

    def test_status_render_and_download_when_files_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.settings(GENERATED_DIR=tmp):
                job = pipeline.start_job('demo', spawn=False)
                result = make_valid_output(Path(job.output_dir))
                job.status = GenerationJob.STATUS_SUCCESS
                job.blend_file = 'scene.blend'
                job.render_file = 'render.png'
                job.result_json = result
                job.save()

                status = self.client.get('/api/job/%d/status/' % job.id).json()
                self.assertTrue(status['result']['render_url'])
                self.assertTrue(status['result']['blend_url'])
                self.assertIn('DemoCube', status['result']['objects'])

                render = self.client.get(status['result']['render_url'])
                self.assertEqual(render.status_code, 200)
                self.assertTrue(render.content.startswith(PNG_MAGIC))

                download = self.client.get(status['result']['blend_url'])
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download['Content-Disposition'].split('filename=')[1].strip('"'),
                                 'promptora_scene_%d.blend' % job.id)

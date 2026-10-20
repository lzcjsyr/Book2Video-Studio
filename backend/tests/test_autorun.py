"""Automatic production: steps 2–6 run in order through the real job system, with a stand-in for the engine."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import quote

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import autorun
import headless
from server import RENDER_COVERS, Studio, Handler, Conflict, atomic_json
from test_covers import HTML as COVER_HTML

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'
SMALL_HTML_COVERS = {'cover_method': 'agent', 'cover_image_size_presets': {'16:9': '640x360'}, 'cover_image_size_keys': ['16:9'], 'cover_image_count': 1}
RAW = {'source_name': '自动之书', 'content': '第一句话。第二句话。第三句话。', 'total_length': 15,
       'video_titles': ['自动视频'], 'cover_titles': ['封面'], 'cover_subtitles': ['副标题'], 'golden_quotes': ['开场金句。']}
# Stands in for runner.py: makes each step's files the way the engine names them, from the job request.
ENGINE_STAND_IN = r'''
import json, os, sys, time, wave
from pathlib import Path
request = json.loads(sys.argv[-1]); p = Path(request['project']); step, mode = request['step'], request.get('mode')
control = json.loads((p / 'control.json').read_text()) if (p / 'control.json').is_file() else {}
time.sleep(control.get('sleep', {}).get(step, 0))
if step in control.get('fail', []):
    print('STUDIO_RESULT:' + json.dumps({'success': False, 'message': f'Step {step} stand-in failed.'})); sys.exit(1)
def recording(path):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0' * 16000)
script = lambda: json.loads((p / 'text/script.json').read_text())
everything = lambda: [s['index'] for s in script()['segments']]
result = {'success': True}
if step == '2':
    raw = json.loads((p / 'text/raw.json').read_text())
    parts = [x + '。' for x in raw['content'].split('。') if x.strip()]
    keep = {k: raw[k] for k in ('source_name', 'video_titles', 'cover_titles', 'cover_subtitles', 'golden_quotes')}
    (p / 'text/script.json').write_text(json.dumps({**keep, 'segments': [{'index': i, 'content': c, 'length': len(c), 'estimated_duration': 1.0, 'visualizer': 'image'} for i, c in enumerate(parts, 1)]}, ensure_ascii=False))
elif step == '3':
    targets = request['indices'] if request.get('indices') is not None else everything()
    for i in targets:
        if i not in control.get('skip_audio', []): recording(p / f'voice/voice_{i}.wav')
    # Like the engine, a missing opening recording is made whenever the opening is on.
    if request.get('opening') or (request['settings'].get('global', {}).get('opening_quote', True) and not (p / 'voice/opening.wav').exists()):
        recording(p / 'voice/opening.wav')
    result['processed_segments'] = targets
elif step == '4' and mode == 'summary' or request.get('summary'):
    (p / 'text/mini_summary.json').write_text(json.dumps({'summary': 'stand-in summary ' + str(time.time())}))
if step == '4' and mode == 'segments':
    for i in request['indices']: (p / f'images/segment_{i}.png').write_bytes(f'image {i} {time.time()}'.encode())
    if request.get('opening'): (p / 'images/opening_bg.png').write_bytes(f'background {time.time()}'.encode())
    result.update(processed_segments=request['indices'], opening_generated=bool(request.get('opening')))
if step == '5' and mode in (None, 'agent'):
    (p / 'final_video.mp4').write_bytes(f'video {time.time()}'.encode())
    if mode is None and request['settings'].get('three_by_four', {}).get('enabled', True):
        (p / 'final_video_3x4.mp4').write_bytes(b'portrait')
    if mode == 'agent': result['args'] = request['launch']['args']
if step == '5' and mode == 'three_by_four':
    (p / 'final_video_3x4.mp4').write_bytes(f'portrait {time.time()}'.encode())
if step == '6' and mode == 'agent':
    # Like Claude Code: a real WebGL page per cover, which the real renderer turns into PNGs.
    folder = p / request['launch']['cover_design']['folder']
    for entry in request['launch']['cover_design']['entries']: (folder / entry['html']).write_text(COVER_HTML)
if step == '6' and mode is None:
    (p / f'cover_{time.time_ns()}.png').write_bytes(b'cover')
print('STUDIO_RESULT:' + json.dumps(result))
'''


class AutoRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.studio = Studio(ENGINE, root / 'data', root / 'projects')
        self.p = self.studio.workspace / '自动 production'
        for folder in ['text', 'images', 'voice']: (self.p / folder).mkdir(parents=True)
        atomic_json(self.p / 'text/raw.json', dict(RAW))
        self.settings({'global': {'opening_quote': True}, 'step5': {'assembly_method': 'standard', 'bgm_filename': None},
                       'three_by_four': {'enabled': False}, 'step6': {'cover_method': 'api'}})
        self.id = self.studio.project_id(self.p)
        (root / 'engine_stand_in.py').write_text(ENGINE_STAND_IN.replace('COVER_HTML', repr(COVER_HTML)))
        real = subprocess.Popen
        def popen(command, *args, **kwargs):
            # Only the job runner is replaced (FFprobe and other tools still run), and HTML covers are rendered
            # by the real runner, in the app's environment.
            if isinstance(command, list) and len(command) > 2 and str(command[2]).endswith('runner.py'):
                request = json.loads(command[-1])
                with (self.p / 'requests.jsonl').open('a') as log:
                    log.write(json.dumps({k: request.get(k) for k in ('step', 'mode', 'indices', 'opening', 'batch')}) + '\n')
                if request.get('mode') == 'cover_render':
                    kwargs['env'] = {k: v for k, v in kwargs['env'].items() if k != 'PYTHONPATH'}
                else:
                    command = [sys.executable, str(root / 'engine_stand_in.py'), command[-1]]
            return real(command, *args, **kwargs)
        patcher = patch('server.subprocess.Popen', side_effect=popen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for run in list(self.studio.autoruns.values()):
            if run['status'] in autorun.ACTIVE: autorun.stop(self.studio, run['id'])
        self.wait(timeout=20)
        self.tmp.cleanup()

    def settings(self, settings):
        state = self.studio.state(self.p)
        for section, fields in settings.items(): state['settings'].setdefault(section, {}).update(fields)
        self.studio.persist(self.p, state)

    def control(self, **values):
        (self.p / 'control.json').write_text(json.dumps(values))

    def wait(self, timeout=60):
        """Until no automatic run or job of the project is active."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            # Under the service's lock: a queued job is replaced by its running job within one locked step.
            with self.studio.lock:
                if not autorun.active(self.studio, self.id) and not any(j['status'] in {'queued', 'running', 'cancelling'} for j in self.studio.jobs.values()):
                    return next((r for r in self.studio.autoruns.values() if r['project'] == self.id), None)
            time.sleep(0.1)
        self.fail('The automatic run did not end')

    def requests(self):
        path = self.p / 'requests.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []

    def run_to_end(self, **options):
        autorun.start(self.studio, self.id, options)
        return self.wait()

    def produce(self):
        """A finished production: every step's outputs exist and nothing is outdated."""
        run = self.run_to_end()
        self.assertEqual(run['status'], 'succeeded', run)
        (self.p / 'requests.jsonl').unlink()

    def test_after_writing_every_step_runs_in_order(self):
        plan = autorun.plan(self.studio, self.id, {})
        self.assertIsNone(plan['blocked'])
        self.assertEqual([(s['step'], s['run'], s.get('after')) for s in plan['steps']],
                         [('2', True, None), ('3', True, ['2']), ('4', True, ['2']), ('5', True, ['2', '3', '4']), ('6', True, None)])
        run = self.run_to_end()
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual([s['status'] for s in run['steps']], ['done'] * 5)
        self.assertEqual(self.requests(), [
            {'step': '2', 'mode': None, 'indices': None, 'opening': None, 'batch': None},
            {'step': '3', 'mode': None, 'indices': [1, 2, 3], 'opening': True, 'batch': None},
            {'step': '4', 'mode': 'segments', 'indices': [1, 2, 3], 'opening': True, 'batch': 'selected'},
            {'step': '5', 'mode': None, 'indices': None, 'opening': None, 'batch': None},
            {'step': '6', 'mode': None, 'indices': None, 'opening': None, 'batch': None}])
        detail = self.studio.detail(self.id)
        self.assertTrue(all(detail['ready'].values()), detail['ready'])
        self.assertEqual(detail['state']['stale'], {})
        self.assertTrue(detail['summary']['summary'])

    def test_finished_work_is_kept_and_only_what_is_missing_or_outdated_is_made(self):
        self.produce()
        plan = autorun.plan(self.studio, self.id, {})
        self.assertEqual([s['run'] for s in plan['steps']], [False] * 5)
        state = self.studio.state(self.p)
        state['stale']['3'] = {'reason': 'Segment text changed', 'segments': [2]}
        state['stale']['4'] = {'reason': 'Segment text changed', 'segments': [3]}
        self.studio.persist(self.p, state)
        plan = {s['step']: s for s in autorun.plan(self.studio, self.id, {})['steps']}
        self.assertEqual((plan['3']['segments'], plan['3']['opening'], plan['4']['images'], plan['5']['after']), (1, False, 1, ['3', '4']))
        run = self.run_to_end()
        self.assertEqual([s['status'] for s in run['steps']], ['skipped', 'done', 'done', 'done', 'skipped'])
        self.assertEqual([(r['step'], r['indices'], r['opening']) for r in self.requests()],
                         [('3', [2], False), ('4', [3], False), ('5', None, None)])
        self.assertEqual(self.studio.detail(self.id)['state']['stale'], {})

    def test_everything_is_made_again_on_request(self):
        self.produce()
        run = self.run_to_end(scope='all')
        self.assertEqual(run['status'], 'succeeded', run)
        # An existing content summary is written again before the visuals that follow it.
        self.assertEqual([(r['step'], r['mode']) for r in self.requests()],
                         [('2', None), ('3', None), ('4', 'summary'), ('4', 'segments'), ('5', None), ('6', None)])
        self.assertEqual(self.requests()[1]['indices'], [1, 2, 3])
        history = [e['label'] for e in self.studio.state(self.p)['events']]
        self.assertIn('Before generating step 5', history)

    def test_an_outdated_summary_is_rewritten_only_when_asked(self):
        self.produce()
        state = self.studio.state(self.p); state['summary_stale'] = True; self.studio.persist(self.p, state)
        plan = {s['step']: s for s in autorun.plan(self.studio, self.id, {})['steps']}
        self.assertFalse(plan['4']['run'])
        plan = {s['step']: s for s in autorun.plan(self.studio, self.id, {'refresh_summary': True})['steps']}
        self.assertEqual((plan['4']['summary'], plan['4']['images'], plan['4']['opening']), ('rewrite', 3, True))
        run = self.run_to_end(refresh_summary=True)
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual([(r['step'], r['mode'], r['indices']) for r in self.requests()],
                         [('4', 'summary', None), ('4', 'segments', [1, 2, 3]), ('5', None, None)])

    def test_a_failed_job_ends_the_run_before_the_next_step(self):
        self.control(fail=['3'])
        run = self.run_to_end()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual([s['status'] for s in run['steps']], ['done', 'failed', 'pending', 'pending', 'pending'])
        self.assertEqual(run['steps'][1]['message'], 'Step 3 stand-in failed.')
        self.assertEqual([r['step'] for r in self.requests()], ['2', '3'])

    def test_a_step_that_leaves_outputs_missing_fails_the_run(self):
        self.control(skip_audio=[2])
        run = self.run_to_end()
        self.assertEqual((run['status'], run['steps'][1]['message']), ('failed', autorun.INCOMPLETE['3']))
        self.assertNotIn('4', [r['step'] for r in self.requests()])

    def test_stopping_cancels_the_running_job_and_the_rest(self):
        self.control(sleep={'3': 30})
        run = autorun.start(self.studio, self.id, {})
        deadline = time.time() + 20
        while not any(r['step'] == '3' for r in self.requests()) and time.time() < deadline: time.sleep(0.1)
        autorun.stop(self.studio, run['id'])
        run = self.wait()
        self.assertEqual(run['status'], 'stopped')
        self.assertEqual([s['status'] for s in run['steps']], ['done', 'stopped', 'pending', 'pending', 'pending'])

    def test_the_run_holds_the_project_and_queued_images_wait_for_it(self):
        self.produce()
        self.control(sleep={'3': 3})
        state = self.studio.state(self.p); state['stale']['3'] = {'reason': 'Segment text changed', 'segments': [1]}; self.studio.persist(self.p, state)
        autorun.start(self.studio, self.id, {})
        with self.assertRaises(Conflict): autorun.start(self.studio, self.id, {})
        with self.assertRaises(Conflict): self.studio.run_job(self.id, {'step': '6'})
        with self.assertRaises(Conflict): self.studio.select_take(self.id, {'index': 1, 'take': 'x'})
        with self.assertRaises(Conflict): self.studio.set_opening(self.id, {'enabled': False})
        # Between its jobs too: a new image waits in the queue instead of starting.
        self.assertEqual(self.studio.queue_image(self.id, {'index': 2})['status'], 'queued')
        run = self.wait()
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual([(r['step'], r['mode'], r['indices']) for r in self.requests()][-1], ('4', 'static', [2]))

    def test_an_opening_quote_is_needed_when_the_opening_is_on(self):
        raw = dict(RAW, golden_quotes=[]); atomic_json(self.p / 'text/raw.json', raw)
        self.assertEqual(autorun.plan(self.studio, self.id, {})['blocked'], 'Choose an opening quote in Writing, or turn the opening off.')
        with self.assertRaises(ValueError): autorun.start(self.studio, self.id, {})
        self.settings({'global': {'opening_quote': False}})
        run = self.run_to_end(covers=False)
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual([s['step'] for s in run['steps']], ['2', '3', '4', '5'])
        self.assertFalse((self.p / 'voice/opening.wav').exists())

    def test_claude_code_editing_and_html_covers_run_without_a_terminal(self):
        self.produce()
        self.settings({'step5': {'assembly_method': 'agent', 'video_size': '320x180'}, 'three_by_four': {'enabled': True}, 'step6': SMALL_HTML_COVERS})
        run = self.run_to_end(scope='all')
        self.assertEqual(run['status'], 'succeeded', run)
        work = [(r['step'], r['mode']) for r in self.requests()][-4:]
        self.assertEqual(work, [('5', 'agent'), ('5', 'three_by_four'), ('6', 'agent'), ('6', 'cover_render')])
        detail = self.studio.detail(self.id)
        self.assertTrue(detail['assembly_edit']['delivered'])
        self.assertTrue(detail['cover_design']['complete'])
        from PIL import Image
        rendered = [c for c in detail['covers'] if c.startswith('cover_640x360_1_')]
        sizes = []
        for cover in rendered:
            with Image.open(self.p / cover) as image: sizes.append(image.size)
        self.assertEqual(sizes, [(640, 360)])
        self.assertNotIn('5', detail['state']['stale'])
        self.assertNotIn('three_by_four_stale', detail['state'])
        self.assertIsNone(detail['state'].get('agent_active'))

    def test_a_saved_cover_design_is_rendered_not_designed_again(self):
        """A design whose render failed (or was never rendered) still matches the cover copy and setup: the next run renders it."""
        self.produce()
        self.settings({'step6': SMALL_HTML_COVERS})
        job = self.studio.run_job(self.id, {'step': '6', 'mode': 'agent'})
        self.wait()
        self.assertEqual(self.studio.jobs[job['id']]['status'], 'succeeded')
        self.assertEqual(self.studio.state(self.p)['stale']['6']['reason'], RENDER_COVERS)
        # Earlier covers exist and the design waits for its render; then, with no covers and nothing marked outdated.
        for prepare in [lambda: None, lambda: self.clear_covers()]:
            prepare()
            plan = {s['step']: s for s in autorun.plan(self.studio, self.id, {})['steps']}
            self.assertEqual(plan['6']['jobs'], ['covers'])
            (self.p / 'requests.jsonl').unlink(missing_ok=True)
            run = self.run_to_end()
            self.assertEqual(run['status'], 'succeeded', run)
            self.assertEqual([(r['step'], r['mode']) for r in self.requests()], [('6', 'cover_render')])
            self.assertTrue(any(c.startswith('cover_640x360_1_') for c in self.studio.detail(self.id)['covers']))
        # Everything again designs anew.
        plan = {s['step']: s for s in autorun.plan(self.studio, self.id, {'scope': 'all'})['steps']}
        self.assertEqual(plan['6']['jobs'], ['design', 'covers'])

    def clear_covers(self):
        for cover in self.p.glob('cover_*'): cover.unlink()
        state = self.studio.state(self.p); state['stale'].pop('6', None); self.studio.persist(self.p, state)

    def test_a_background_edit_has_no_terminal_kickoff_or_terminal_permissions(self):
        self.produce()
        self.settings({'step5': {'assembly_method': 'agent', 'video_size': '320x180'}})
        job = self.studio.run_job(self.id, {'step': '5', 'mode': 'agent'})
        self.wait()
        args = self.studio.jobs[job['id']]['result']['args']
        import editing
        self.assertNotIn('开始。', args)
        self.assertNotIn('--allowedTools', args)
        self.assertEqual(args[args.index('--add-dir') - 2], '--append-system-prompt-file')
        with self.assertRaisesRegex(ValueError, 'interactive Claude Code terminal'):
            self.studio.run_job(self.id, {'step': '5'})
        self.settings({'step5': {'assembly_method': 'standard'}})
        with self.assertRaisesRegex(ValueError, 'Choose Claude Code editing'):
            self.studio.run_job(self.id, {'step': '5', 'mode': 'agent'})

    def test_a_background_session_runs_claude_code_in_print_mode(self):
        project = Path(self.tmp.name) / 'session'
        (project / '.studio/agent/one').mkdir(parents=True)
        claude = Path(self.tmp.name) / 'claude'
        claude.write_text(f'#!{sys.executable}\nimport json, sys, pathlib\n'
                          f'pathlib.Path({str(project)!r}, "argv.json").write_text(json.dumps(sys.argv[1:]))\n'
                          f'pathlib.Path({str(project)!r}, "final_video.mp4").write_bytes(b"delivered")\n'
                          'print(json.dumps({"is_error": False, "result": "done"}))\n')
        claude.chmod(0o755)
        launch = {'cwd': str(project), 'context': str(project / '.studio/agent/one/context.md'), 'args': ['--session-id', 'one', '--add-dir', str(project)],
                  'assembly_edit': {'folder': '.studio/edits/one', 'delivered_before': None}}
        with patch.dict(os.environ, {'STUDIO_CLAUDE': str(claude)}), patch.object(headless, 'claude_command', return_value=(str(claude), {})):
            result = headless.run_session({'step': '5', 'project': str(project), 'launch': launch})
        self.assertTrue(result['success'])
        argv = json.loads((project / 'argv.json').read_text())
        self.assertEqual(argv[:2], ['-p', headless.first_message()])
        for flag in ['--permission-mode', 'acceptEdits', '--output-format', 'json', '--session-id']: self.assertIn(flag, argv)
        (project / 'final_video.mp4').unlink()
        claude.write_text(f'#!{sys.executable}\nprint("{{\\"is_error\\": false}}")\n')
        with patch.object(headless, 'claude_command', return_value=(str(claude), {})), self.assertRaisesRegex(ValueError, 'without delivering'):
            headless.run_session({'step': '5', 'project': str(project), 'launch': launch})

    def test_runs_are_listed_planned_and_stopped_over_http(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.studio, server.token = self.studio, 'token'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def call(method, path, body=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            connection.request(method, quote(path), json.dumps(body) if body is not None else None, {'Authorization': 'Bearer token'})
            response = connection.getresponse()
            try:
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        status, plan = call('POST', f'/api/projects/{self.id}/autorun-plan', {'scope': 'missing'})
        self.assertEqual((status, plan['steps'][0]['run']), (200, True))
        self.assertEqual(call('POST', f'/api/projects/{self.id}/autorun', {'scope': 'nothing'})[0], 400)
        self.control(sleep={'2': 30})
        status, run = call('POST', f'/api/projects/{self.id}/autorun', {'scope': 'missing'})
        self.assertEqual((status, run['status']), (200, 'running'))
        self.assertEqual([r['id'] for r in call('GET', '/api/autoruns')[1]], [run['id']])
        self.assertEqual(call('POST', f'/api/autoruns/{run["id"]}/stop', {})[1]['status'], 'stopping')
        self.assertEqual(self.wait()['status'], 'stopped')


if __name__ == '__main__': unittest.main()

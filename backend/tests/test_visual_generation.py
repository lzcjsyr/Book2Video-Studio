import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_generation as visuals


class VisualGenerationTests(unittest.TestCase):
    def test_subscription_does_not_inherit_api_routing(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY":"private", "ANTHROPIC_BASE_URL":"wrong-endpoint", "CLAUDECODE":"nested"}):
            program, env = visuals.claude_command({"mode":"subscription", "path":sys.executable})
        self.assertEqual(program, sys.executable)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertNotIn("CLAUDECODE", env)

    def test_batch_dispatches_types_and_bounds_motion_concurrency(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / 'text').mkdir()
            segments = [{'index':i, 'content':f'Segment {i}', 'visualizer':'image' if i == 1 else 'hf'} for i in range(1, 7)]
            (project / 'text/script.json').write_text(json.dumps({'segments':segments}))
            request = {'project':temporary, 'engine':temporary, 'studio_data':temporary, 'workspace':temporary,
                       'indices':list(range(1,7)), 'opening':False, 'motion_folders':{str(i):str(project/f'motion_{i}') for i in range(2,7)}}
            active = peak = 0
            lock = threading.Lock()
            def motion(launch, *_):
                nonlocal active, peak
                with lock:
                    active += 1; peak = max(peak, active)
                time.sleep(.03)
                with lock: active -= 1
                if launch['index'] == 4: raise ValueError('Motion 4 failed')
                return launch['index']
            def prepare(studio, project, body): return {'index':body['indices'][0]}
            params = {'image_server':'test', 'image_model':'test', 'llm_server_step4':'test', 'llm_model_step4':'test', 'llm_base_url_step4':'test'}
            calls = []
            def call(fn, values):
                calls.append(values)
                return {'success':True, 'processed_segments':values['target_segments']}
            with patch.object(visuals, 'claude_command', return_value=('claude', {})), patch.object(visuals, 'generate_motion', side_effect=motion), \
                 patch('server.Studio'), patch('context.prepare', side_effect=prepare), patch('runner.check_image_model'):
                result = visuals.run(request, params, type('Steps', (), {'run_step_4':None}), call)
            self.assertEqual(peak, 3)
            self.assertEqual(calls[0]['target_segments'], [1])
            self.assertFalse(calls[0]['opening_quote'])
            self.assertEqual(result['processed_segments'], [1,2,3,5,6])
            self.assertEqual(result['failed_segments'], [4])
            self.assertFalse(result['success'])

    def test_motion_only_batch_never_calls_image_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); (project/'text').mkdir()
            (project/'text/script.json').write_text(json.dumps({'segments':[{'index':1,'visualizer':'hf'}]}))
            request = {'project':temporary,'engine':temporary,'studio_data':temporary,'workspace':temporary,
                       'indices':[1], 'motion_folders':{'1':temporary}}
            with patch.object(visuals, 'claude_command', return_value=('claude', {})), patch.object(visuals, 'generate_motion', return_value=1), \
                 patch('server.Studio'), patch('context.prepare', return_value={}), patch('runner.check_image_model') as image_check:
                def unexpected(*_): self.fail('Motion-only scope called the image provider')
                result = visuals.run(request, {}, object(), unexpected)
            self.assertTrue(result['success'])
            image_check.assert_not_called()


if __name__ == '__main__': unittest.main()

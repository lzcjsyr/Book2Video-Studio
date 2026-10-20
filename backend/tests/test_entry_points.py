"""Every Python script the app or a Claude Code session starts finds the engine without PYTHONPATH, as in the app."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'
BACKEND = Path(__file__).resolve().parents[1]
PACKAGED = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}


class EntryPointTests(unittest.TestCase):
    def test_the_motion_clip_render_script_runs_from_its_folder(self):
        sys.path.insert(0, str(ENGINE))
        from core.infra.motion.runtime import prepare
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / 'motion-1'
            prepare(work, {'segmentIndex': 1, 'index': 1, 'content': '一段话。', 'durationSeconds': 2, 'width': 320, 'height': 180, 'fps': 30})
            result = subprocess.run([sys.executable, 'render.py', 'capabilities'], cwd=work, env=PACKAGED, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout[-1500:] + result.stderr[-1500:])

    def test_the_job_runner_imports_the_engine_before_any_job(self):
        # A render request the renderer turns down only after importing the engine (an unsaved design).
        with tempfile.TemporaryDirectory() as project:
            request = {'engine': str(ENGINE), 'project': project, 'step': '6', 'mode': 'cover_render',
                       'cover_design': {'folder': '.studio/cover-designs/unsaved', 'entries': [{'html': 'cover.html', 'width': 64, 'height': 64}]}}
            result = subprocess.run([sys.executable, '-u', str(BACKEND / 'runner.py'), json.dumps(request)], env=PACKAGED, capture_output=True, text=True, timeout=60)
        outcome = json.loads(next(line for line in result.stdout.splitlines() if line.startswith('STUDIO_RESULT:'))[len('STUDIO_RESULT:'):])
        self.assertEqual(outcome['message'], 'Save every cover HTML design with Claude Code before rendering.')


if __name__ == '__main__': unittest.main()

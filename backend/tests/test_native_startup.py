"""Run the service with the same isolated Python environment as the native host."""
import http.client
import json
import os
from pathlib import Path
import queue
import threading
import subprocess
import sys
import tempfile
import unittest

DESKTOP = Path(__file__).resolve().parents[2]


class NativeStartupTests(unittest.TestCase):
    def test_source_service_loads_saved_edit_without_pythonpath(self):
        self.check_service(DESKTOP/'backend/server.py')

    def test_packaged_service_loads_saved_edit_without_pythonpath(self):
        self.check_service(DESKTOP/'build/backend/server.py')

    def check_service(self, script):
        with tempfile.TemporaryDirectory(prefix='studio native ') as temporary:
            root = Path(temporary)
            project = root/'projects/story'
            work = project/'.studio/edits/test'
            work.mkdir(parents=True)
            (project/'text').mkdir()
            (project/'text/raw.json').write_text(json.dumps({'content': 'Hello.', 'video_titles': ['Test']}))
            (project/'.studio/state.json').write_text(json.dumps({
                'layout': 2, 'stale': {}, 'reviewed': {}, 'events': [],
                'settings': {'global': {'opening_quote': False}},
                'assembly_edit': {'folder': '.studio/edits/test', 'skill': 'editorial-explainer', 'source': ''},
            }))
            environment = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
            environment.update(STUDIO_TOKEN='native-startup-test', STUDIO_NATIVE_HOST='1', PYTHONNOUSERSITE='1')
            process = subprocess.Popen([
                sys.executable, '-u', str(script),
                '--engine', str(DESKTOP/'build/engine'), '--data', str(root/'data'),
                '--workspace', str(root/'projects'),
            ], cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                lines = queue.Queue()
                threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
                handshake = lines.get(timeout=20)
                self.assertTrue(handshake, 'Service exited before its startup handshake')
                port = json.loads(handshake)['port']
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
                try:
                    for path in ['/api/bootstrap', '/api/projects', '/api/projects/story']:
                        connection.request('GET', path, headers={'Authorization': 'Bearer native-startup-test'})
                        response = connection.getresponse()
                        content = response.read().decode()
                        self.assertEqual(response.status, 200, content)
                        if path.endswith('/story'):
                            edit = json.loads(content)['assembly_edit']
                            self.assertEqual((edit['current'], edit['delivered']), (False, False))
                finally:
                    connection.close()
            finally:
                try:
                    _, errors = process.communicate('shutdown\n', timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill(); _, errors = process.communicate()
                self.assertEqual(process.returncode, 0, errors)

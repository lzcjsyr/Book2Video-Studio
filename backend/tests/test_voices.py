import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Studio, Handler, VOICES_FILE

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'


class VoiceLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.studio = Studio(ENGINE, self.root/'data', self.root/'projects')

    def tearDown(self):
        self.temp.cleanup()

    def test_named_voice_survives_restart_and_is_shared_between_projects(self):
        first = self.studio.create({'name': 'First'})
        second = self.studio.create({'name': 'Second'})
        before = {item['id']: self.studio.state(self.studio.project(item['id'])) for item in [first, second]}
        self.studio.save_voice({'id': 'S_test123', 'name': '张老师'})
        reopened = Studio(ENGINE, self.root/'data', self.root/'projects')
        self.assertIn({'id': 'S_test123', 'name': '张老师'}, reopened.bootstrap()['voices'])
        # Saving an alias neither selects a project voice nor outdates recordings.
        for project, state in before.items():
            self.assertEqual(reopened.state(reopened.project(project)), state)

    def test_rename_updates_one_voice_and_rejects_invalid_input_without_writing(self):
        self.studio.save_voice({'id': 'S_test123', 'name': '旧名称'})
        voices = self.studio.save_voice({'id': ' S_test123 ', 'name': ' 新名称 '})['voices']
        self.assertEqual([voice for voice in voices if voice['id'] == 'S_test123'], [{'id': 'S_test123', 'name': '新名称'}])
        file = self.studio.data/VOICES_FILE
        before = file.read_bytes()
        for body in [[], {'id': '', 'name': '声音'}, {'id': '../escape', 'name': '声音'},
                     {'id': 'S_test', 'name': ''}, {'id': 'S_test', 'name': 'a'*81}]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.studio.save_voice(body)
        self.assertEqual(file.read_bytes(), before)

    def test_voice_http_endpoint_requires_auth_and_returns_the_saved_name(self):
        service = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        service.studio, service.token = self.studio, 'test-token'
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', service.server_port)
        try:
            connection.request('POST', '/api/voices', json.dumps({'id':'S_test123','name':'老师'}))
            response = connection.getresponse(); self.assertEqual(response.status, 401); response.read()
            headers = {'Authorization': 'Bearer test-token', 'Content-Type': 'application/json'}
            connection.request('POST', '/api/voices', json.dumps({'id':'S_test123','name':'老师'}), headers)
            response = connection.getresponse(); self.assertEqual(response.status, 200)
            self.assertIn({'id':'S_test123','name':'老师'}, json.loads(response.read())['voices'])
            connection.request('GET', '/api/voices', headers=headers)
            response = connection.getresponse(); self.assertEqual(response.status, 200)
            self.assertIn({'id':'S_test123','name':'老师'}, json.loads(response.read())['voices'])
        finally:
            connection.close(); service.shutdown(); service.server_close(); thread.join()

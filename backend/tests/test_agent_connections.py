"""Exercise the shipped Claude executable against a local provider, without real credentials."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DESKTOP = Path(__file__).resolve().parents[2]


class AgentConnectionTests(unittest.TestCase):
    def test_bundled_claude_uses_api_endpoint_token_and_model_despite_user_settings(self):
        runtime = DESKTOP / 'build/runtime'
        manifest = json.loads((runtime / 'manifest.json').read_text())
        received = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_): pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((request.get('model'), self.headers.get('authorization')))
                message = dict(id='msg_fixture', type='message', role='assistant', model=request.get('model'),
                    content=[{'type':'text', 'text':'READY'}], stop_reason='end_turn', stop_sequence=None,
                    usage={'input_tokens':10, 'output_tokens':1})
                if request.get('stream'):
                    events = [
                        ('message_start', {'type':'message_start', 'message':{**message, 'content':[], 'stop_reason':None}}),
                        ('content_block_start', {'type':'content_block_start', 'index':0, 'content_block':{'type':'text', 'text':''}}),
                        ('content_block_delta', {'type':'content_block_delta', 'index':0, 'delta':{'type':'text_delta', 'text':'READY'}}),
                        ('content_block_stop', {'type':'content_block_stop', 'index':0}),
                        ('message_delta', {'type':'message_delta', 'delta':{'stop_reason':'end_turn', 'stop_sequence':None}, 'usage':{'output_tokens':1}}),
                        ('message_stop', {'type':'message_stop'}),
                    ]
                    data = ''.join(f'event: {event}\ndata: {json.dumps(value)}\n\n' for event, value in events).encode()
                    content_type = 'text/event-stream'
                else:
                    data, content_type = json.dumps(message).encode(), 'application/json'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                config = Path(temporary) / 'config'; config.mkdir()
                (config / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding':True}))
                (config / 'settings.json').write_text(json.dumps({'model':'wrong-user-model', 'env':{
                    'ANTHROPIC_BASE_URL':'http://127.0.0.1:1', 'ANTHROPIC_AUTH_TOKEN':'wrong-user-token'}}))
                env = {key:value for key, value in os.environ.items()
                       if not key.startswith(('ANTHROPIC_', 'CLAUDE_CODE_', 'CLAUDECODE'))}
                env.update(CLAUDE_CONFIG_DIR=str(config), ANTHROPIC_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                    ANTHROPIC_AUTH_TOKEN='fixture-api-key', ANTHROPIC_API_KEY='', ANTHROPIC_MODEL='fixture-model',
                    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', NO_PROXY='127.0.0.1,localhost')
                result = subprocess.run([str(runtime / manifest['claude']), '-p', 'Reply READY',
                    '--setting-sources', 'project,local', '--model', env['ANTHROPIC_MODEL'],
                    '--tools', '', '--max-turns', '1', '--output-format', 'json', '--no-session-persistence'],
                    cwd=temporary, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['result'], 'READY')
                self.assertTrue(received)
                self.assertTrue(all(model == 'fixture-model' and token == 'Bearer fixture-api-key'
                                    for model, token in received))
        finally:
            server.shutdown(); server.server_close()

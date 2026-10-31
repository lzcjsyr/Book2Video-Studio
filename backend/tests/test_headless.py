"""Background sessions keep renders bounded, cancellable, and isolated from each other."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import headless


class HeadlessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cli = self.root / 'claude'
        self.launch = {'cwd': str(self.root), 'args': []}

    def script(self, code):
        self.cli.write_text(f'#!{sys.executable}\n' + code)
        self.cli.chmod(0o755)

    def run_session(self, prompt='test', timeout=5, logs=None):
        return headless.run_claude(str(self.cli), os.environ.copy(), self.launch, prompt, 10, timeout, logs or self.root)

    def test_stdin_is_closed_so_unattended_sessions_do_not_wait_for_input(self):
        self.script('import json,sys\nassert sys.stdin.read()==""\nprint(json.dumps({"is_error":False,"result":"done"}))\n')
        self.assertEqual(self.run_session()['result'], 'done')

    @unittest.skipIf(os.name == 'nt', 'POSIX process group supervision')
    def test_timeout_stops_background_render_even_when_it_ignores_term(self):
        child = ('import os,signal,time\nfrom pathlib import Path\n'
                 'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
                 f'Path({str(self.root / "render.pid")!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
        self.script(f'import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",{child!r}])\ntime.sleep(60)\n')
        with self.assertRaisesRegex(ValueError, 'Completed files were kept'):
            self.run_session(timeout=1)
        self.assert_stopped(int((self.root / 'render.pid').read_text()))
        self.assertFalse(headless._running)

    @unittest.skipIf(os.name == 'nt', 'POSIX process group supervision')
    def test_cancelling_runner_forwards_to_cli_and_render(self):
        child = (f'import os,time\nfrom pathlib import Path\nPath({str(self.root / "render.pid")!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
        self.script(f'import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",{child!r}])\ntime.sleep(60)\n')
        code = (f'import sys\nsys.path.insert(0,{str(Path(headless.__file__).parent)!r})\n'
                'import headless,os\nheadless.install_signal_handlers()\n'
                f'headless.run_claude({str(self.cli)!r},os.environ.copy(),{self.launch!r},"test",10,60,__import__("pathlib").Path({str(self.root)!r}))\n')
        runner = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / 'render.pid').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.root / 'render.pid').exists())
            runner.terminate()
            runner.communicate(timeout=10)
            self.assertEqual(runner.returncode, 128 + signal.SIGTERM)
            self.assert_stopped(int((self.root / 'render.pid').read_text()))
        finally:
            if runner.poll() is None:
                runner.terminate()
                runner.communicate(timeout=10)

    @unittest.skipIf(os.name == 'nt', 'POSIX signals')
    def test_cancelling_runner_neither_waits_for_busy_threads_nor_starts_queued_work(self):
        """Regression: a stopped image job ran on until its provider retries ended, then started the next image."""
        started = self.root / 'started'
        code = (f'import sys,time\nsys.path.insert(0,{str(Path(headless.__file__).parent)!r})\n'
                'import headless\nfrom concurrent.futures import ThreadPoolExecutor\nheadless.install_signal_handlers()\n'
                'with ThreadPoolExecutor(max_workers=1) as pool:\n'
                '    pool.submit(time.sleep, 60)\n'
                f'    pool.submit(open, {str(started)!r}, "w")\n'
                '    print("ready", flush=True)\n'
                '    time.sleep(60)\n')
        runner = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(runner.stdout.readline().strip(), 'ready')
            stopped = time.monotonic()
            runner.terminate()
            runner.wait(timeout=10)
            self.assertLess(time.monotonic() - stopped, 5)
            self.assertEqual(runner.returncode, 128 + signal.SIGTERM)
            self.assertFalse(started.exists())
        finally:
            if runner.poll() is None:
                runner.kill()
            runner.wait()
            runner.stdout.close()

    def test_no_session_starts_once_the_job_is_stopped(self):
        self.script('from pathlib import Path\nPath(__file__).with_name("ran").write_text("")\n')
        headless._cancelled = True
        try:
            with self.assertRaisesRegex(ValueError, 'stopped'):
                self.run_session()
        finally:
            headless._cancelled = False
        self.assertFalse((self.root / 'ran').exists())
        self.assertFalse(headless._running)

    def test_one_timed_out_session_does_not_cancel_a_parallel_session(self):
        self.script('import json,sys,time\ntime.sleep(60 if sys.argv[2]=="slow" else .5)\nprint(json.dumps({"is_error":False,"result":"done"}))\n')
        slow, fast = self.root / 'slow', self.root / 'fast'
        slow.mkdir(); fast.mkdir()
        with ThreadPoolExecutor(max_workers=2) as pool:
            stopped = pool.submit(self.run_session, 'slow', .2, slow)
            completed = pool.submit(self.run_session, 'fast', 5, fast)
            with self.assertRaisesRegex(ValueError, 'timed out'):
                stopped.result()
            self.assertEqual(completed.result()['result'], 'done')

    def test_long_assembly_budget_uses_recording_length_and_speed(self):
        folder = self.root / '.studio/edits/one'
        folder.mkdir(parents=True)
        materials = {'segments': [{'audio_seconds': 1200}], 'opening': {'audio_seconds': 60}, 'narration_speed': 2}
        (folder / 'materials.json').write_text(json.dumps(materials))
        request = {'step': '5', 'project': str(self.root), 'launch': {'assembly_edit': {'folder': '.studio/edits/one'}}}
        self.assertEqual(headless.session_limits(request), (200, 18900))
        materials['segments'][0]['audio_seconds'] = 30
        (folder / 'materials.json').write_text(json.dumps(materials))
        self.assertEqual(headless.session_limits(request), (200, 10800))
        self.assertEqual(headless.session_limits({'step': '6'}), (100, 3600))

    def assert_stopped(self, pid):
        status = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not status or status.startswith('Z'), f'Render {pid} still running: {status}')


if __name__ == '__main__':
    unittest.main()

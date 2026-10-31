"""Verify long encoded clips without decoding every frame just to count them."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'build/engine'))
from core.infra.motion import renderer


class MotionValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.video = Path(cls.temp.name) / 'three-minutes.mp4'
        subprocess.run([renderer._tool('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=s=160x90:r=30', '-t', '180', '-c:v', 'libx264', '-preset', 'ultrafast',
                        '-pix_fmt', 'yuv420p', '-an', str(cls.video)], check=True, timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_three_minute_video_counts_all_5400_packets_without_decoding(self):
        with patch.object(renderer.subprocess, 'run', wraps=subprocess.run) as run:
            metadata = renderer.verify_export(self.video, 160, 90, 30, 5400)
        self.assertEqual(int(metadata['streams'][0]['nb_read_packets']), 5400)
        self.assertIn('-count_packets', run.call_args.args[0])
        self.assertNotIn('-count_frames', run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['timeout'], 180)

    def test_missing_frame_wrong_dimensions_or_wrong_rate_still_fail(self):
        for width, fps, frames in [(160, 30, 5401), (320, 30, 5400), (160, 25, 5400)]:
            with self.subTest(width=width, fps=fps, frames=frames), self.assertRaisesRegex(RuntimeError, 'validation'):
                renderer.verify_export(self.video, width, 90, fps, frames)

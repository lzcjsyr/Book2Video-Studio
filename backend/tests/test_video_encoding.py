"""Check real backend selection and failure handling without requiring a GPU."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "build/engine"))
from core.infra.motion import encoding


class VideoEncodingTests(unittest.TestCase):
    def setUp(self):
        encoding.select_encoder.cache_clear()

    def test_all_apple_silicon_models_share_the_native_policy(self):
        # Selection uses the OS, never a list of particular M chip names.
        with patch.object(encoding.sys, 'platform', 'darwin'), patch.object(encoding.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, '', '')
            for codec in ['h264', 'hevc']:
                self.assertEqual(encoding.select_encoder('ffmpeg', codec, 1920, 1080, 'darwin'), f'{codec}_videotoolbox')
                command = run.call_args.args[0]
                self.assertIn('color=s=1920x1080:r=30', command)
                self.assertIn('-allow_sw', command)
                self.assertNotIn('-crf', command)

    def test_windows_tries_other_hardware_before_software(self):
        with patch.object(encoding.subprocess, 'run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 1, '', 'No NVIDIA device'),
                               subprocess.CompletedProcess([], 0, '', '')]
            self.assertEqual(encoding.select_encoder('ffmpeg', 'h264', 1280, 720, 'win32'), 'h264_qsv')
            self.assertEqual(run.call_count, 2)

    def test_amd_can_succeed_after_nvidia_and_intel_fail(self):
        with patch.object(encoding.subprocess, 'run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 1, '', 'Device unavailable')]*2 + [subprocess.CompletedProcess([], 0, '', '')]
            self.assertEqual(encoding.select_encoder('ffmpeg', 'hevc', 1280, 720, 'win32'), 'hevc_amf')

    def test_failed_and_timed_out_probes_report_software_fallback(self):
        with patch.object(encoding.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffmpeg', 15)), patch('builtins.print') as log:
            self.assertEqual(encoding.select_encoder('ffmpeg', 'hevc', 1920, 1080, 'win32'), 'libx265')
            message = log.call_args.args[0]
            for backend in ['hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'libx265']:
                self.assertIn(backend, message)

    def test_actual_hardware_failure_retries_with_software(self):
        run = Mock(side_effect=[subprocess.CalledProcessError(1, 'ffmpeg'), None])
        with patch.object(encoding, 'select_encoder', return_value='h264_videotoolbox'):
            encoding.encode(run, '-i', 'source.mp4', output='out.mp4', width=1920, height=1080)
        self.assertEqual(run.call_count, 2)
        hardware, software = [call.args for call in run.call_args_list]
        self.assertIn('h264_videotoolbox', hardware)
        self.assertNotIn('-crf', hardware)
        self.assertIn('libx264', software)
        self.assertEqual(hardware[-1], software[-1])

    def test_software_failure_is_not_swallowed(self):
        run = Mock(side_effect=subprocess.CalledProcessError(1, 'ffmpeg'))
        with patch.object(encoding, 'select_encoder', return_value='libx264'):
            with self.assertRaises(subprocess.CalledProcessError):
                encoding.encode(run, '-i', 'source.mp4', output='out.mp4', width=1920, height=1080)
        self.assertEqual(run.call_count, 1)

"""Each recording gets a timing file beside it that matches the recording as saved, pauses trimmed or not."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(ENGINE)]
RATE = 48000


def speech(*parts):
    """PCM from (seconds, loud) parts: a 220 Hz tone for speech, zeros for silence."""
    audio = [np.sin(2 * np.pi * 220 * np.arange(int(s * RATE)) / RATE) * 8000 if loud else np.zeros(int(s * RATE)) for s, loud in parts]
    return np.concatenate(audio).astype(np.int16).tobytes()


def word(text, start, end):
    return {'word': text, 'startTime': start, 'endTime': end, 'confidence': 0.9}


# 0.4 s silence, sentence one 0.4–1.3, 0.5 s pause, sentence two 1.8–2.8: its number is marked late and its end early.
PCM = speech((0.4, False), (0.9, True), (0.5, False), (1.0, True), (0.2, False))
SENTENCES = [{'text': '一二。', 'words': [word('一', 0.4, 0.8), word('二。', 0.8, 1.3)]},
             {'text': '2026年！', 'words': [word('2026', 2.0, 2.1), word('年！', 2.2, 2.4)]}]


class VoiceTimingTests(unittest.TestCase):
    def synthesize(self, threshold):
        from core.infra.ai import tts_client
        folder = tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        wav = Path(folder.name) / 'voice_1.wav'
        with mock.patch.object(tts_client.config, 'BYTEDANCE_TTS_API_KEY', 'key'), \
             mock.patch.object(tts_client, '_request_bytedance_tts_pcm', return_value=(PCM, SENTENCES)) as request:
            tts_client.text_to_audio_bytedance('一二。2026年！', str(wav), voice='S_test', mute_cut_threshold=threshold,
                                               mute_cut_min_silence_ms=300, mute_cut_remain_ms=100)
        self.assertTrue(request.call_args.kwargs['subtitles'])
        return wav, json.loads(wav.with_suffix('.json').read_text())

    def test_untrimmed_timing_follows_the_service(self):
        wav, timing = self.synthesize(0)
        self.assertFalse(timing['trimmed_pauses']);self.assertAlmostEqual(timing['seconds'], 3.0, delta=.001)
        one, two = timing['sentences']
        self.assertEqual((one['text'], one['start'], two['text']), ('一二。', 0.4, '2026年！'))
        # A word marked late starts where the one before it ended; the sentence ends where the voice does.
        self.assertEqual(two['words'][0]['start'], 2.0);self.assertEqual(two['words'][1]['start'], 2.1)
        self.assertAlmostEqual(two['end'], 2.8, delta=.02)

    def test_trimmed_timing_moves_with_the_recording(self):
        wav, timing = self.synthesize(400)
        self.assertTrue(timing['trimmed_pauses'])
        # Each long pause keeps 0.1 s on both sides: 0.2 s of the leading 0.4 s and 0.3 s of the 0.5 s pause go.
        self.assertAlmostEqual(timing['seconds'], 3.0 - 0.5, delta=.02)
        one, two = timing['sentences']
        self.assertAlmostEqual(one['start'], 0.4 - 0.2, delta=.02);self.assertAlmostEqual(two['words'][0]['start'], 2.0 - 0.5, delta=.02)
        # The voice ends where the trimmed recording's last 0.2 s of silence (too short to trim) begins.
        self.assertAlmostEqual(two['end'], timing['seconds'] - 0.2, delta=.015)
        self.assertAlmostEqual(timing['seconds'], (wav.stat().st_size - 44) / 2 / RATE, delta=.001)

    def test_a_recording_without_timestamps_drops_the_old_timing(self):
        from core.infra.ai import tts_client
        wav, _ = self.synthesize(0)
        with mock.patch.object(tts_client.config, 'BYTEDANCE_TTS_API_KEY', 'key'), \
             mock.patch.object(tts_client, '_request_bytedance_tts_pcm', return_value=(PCM, [])):
            tts_client.text_to_audio_bytedance('一二。', str(wav), voice='zh_male_test_moon_bigtts', mute_cut_threshold=0)
        self.assertFalse(wav.with_suffix('.json').exists())

    def test_short_pauses_never_repeat_audio(self):
        from core.infra.ai.tts_client import silence_keep_ranges
        # A 0.35 s pause is longer than the 0.3 s minimum but shorter than the two 0.2 s margins.
        ranges = silence_keep_ranges(speech((0.5, True), (0.35, False), (0.5, True)), RATE, 400, 300, 200)
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), ranges)


if __name__ == '__main__': unittest.main()

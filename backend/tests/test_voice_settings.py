"""Voice delivery settings reach the engine's voice and assembly stages under the names those stages take."""
import inspect
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(ENGINE)]


class VoiceSettingsTests(unittest.TestCase):
    def test_speaking_rate_loudness_and_emotion_reach_the_stages(self):
        from core.config import get_generation_params
        from core.pipeline import steps
        chosen = {'speech_rate': 37, 'loudness_rate': 11, 'emotion': 'happy', 'emotion_scale': 2}
        with tempfile.TemporaryDirectory() as folder:
            merged = yaml.safe_load((ENGINE / 'config.example.yaml').read_text())
            merged['step3'].update(chosen)
            path = Path(folder) / 'job-config.yaml'
            path.write_text(yaml.safe_dump(merged, allow_unicode=True))
            params = get_generation_params(path)
        # The runner passes a stage the parameters whose names it takes (runner.call).
        for stage in (steps.run_step_3, steps.run_step_5):
            passed = {k: params[k] for k in inspect.signature(stage).parameters if k in params}
            self.assertEqual({k: passed.get(k) for k in chosen}, chosen, stage.__name__)


if __name__ == '__main__': unittest.main()

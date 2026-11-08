import hashlib
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_voice
from server import Studio, atomic_json

ENGINE = Path(__file__).resolve().parents[2] / "build/engine"


def recording():
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", ""))
        wav.writeframes(b"\x00\x10\x00\xf0" * 8000)
    return data.getvalue()


class LocalVoiceTests(unittest.TestCase):
    def test_download_resumes_and_checks_the_complete_file(self):
        data = b"pinned weights"
        item = {"path": "weights", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            partial = folder / "weights.modelscope.part"
            partial.write_bytes(data[:4])
            response = Mock(status_code=206, headers={"Content-Range": f"bytes 4-{len(data)-1}/{len(data)}",
                                                      "Content-Length": str(len(data)-4)})
            response.iter_content.return_value = iter([data[4:]])
            session = MagicMock()
            session.get.return_value.__enter__.return_value = response
            local_voice.fetch_model_file(session, folder, item, lambda _offset: None)
            self.assertEqual(session.get.call_args.kwargs["headers"]["Range"], "bytes=4-")
            self.assertEqual((folder / "weights").read_bytes(), data)
            self.assertFalse(partial.exists())

    def test_reference_replacement_keeps_the_old_file_if_import_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            studio = Studio(ENGINE, root / "data", root / "projects")
            created = studio.create({"name": "Local narration", "settings": {"step3": {"tts_provider": "voxcpm2"}}})
            project = Path(created["path"])
            data = recording()
            detail = studio.import_voice_reference(created["id"], "voice.wav", io.BytesIO(data), len(data))
            source = local_voice.reference(project, detail["state"])
            self.assertTrue(source.is_relative_to(project))
            self.assertEqual(detail["voice_reference"], "voice.wav")
            self.assertEqual(detail["state"]["stale"]["3"]["reason"], "Reference recording changed")
            with self.assertRaisesRegex(ValueError, "playable"):
                studio.import_voice_reference(created["id"], "bad.wav", io.BytesIO(b"bad"), 3)
            self.assertEqual(local_voice.reference(project, studio.state(project)), source)
            self.assertTrue(source.is_file())

    def test_local_backend_generates_segments_and_opening_without_calling_cloud_tts(self):
        sys.path.insert(0, str(ENGINE))
        from core.pipeline import steps
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            atomic_json(project / "text/script.json", {"golden_quotes": ["Opening"], "segments": [
                {"index": 1, "content": "First segment."}, {"index": 2, "content": "Second segment."}
            ]})
            written = []

            def synthesize(text, output_filename, **_):
                written.append(text)
                Path(output_filename).write_bytes(recording())
                return True

            with patch.object(steps, "text_to_audio_bytedance") as cloud:
                result = steps.run_step_3("voxcpm2", "", "", str(project), target_segments=[2], synthesize=synthesize)
            cloud.assert_not_called()
            self.assertTrue(result["success"])
            self.assertEqual(written, ["Second segment.", "Opening"])
            self.assertTrue((project / "voice/voice_2.wav").is_file())
            self.assertTrue((project / "voice/opening.wav").is_file())
            self.assertFalse((project / "voice/voice_1.wav").exists())

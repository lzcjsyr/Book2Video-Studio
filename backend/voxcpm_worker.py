"""Isolated MLX worker. Only this process imports the downloaded runtime."""
import fcntl
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path


def main(root, runtime, weights, check=False):
    sys.path.insert(0, runtime)
    # Keep the protocol separate from logs emitted by MLX and the tokenizer.
    protocol = sys.stdout

    def respond(result):
        print(json.dumps(result, ensure_ascii=False), file=protocol, flush=True)

    with (root / "inference.lock").open("a") as lock, redirect_stdout(sys.stderr):
        fcntl.flock(lock, fcntl.LOCK_EX)
        import mlx.core as mx
        import numpy as np
        import soundfile as sf
        from mlx_audio.tts.utils import load_model

        mx.set_default_device(mx.gpu)
        mx.set_cache_limit(512 * 1024 * 1024)
        model = load_model(weights)
        if check:
            mx.eval(mx.ones((8,)) + 1)
            return
        respond({"ready": True})
        max_patches = 768
        samples_per_patch = model.patch_size * model.audio_vae.decode_chunk_size
        for line in sys.stdin:
            try:
                request = json.loads(line)
                mx.random.seed(42)
                result = next(model.generate(text=request["text"], ref_audio=request["reference"],
                                             max_tokens=max_patches))
                audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                if not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 1e-5:
                    raise ValueError("VoxCPM2 did not generate playable speech. Try a different reference recording.")
                if audio.size >= max_patches * samples_per_patch:
                    raise ValueError("This recording reached the VoxCPM2 length limit. Split the text into shorter segments.")
                sf.write(request["output"], audio, result.sample_rate, format="WAV", subtype="PCM_16")
                respond({"success": True})
            except (ValueError, OSError, RuntimeError, StopIteration) as exc:
                print(f"VoxCPM2: {exc}", file=sys.stderr)
                respond({"error": str(exc) or "VoxCPM2 could not generate the recording."})
            finally:
                mx.clear_cache()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), "--check" in sys.argv[4:])

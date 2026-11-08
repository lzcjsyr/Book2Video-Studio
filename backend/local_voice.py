"""Install the optional VoxCPM2 runtime and bridge one production job to it."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid
import wave
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import quote

PROVIDER = "voxcpm2"
REFERENCE_LIMIT = 64 * 1024 * 1024
REFERENCE_FORMATS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg", ".opus", ".webm"}
ASSETS = Path(__file__).with_name(PROVIDER)
WORKER = Path(__file__).with_name("voxcpm_worker.py")
LOCK = ASSETS / "requirements.lock"
MANIFEST = json.loads((ASSETS / "model.json").read_text(encoding="utf-8"))
MODEL_BYTES = sum(entry["size"] for entry in MANIFEST["files"])
RUNTIME_VERSION = hashlib.sha256(LOCK.read_bytes()).hexdigest()[:16]


def support_problem():
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return "VoxCPM2 is available on Apple silicon Macs."
    if int(platform.mac_ver()[0].split(".")[0] or 0) < 14:
        return "VoxCPM2 requires macOS 14 or later."
    if sys.version_info[:2] != (3, 13):
        return "VoxCPM2 needs Grist’s bundled runtime. Reinstall Grist."
    return None


def model_folder(root):
    return root / "models" / MANIFEST["revision"]


def runtime_folder(root):
    return root / f"python-{RUNTIME_VERSION}"


def file_size(path):
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def ready(root):
    return (
        (runtime_folder(root) / ".ready").is_file()
        and (runtime_folder(root) / "mlx").is_dir()
        and all(file_size(model_folder(root) / f["path"]) == f["size"] for f in MANIFEST["files"])
    )


def reference(project, state):
    selected = state.get("voice_reference") or {}
    ident = selected.get("id", "")
    if isinstance(ident, str) and re.fullmatch(r"[a-f0-9]{32}", ident):
        path = project / ".studio" / "voice-references" / f"{ident}.wav"
        if path.is_file():
            return path
    return None


def prepare_reference(source, target):
    ffmpeg = os.environ.get("AIGC_FFMPEG_PATH") or shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("FFmpeg is unavailable. Reinstall Grist.")
    try:
        subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file",
                        "-i", str(source), "-map", "0:a:0", "-t", "61", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(target)], check=True, capture_output=True, timeout=60)
        with wave.open(str(target), "rb") as wav:
            seconds = wav.getnframes() / wav.getframerate()
    except (subprocess.SubprocessError, wave.Error, EOFError):
        raise ValueError("Choose a playable reference audio file.") from None
    if not 1 <= seconds <= 60:
        raise ValueError("Choose a reference recording between 1 and 60 seconds.")


def verified(path, item):
    if file_size(path) != item["size"]:
        return False
    digest = hashlib.sha256() if item["sha256"] else hashlib.sha1()
    if not item["sha256"]:
        digest.update(f"blob {item['size']}\0".encode())
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == (item["sha256"] or item["git_blob"])


def fetch_model_file(session, folder, item, progress):
    """Resume an interrupted transfer and publish only files matching the pinned manifest."""
    destination = folder / item["path"]
    if verified(destination, item):
        return
    name = quote(item["path"], safe="/")
    repo, rev = MANIFEST["repo"], MANIFEST["revision"]
    sources = (
        ("modelscope", f"https://modelscope.cn/models/{repo}/resolve/master/{name}"),
        ("huggingface", f"https://huggingface.co/{repo}/resolve/{rev}/{name}"),
    )
    partials = [destination.with_name(f"{destination.name}.{source}.part") for source, _ in sources]
    for (_, url), partial in zip(sources, partials):
        if not verified(partial, item):
            offset = file_size(partial)
            if offset >= item["size"]:
                offset = 0
            try:
                headers = {"Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                with session.get(url, headers=headers,
                                 stream=True, timeout=(12, 30)) as response:
                    response.raise_for_status()
                    if response.status_code == 206:
                        if not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                            continue
                    else:
                        offset = 0
                    length = response.headers.get("Content-Length")
                    if length is not None and int(length) != item["size"] - offset:
                        continue
                    with partial.open("ab" if offset else "wb") as stream:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            offset += len(chunk)
                            if offset > item["size"]:
                                break
                            stream.write(chunk)
                            progress(offset)
            except OSError:
                continue
        if verified(partial, item):
            partial.replace(destination)
            for candidate in partials:
                candidate.unlink(missing_ok=True)
            return
        if file_size(partial) >= item["size"]:
            partial.unlink()
    raise ValueError("VoxCPM2 download failed. Check your network and try again.")


def child_env():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("PIP_", "HF_", "HUGGINGFACE_")) or key.endswith(("_KEY", "_TOKEN")):
            env.pop(key)
    env.update(PIP_CONFIG_FILE=os.devnull, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1")
    return env


def report(state, **fields):
    print(json.dumps({"state": state, **fields}), flush=True)


def install(root):
    import fcntl
    import requests

    if problem := support_problem():
        raise ValueError(problem)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "install.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("VoxCPM2 is already being installed in another Grist window.") from None
        if ready(root):
            return
        folder, runtime = model_folder(root), runtime_folder(root)
        folder.mkdir(parents=True, exist_ok=True)
        missing = sum(max(0, item["size"] - file_size(folder / item["path"])) for item in MANIFEST["files"])
        if shutil.disk_usage(root).free < missing + 1_000_000_000:
            raise ValueError("Free up disk space before installing VoxCPM2.")
        downloaded = 0
        with requests.Session() as session:
            for item in MANIFEST["files"]:
                last = -1

                def progress(offset):
                    nonlocal last
                    percent = (downloaded + offset) * 100 // MODEL_BYTES
                    if percent != last:
                        report("downloading", percent=percent)
                        last = percent

                progress(0)
                fetch_model_file(session, folder, item, progress)
                downloaded += item["size"]
        report("installing")
        pending = runtime.with_suffix(".part")
        shutil.rmtree(pending, ignore_errors=True)
        try:
            subprocess.run([sys.executable, "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                            "--only-binary=:all:", "--platform", "macosx_14_0_arm64", "--require-hashes", "--no-deps",
                            "--no-compile", "--target", str(pending), "-r", str(LOCK)],
                           check=True, env=child_env(), stdout=sys.stderr)
            report("checking")
            subprocess.run(worker_command(root, pending) + ["--check"], check=True, env=child_env(), stdout=sys.stderr)
            (pending / ".ready").touch()
            shutil.rmtree(runtime, ignore_errors=True)
            pending.replace(runtime)
        except subprocess.CalledProcessError:
            raise ValueError("VoxCPM2 setup failed. Check your network and available memory, then try again.") from None
        finally:
            shutil.rmtree(pending, ignore_errors=True)


class Installer:
    def __init__(self, data):
        self.root = data / "local-voice" / PROVIDER
        self.lock = threading.Lock()
        self.process = None
        self.progress = {}

    def status(self):
        with self.lock:
            return self._status()

    def _status(self):
        if problem := support_problem():
            status = {"state": "unsupported", "error": problem}
        elif self.process:
            status = self.progress
        elif ready(self.root):
            status = {"state": "ready"}
        else:
            status = self.progress or {"state": "available"}
        return {**status, "model_bytes": MODEL_BYTES}

    def start(self):
        with self.lock:
            if self._status()["state"] in {"available", "failed"}:
                self.process = subprocess.Popen(
                    [sys.executable, "-I", "-u", __file__, "install", str(self.root)],
                    env=child_env(), stdout=subprocess.PIPE, stderr=None, text=True,
                    encoding="utf-8", errors="replace", start_new_session=True)
                self.progress = {"state": "downloading", "percent": 0}
                threading.Thread(target=self.observe, args=(self.process,), daemon=True).start()
            return self._status()

    def observe(self, process):
        with process.stdout:
            for line in process.stdout:
                try:
                    update = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    self.progress = update
        process.wait()
        with self.lock:
            self.process = None
            if ready(self.root):
                self.progress = {}
            elif self.progress.get("state") != "failed":
                self.progress = {"state": "failed", "error": "VoxCPM2 installation was interrupted. Try again."}

    def stop(self):
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


def worker_command(root, runtime):
    return [sys.executable, "-I", "-u", str(WORKER), str(root), str(runtime), str(model_folder(root))]


@contextmanager
def synthesizer(root, source):
    """One model load per job, shared by all its recordings including the opening."""
    from core.domain.metadata import parse_marked_focus_text
    from core.infra.ai.tts_client import remove_silence_from_pcm

    if not ready(root) or not source.is_file():
        raise ValueError("Install VoxCPM2 and choose a reference recording in Step 3.")
    print("Loading VoxCPM2. If another project is using it, this job will wait.", flush=True)
    with subprocess.Popen(worker_command(root, runtime_folder(root)), env=child_env(), stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True, encoding="utf-8") as worker:
        try:
            hello = json.loads(worker.stdout.readline() or "{}")
            if not hello.get("ready"):
                raise ValueError("VoxCPM2 could not load. Check available memory and retry.")

            def synthesize(text, output_filename, *, mute_cut_threshold, mute_cut_min_silence_ms,
                           mute_cut_remain_ms, **_):
                output = Path(output_filename)
                temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.part")
                try:
                    request = {"text": parse_marked_focus_text(text)[0], "reference": str(source),
                               "output": str(temporary)}
                    worker.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                    worker.stdin.flush()
                    result = json.loads(worker.stdout.readline() or "{}")
                    if not result.get("success"):
                        raise ValueError(result.get("error") or "VoxCPM2 stopped while generating the recording.")
                    if mute_cut_threshold > 0:
                        with wave.open(str(temporary), "rb") as wav:
                            params, pcm = wav.getparams(), wav.readframes(wav.getnframes())
                        pcm = remove_silence_from_pcm(pcm, params.framerate, mute_cut_threshold,
                                                      mute_cut_min_silence_ms, mute_cut_remain_ms)
                        with wave.open(str(temporary), "wb") as wav:
                            wav.setparams(params)
                            wav.writeframes(pcm)
                    temporary.replace(output)
                    output.with_suffix(".json").unlink(missing_ok=True)
                    return True
                finally:
                    temporary.unlink(missing_ok=True)

            yield synthesize
        finally:
            with suppress(BrokenPipeError):
                worker.stdin.close()
            if worker.poll() is None:
                worker.terminate()


if __name__ == "__main__":
    try:
        install(Path(sys.argv[2]))
    except (ValueError, OSError) as exc:
        report("failed", error=str(exc))
        sys.exit(1)

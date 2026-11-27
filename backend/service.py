"""Discover or start the single user-local Grist service."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

PROTOCOL = 1
ENDPOINT = "service.json"
OPENER = build_opener(ProxyHandler({}))


@contextmanager
def service_lock(data: Path):
    """Hold the lock for the lifetime of the background server."""
    data.mkdir(parents=True, exist_ok=True)
    with (data / "service.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        yield True


@contextmanager
def publish_endpoint(data: Path, port: int, token: str):
    """Publish only after initialization, with permissions set before writing."""
    endpoint = data / ENDPOINT
    pending = data / f".service-{os.getpid()}.json"
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"protocol": PROTOCOL, "pid": os.getpid(), "port": port, "token": token}, stream)
        pending.replace(endpoint)
        try:
            yield
        finally:
            endpoint.unlink(missing_ok=True)
    finally:
        pending.unlink(missing_ok=True)


def request(endpoint, path, body=None, *, timeout=30):
    headers = {"Authorization": f"Bearer {endpoint['token']}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(f"http://127.0.0.1:{endpoint['port']}{path}", headers=headers, data=body)
    with OPENER.open(req, timeout=timeout) as response:
        return json.load(response)


def discover(data: Path):
    try:
        endpoint = data / ENDPOINT
        if os.name != "nt" and endpoint.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            return None
        info = json.loads(endpoint.read_text(encoding="utf-8"))
        if (type(info["port"]) is not int or not 0 < info["port"] <= 65535
                or not isinstance(info["token"], str)):
            return None
        health = request(info, "/api/service", timeout=1)
    except (OSError, URLError, ValueError, KeyError, TypeError):
        return None
    if health != {"pid": info.get("pid"), "protocol": PROTOCOL}:
        raise ValueError("The running Grist service uses a different protocol. Stop it before updating.")
    return info


def ensure(data: Path, engine: Path, workspace: Path, server: Path, credentials: dict):
    if existing := discover(data):
        return existing
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", str(server), "--shared", "--data", str(data),
               "--engine", str(engine), "--workspace", str(workspace)]
    with (logs / "service.log").open("ab") as log:
        process = subprocess.Popen(command, cwd=data, stdin=subprocess.PIPE, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                                   creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
                                   if os.name == "nt" else 0)
    try:
        process.stdin.write(json.dumps(credentials).encode("utf-8") + b"\n")
    except BrokenPipeError:
        pass
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if existing := discover(data):
            return existing
        if process.poll() not in (None, 0):
            break
        time.sleep(0.1)
    raise ValueError(f"Cannot start the Grist service. See {logs / 'service.log'}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = ensure(args.data, args.engine, args.workspace, Path(__file__).with_name("server.py"),
                        json.load(sys.stdin))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

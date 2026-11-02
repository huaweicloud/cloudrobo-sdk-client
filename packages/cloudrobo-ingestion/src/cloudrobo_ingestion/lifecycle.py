"""Per-user lock, local authenticated control and detached process launch."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from multiprocessing.connection import Client
from pathlib import Path


def runtime_dir():
    # Override permits isolated integration tests and explicitly managed users.
    return Path(
        os.environ.get("CLOUDROBO_INGESTION_RUNTIME_DIR", str(Path.home() / ".cloudrobo/ingestion"))
    ).resolve()


def private_dir(path):
    from .filesystem import is_link

    if path.exists() and is_link(path):
        raise ValueError("运行和工作目录不允许链接或 junction")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


def write_json(path, value):
    """Atomically replace JSON using a temporary file with mode 0600."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows status readers can briefly prevent replacing an
                # open file; retry without exposing a partially written JSON.
                if attempt == 4:
                    raise
                time.sleep(0.02)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


class InstanceLock:
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def __enter__(self):
        self.file = self.path.open("a+b")
        # Windows byte locks are mandatory: reading the locked byte before
        # trying to acquire it would make status() raise PermissionError.
        if os.fstat(self.file.fileno()).st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("已有回流进程正在运行") from exc
        return self

    def __exit__(self, *args):
        if self.file:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


def running(root=None):
    root = root or runtime_dir()
    if not (root / "instance.lock").exists():
        return False
    try:
        with InstanceLock(root / "instance.lock"):
            return False
    except RuntimeError:
        return True


def status():
    root = runtime_dir()
    from .diagnostics import make_error, summary

    try:
        live = running(root)
    except OSError as exc:
        info = make_error("local", "control", exc, context=dict(path=str(root / "instance.lock")))
        return dict(running=False, state="unknown", error=summary(info), error_info=info)
    try:
        for attempt in range(5):
            try:
                value = json.loads((root / "status.json").read_text(encoding="utf-8"))
                break
            except PermissionError:
                # Windows can briefly deny opening the old status file while
                # the daemon atomically replaces it. Keep persistent failures
                # visible, but do not discard state for a transient collision.
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except FileNotFoundError:
        value = {}
    except (OSError, ValueError) as exc:
        info = make_error("local", "status", exc, context=dict(path=str(root / "status.json")))
        value = dict(error=summary(info), error_info=info)
    value["running"] = live
    if not live and value.get("state") != "failed":
        value["state"] = "stopped"
    return value


def start(config_path, overrides=None):
    from .config import IngestionConfig
    from .workspace import check_mode

    config_path = Path(config_path).resolve()
    config = IngestionConfig.load(config_path, overrides=overrides)
    check_mode(config)
    private_dir(runtime_dir())
    private_dir(config.work_dir)
    # Serialize competing start commands independently of the daemon lock.
    with InstanceLock(runtime_dir() / "start.lock"), tempfile.TemporaryDirectory(
        prefix="startup-", dir=config.work_dir
    ) as startup_dir:
        if running():
            raise RuntimeError("已有回流进程正在运行")
        check_mode(config)
        handshake = Path(startup_dir) / "handshake.json"
        effective = Path(startup_dir) / "effective.json"
        # Pass the validated effective configuration rather than re-reading the
        # original YAML in the child (which would lose CLI overrides).
        write_json(effective, config.to_mapping())
        command = [
            sys.executable,
            "-m",
            "cloudrobo_ingestion.daemon",
            str(config_path),
            str(handshake),
            str(effective),
        ]
        kwargs = dict(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if handshake.exists():
                result = json.loads(handshake.read_text(encoding="utf-8"))
                if result.get("error"):
                    if result.get("error_info"):
                        from .diagnostics import DiagnosticError

                        raise DiagnosticError(result["error_info"])
                    raise RuntimeError(result["error"])
                return result
            if process.poll() is not None:
                raise RuntimeError("后台进程启动失败，请查看工作目录 ingestion.log")
            time.sleep(0.1)
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise RuntimeError("后台进程启动超时")


def stop(timeout=15):
    root = runtime_dir()
    if not running(root):
        return {"running": False, "state": "stopped"}
    endpoint = json.loads((root / "control.json").read_text(encoding="utf-8"))
    # Authenticated local control avoids PID reuse and platform-specific signals.
    connection = Client(("127.0.0.1", endpoint["port"]), authkey=bytes.fromhex(endpoint["token"]))
    try:
        connection.send("stop")
        if not connection.poll(2) or connection.recv() != "stopping":
            raise RuntimeError("进程未确认停止请求")
    finally:
        connection.close()
    deadline = time.monotonic() + timeout
    while running(root) and time.monotonic() < deadline:
        time.sleep(0.1)
    if running(root):
        raise RuntimeError("停止超时，进程仍在清理；可使用 status 查看状态")
    return status()

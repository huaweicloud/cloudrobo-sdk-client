#!/usr/bin/env python3
"""Dev Services - manages r2c-client via system-level supervisord.

supervisord is managed by systemd (starts at container boot).
This script does NOT manage supervisord's lifecycle — it only
writes program configs and calls supervisorctl.

Supervisord config paths (socket, pidfile, include dir) are
auto-detected from the running supervisord's config file, so
the script works across different container setups without
hardcoded paths.

Services:
  - r2c-client:          R2C client application
                         (./examples/robots_app.py)

Robot type is determined by DEV_SERVICES_ROBOT_TYPE env var
(default: r1). Only alphanumeric, underscore, and hyphen are
allowed (validated to prevent command injection).

start and restart automatically install the transformer package
before launching services. After modifying Python code or config
files, run restart to reload changes.

Environment variables:
    DEV_SERVICES_ROBOT_TYPE   Robot type (default: r1)
    DEV_SERVICES_WORKSPACE    Workspace root (default: /data/workspace)
    SUPERVISORD_CONF_FILE     supervisord config path (auto-detected)
    SUPERVISORD_CONF_DIR      program config dir (auto-detected)
    SUPERVISORD_PID_FILE      supervisord PID file (auto-detected)

Prerequisite:
    pip install supervisor
    System-level supervisord running (managed by systemd)

Usage:
    python3 ./examples/dev_services.py start          Deploy configs, install transformer, start services
    python3 ./examples/dev_services.py stop           Stop all services
    python3 ./examples/dev_services.py status         Show service status
    python3 ./examples/dev_services.py logs [svc] [-f] [-n N]  View logs
    python3 ./examples/dev_services.py restart [svc]  Install transformer, restart a service (or all)

See also:
    examples/dev_services_guide.md    Operation guide
    docs/dev_services_design.md       Design document
"""

from __future__ import annotations

import argparse
import configparser
import enum
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from robot_common_utils import ROBOT_TYPE

# ── Configuration ─────────────────────────────────────────────────────────────

WORKSPACE = os.environ.get("DEV_SERVICES_WORKSPACE", "/data/workspace")
STATE_DIR = Path(WORKSPACE) / ".dev_services"
LOG_DIR = STATE_DIR / "logs"
logger = logging.getLogger("dev_services")
ROS2_SETUP = "/opt/ros/humble/setup.bash"
R2C_DIR = f"{WORKSPACE}/cloudrobo_r2c_python"
TRANSFORMER_DIR = f"{WORKSPACE}/eail/Assets/robots/robot_{ROBOT_TYPE}/{ROBOT_TYPE}_transformer"

# ── Supervisord config auto-detection ────────────────────────────────────────


def _find_supervisord_conf() -> str:
    """Find the config file used by the running supervisord.

    Priority: SUPERVISORD_CONF_FILE env > /proc/PID/cmdline -c arg >
    /etc/supervisor/supervisord.conf
    """
    env_conf = os.environ.get("SUPERVISORD_CONF_FILE")
    if env_conf and os.path.isfile(env_conf):
        return env_conf

    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-o", "supervisord"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pid_str = result.stdout.strip().splitlines()[0]
            cmdline_bytes = Path(
                f"/proc/{pid_str}/cmdline"
            ).read_bytes()
            parts = [p.decode("utf-8", errors="replace") for p in cmdline_bytes.split(b"\x00") if p]
            for i, part in enumerate(parts):
                if part == "-c" and i + 1 < len(parts):
                    conf = parts[i + 1]
                    if not os.path.isabs(conf):
                        cwd = os.path.realpath(
                            f"/proc/{pid_str}/cwd"
                        )
                        conf = os.path.join(cwd, conf)
                    if os.path.isfile(conf):
                        return conf
    except (FileNotFoundError, PermissionError) as e:
        logger.debug(
            "Cannot read /proc for supervisord config "
            "(process exited or permission denied): %s, "
            "using default", e
        )
    except Exception as e:
        logger.debug(
            "Failed to detect supervisord config from "
            "/proc: %s, using default", e
        )

    return "/etc/supervisor/supervisord.conf"


def _parse_supervisord_conf(
    conf_path: str,
) -> dict[str, str]:
    """Parse supervisord config and extract key paths.

    Returns dict with: conf_file, pidfile, conf_dir
    """
    info = {
        "conf_file": conf_path,
        "pidfile": "/var/run/supervisord.pid",
        "conf_dir": "/etc/supervisor/conf.d",
        "sockfile": "/var/run/supervisor.sock",
        "logfile": "/var/log/supervisor/supervisord.log",
    }
    try:
        parser = configparser.ConfigParser()
        parser.read(conf_path, encoding="utf-8")

        if parser.has_section("supervisord"):
            info["pidfile"] = parser.get(
                "supervisord", "pidfile",
                fallback=info["pidfile"],
            )
            info["logfile"] = parser.get(
                "supervisord", "logfile",
                fallback=info["logfile"],
            )

        if parser.has_section("unix_http_server"):
            info["sockfile"] = parser.get(
                "unix_http_server", "file",
                fallback=info["sockfile"],
            )

        if parser.has_section("include"):
            files = parser.get(
                "include", "files", fallback=""
            )
            if files:
                # Extract dir from glob like /home/service/supervisord.d/*.conf
                first_spec = files.split()[0]
                info["conf_dir"] = os.path.dirname(first_spec)
    except Exception as e:
        logger.warning(
            "Failed to parse supervisord config %s: %s",
            conf_path, e,
        )
    return info


SUPERVISORD_CONF_FILE = _find_supervisord_conf()
_SUP_INFO = _parse_supervisord_conf(SUPERVISORD_CONF_FILE)
SUPERVISORD_CONF_DIR = Path(
    os.environ.get(
        "SUPERVISORD_CONF_DIR", _SUP_INFO["conf_dir"]
    )
)
SUPERVISORD_PID_FILE = Path(
    os.environ.get(
        "SUPERVISORD_PID_FILE", _SUP_INFO["pidfile"]
    )
)
SUPERVISORD_SOCKET = os.environ.get(
    "SUPERVISORD_SOCKET", _SUP_INFO["sockfile"]
)
SUPERVISORD_LOGFILE = os.environ.get(
    "SUPERVISORD_LOGFILE", _SUP_INFO["logfile"]
)


# ── Shared shell fragments ─────────────────────────────────────────────────────

SOURCE_ROS2 = f"source {shlex.quote(ROS2_SETUP)}"


def make_service_config() -> dict[str, dict[str, str]]:
    """Generate SERVICE_CONFIG."""
    return {
        "r2c-client": {
            "name": "R2C Client",
            "cmd": (
                f"{SOURCE_ROS2} && "
                f"cd {shlex.quote(R2C_DIR)} && "
                f"/usr/bin/python3 ./examples/robots_app.py"
            ),
            "cwd": R2C_DIR,
        },
    }


def make_orphan_patterns() -> dict[str, list[str]]:
    """Generate ORPHAN_PATTERNS."""
    return {
        "r2c-client": ["robots_app.py"],
    }


SERVICE_CONFIG: dict[str, dict[str, str]] = make_service_config()
ORPHAN_PATTERNS: dict[str, list[str]] = make_orphan_patterns()


# ── Build ─────────────────────────────────────────────────────────────────────


def install_transformer(timeout: int = 120) -> tuple[bool, str]:
    """Install robot transformer package in editable mode.

    Runs 'pip install --no-build-isolation -e .' in the transformer
    directory. Users can modify the package content and re-run
    start/restart to pick up changes.

    Returns:
        (success, message)
    """
    if not os.path.isdir(TRANSFORMER_DIR):
        return False, f"Transformer directory not found: {TRANSFORMER_DIR}"

    logger.info("Installing transformer: %s", TRANSFORMER_DIR)
    try:
        result = subprocess.run(
            [
                "/usr/bin/python3", "-m", "pip", "install",
                "--no-build-isolation", "-e", ".",
            ],
            timeout=timeout,
            env=os.environ.copy(),
            cwd=TRANSFORMER_DIR,
        )
        if result.returncode == 0:
            return True, "transformer install succeeded"
        return False, (
            f"transformer install failed "
            f"(exit code {result.returncode})"
        )
    except subprocess.TimeoutExpired:
        return False, f"transformer install timed out after {timeout}s"
    except Exception as e:
        logger.exception("transformer install failed unexpectedly")
        return False, f"transformer install error: {e}"


# ── System-level supervisord helpers ──────────────────────────────────────────


def get_supervisord_pid() -> int:
    """Get system-level supervisord PID.

    Tries /var/run/supervisord.pid first, falls back to pgrep.
    Returns 0 if not found.
    """
    try:
        return int(
            SUPERVISORD_PID_FILE.read_text(encoding="utf-8").strip()
        )
    except (ValueError, OSError):
        pass
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-o", "supervisord"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0])
    except Exception as e:
        logger.debug("Failed to get supervisord PID via pgrep: %s", e)
    return 0


def supervisorctl(*args: str) -> subprocess.CompletedProcess[str]:
    """Run supervisorctl with the same config as supervisord.

    Returns a CompletedProcess with returncode=-1 on timeout.
    """
    try:
        return subprocess.run(
            ["supervisorctl", "-c", SUPERVISORD_CONF_FILE]
            + list(args),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["supervisorctl"] + list(args),
            returncode=-1,
            stdout="",
            stderr="supervisorctl timed out after 30s",
        )


def generate_program_configs(log_level: str = "") -> bool:
    """Write program config files to supervisord conf.d directory.

    Args:
        log_level: If non-empty, sets LOG_LEVEL environment
            variable in the supervisord program config so that
            robots_app.py picks it up on next start.

    Returns True on success, False on failure.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SUPERVISORD_CONF_DIR.mkdir(parents=True, exist_ok=True)

    for svc, cfg in SERVICE_CONFIG.items():
        lines = [
            f"[program:{svc}]",
            f'command=bash -c "{cfg["cmd"]}"',
            f"directory={cfg['cwd']}",
            "autostart=true",
            "autorestart=true",
            "startsecs=5",
            "startretries=5",
            "stopasgroup=true",
            "killasgroup=true",
            "stopsignal=TERM",
            "stopwaitsecs=15",
            "redirect_stderr=true",
            f"stdout_logfile={LOG_DIR / f'{svc}.log'}",
            "stdout_logfile_maxbytes=10MB",
            "stdout_logfile_backups=3",
        ]
        if log_level:
            lines.append(
                f'environment=LOG_LEVEL="{log_level}"'
            )
        conf_file = SUPERVISORD_CONF_DIR / f"dev_services-{svc}.conf"
        try:
            conf_file.write_text(
                "\n".join(lines), encoding="utf-8"
            )
            logger.info("Wrote program config: %s", conf_file)
        except OSError as e:
            logger.error("Failed to write %s: %s", conf_file, e)
            print(f"ERROR: Failed to write {conf_file}: {e}")
            return False

    log_dir_quoted = shlex.quote(str(LOG_DIR))
    log_files = " ".join(
        shlex.quote(str(LOG_DIR / f"{svc}.log")) for svc in SERVICE_CONFIG
    )
    tail_parts = " ".join(
        f"tail -n 0 -F {shlex.quote(str(LOG_DIR / f'{svc}.log'))} "
        f"| sed -u 's/^/[{svc}] /' &"
        for svc in SERVICE_CONFIG
    )
    forwarder_lines = [
        "[program:dev-services-log-forwarder]",
        f'command=bash -c "exec > /proc/1/fd/1 '
        f"2>/proc/1/fd/2; "
        f"mkdir -p {log_dir_quoted} && touch {log_files} "
        f"&& {tail_parts} wait\"",
        "directory=/tmp",
        "autostart=true",
        "autorestart=true",
        "startsecs=0",
        "stdout_logfile=NONE",
        "stderr_logfile=NONE",
        "priority=10",
    ]
    forwarder_conf = (
        SUPERVISORD_CONF_DIR / "dev_services-log-forwarder.conf"
    )
    try:
        forwarder_conf.write_text(
            "\n".join(forwarder_lines), encoding="utf-8"
        )
        logger.info(
            "Wrote log forwarder config: %s", forwarder_conf
        )
    except OSError as e:
        logger.error(
            "Failed to write %s: %s", forwarder_conf, e
        )
        print(f"ERROR: Failed to write {forwarder_conf}: {e}")
        return False

    return True


def get_log_path(svc: str) -> Path:
    """Return the log file path for a service."""
    return LOG_DIR / f"{svc}.log"


# ── Orphan Process Cleanup ────────────────────────────────────────────────────


def find_orphan_pids(svc: str) -> list[int]:
    """Find PIDs of orphaned processes matching a service's patterns.

    Excludes processes managed by supervisord (whose parent is
    the system-level supervisord).
    """
    patterns = ORPHAN_PATTERNS.get(svc, [])
    if not patterns:
        return []

    pids: list[int] = []
    supervisord_pid_val = get_supervisord_pid()

    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)

            stat_path = Path(f"/proc/{pid}/stat")
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            try:
                stat_data = stat_path.read_text()
                rparen = stat_data.rfind(")")
                ppid = int(stat_data[rparen + 1:].split()[1])
            except (FileNotFoundError, PermissionError, ValueError, IndexError):
                continue

            try:
                cmdline_bytes = cmdline_path.read_bytes()
                args = " ".join(
                    p.decode("utf-8", errors="replace")
                    for p in cmdline_bytes.split(b"\x00") if p
                )
            except (FileNotFoundError, PermissionError):
                continue

            if not args:
                continue

            tokens = args.split()
            if not any(
                any(p == t or t.endswith(p) or p in t for t in tokens)
                for p in patterns
            ):
                continue

            if ppid == supervisord_pid_val:
                continue

            if "dev_services.py" in args or "supervisorctl" in args:
                continue

            if "grep" in args or "ps" in tokens:
                continue

            pids.append(pid)
    except Exception as e:
        logger.warning("Failed to find orphan processes: %s", e)

    return pids


def _pid_matches_patterns(pid: int, patterns: list[str]) -> bool:
    """Check if a PID's command line still matches orphan patterns.

    Reads /proc/{pid}/cmdline to verify the process hasn't been
    replaced by a different one (PID reuse protection).
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        if not cmdline:
            return False
        # /proc/PID/cmdline is null-byte separated
        tokens = cmdline.replace(b"\x00", b" ").decode(
            "utf-8", errors="replace"
        ).split()
        return any(
            any(p == t or t.endswith(p) or p in t for t in tokens)
            for p in patterns
        )
    except OSError:
        return False


def kill_orphans(svc: str) -> int:
    """Kill orphaned processes for a service. Returns count killed.

    Uses /proc/{pid}/cmdline to verify the process still matches
    orphan patterns before sending SIGKILL, preventing PID reuse
    from killing unrelated processes.
    """
    pids = find_orphan_pids(svc)
    patterns = ORPHAN_PATTERNS.get(svc, [])
    killed = 0
    term_pids: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            term_pids.append(pid)
        except (ProcessLookupError, PermissionError):
            continue

    for pid in term_pids:
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                killed += 1
                break
            except PermissionError:
                logger.warning(
                    "Permission denied checking process %d, skipping", pid
                )
                break
            time.sleep(0.1)
        else:
            if not _pid_matches_patterns(pid, patterns):
                logger.warning(
                    "PID %d no longer matches orphan patterns, "
                    "skipping SIGKILL (possible PID reuse)",
                    pid,
                )
                continue

            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass

    return killed


def kill_all_orphans() -> int:
    """Kill orphaned processes for all services. Returns total killed."""
    total = 0
    for svc in SERVICE_CONFIG:
        count = kill_orphans(svc)
        if count > 0:
            print(f"  Killed {count} orphan process(es) for {svc}")
            total += count
    return total


# ── CLI Commands ──────────────────────────────────────────────────────────────


def _check_supervisord_socket() -> int:
    """Check supervisord socket exists. Returns 0 if OK, 1 if not."""
    if not os.path.exists(SUPERVISORD_SOCKET):
        print("supervisord socket not found. Start it first:")
        print("  service supervisord start")
        return 1
    return 0


def cleanup_zenoh_shm(_shm_dir: Path | None = None) -> int:
    """Clean up leaked Zenoh POSIX shared memory segments in /dev/shm.

    Zenoh uses POSIX shm segments for zero-copy transport.  If a
    process is killed without calling session.close() (e.g.
    SIGKILL after stopwaitsecs), the segments leak and eventually
    exhaust /dev/shm, causing zenoh.open() to fail with OS
    error 12 (ENOMEM).

    Only segments whose names contain 'zenoh' are removed, to
    avoid affecting other applications (e.g. FastDDS/ROS2 uses
    /dev/shm/fastrtps_* files for SHM transport).

    Args:
        _shm_dir: Override the shm directory (for testing).

    Returns the number of segments removed.
    """
    shm_dir = _shm_dir or Path("/dev/shm")
    if not shm_dir.is_dir():
        return 0
    count = 0
    try:
        entries = list(shm_dir.iterdir())
    except OSError as e:
        logger.warning("Cannot read %s: %s", shm_dir, e)
        return 0
    for entry in entries:
        if entry.is_dir():
            continue
        if "zenoh" not in entry.name.lower():
            continue
        try:
            entry.unlink()
            count += 1
        except OSError as e:
            logger.debug("Could not remove %s: %s", entry, e)
    return count


def _stop_clean_start(svc: str) -> int:
    """Stop a service, clean up shm, then start it.

    Replaces supervisorctl restart with a split stop+start so
    that leaked Zenoh POSIX shm segments can be cleaned up
    between the two phases.  Returns 0 on success, non-zero
    on failure (after checking autorestart recovery).
    """
    stop_result = supervisorctl("stop", svc)
    stop_msg = stop_result.stdout.strip()
    if stop_msg.startswith(f"{svc}:"):
        stop_msg = stop_msg[len(svc) + 1:].strip()
    print(f"  {svc}: {stop_msg}")
    if stop_result.returncode != 0 and stop_result.stderr:
        print(f"  {svc}: {stop_result.stderr.strip()}")

    killed = kill_orphans(svc)
    if killed > 0:
        print(f"  {svc}: killed {killed} orphan process(es)")

    cleaned = cleanup_zenoh_shm()
    if cleaned > 0:
        print(f"  Cleaned {cleaned} leaked shm segment(s) in /dev/shm")

    status_result = supervisorctl("status", svc)
    if "STOPPED" not in status_result.stdout:
        print(f"  {svc}: already running (auto-restarted)")
        return 0

    start_result = supervisorctl("start", svc)
    start_status = (
        start_result.stdout.strip() or start_result.stderr.strip()
    )
    if start_result.returncode != 0:
        if "already started" in start_status:
            print(f"  {svc}: already running (auto-restarted)")
            return 0
        print(
            f"  {svc}: initial start failed, "
            f"waiting for autorestart recovery..."
        )
        if _wait_for_service_running(svc):
            print(
                f"  {svc}: recovered "
                f"(RUNNING via autorestart)"
            )
            return 0
        _print_supervisord_log_snippet(svc)
        print(f"  {svc}: FAILED to start")
        return start_result.returncode
    print(f"  {svc}: started")
    return 0


def _wait_for_service_running(
    svc: str, timeout: int = 20
) -> bool:
    """Wait for a service to reach RUNNING state.

    After a spawn error, supervisord's autorestart may recover
    the service within a few retries. This function polls
    supervisorctl status until the service is RUNNING or timeout.

    Returns True if RUNNING, False otherwise.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        result = supervisorctl("status", svc)
        output = result.stdout.strip()
        if "RUNNING" in output:
            return True
    return False


def _print_supervisord_log_snippet(
    svc: str, n_lines: int = 30
) -> None:
    """Print recent supervisord log lines for a service.

    supervisord logs spawn errors and process state changes
    in its own log file (not the service's stdout log).
    This helps users find the real error behind a spawn error.
    """
    log_path = Path(SUPERVISORD_LOGFILE)
    if not log_path.exists():
        print(
            f"  supervisord log not found at "
            f"{log_path}"
        )
        return
    try:
        result = subprocess.run(
            [
                "/usr/bin/grep", svc,
                str(log_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.splitlines()
        if not lines:
            print(
                f"  No supervisord log entries for '{svc}' "
                f"in {log_path}"
            )
            return
        recent = lines[-n_lines:]
        print(
            f"  --- supervisord log (last "
            f"{len(recent)} lines for '{svc}') ---"
        )
        for line in recent:
            print(f"  {line}")
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  Could not read supervisord log: {e}")


def _prepare_and_deploy() -> bool:
    """Kill orphans, install transformer, deploy configs, sync supervisord.

    Shared by cmd_start and cmd_restart (restart all).
    Returns True on success, False if config deployment fails.
    """
    print("Checking for orphan processes...")
    killed = kill_all_orphans()
    if killed > 0:
        print(f"  Total {killed} orphan process(es) cleaned.")
    else:
        print("  No orphans found.")

    cleaned = cleanup_zenoh_shm()
    if cleaned > 0:
        print(f"  Cleaned {cleaned} leaked shm segment(s) in /dev/shm")

    if "r2c-client" in SERVICE_CONFIG:
        print("Installing transformer package...")
        ok, msg = install_transformer()
        print(f"  {msg}")
        if not ok:
            print("  WARNING: r2c-client may fail to start.")

    print("Deploying program configs...")
    current_log_level = _read_log_level_from_config()
    if not generate_program_configs(log_level=current_log_level):
        print("ERROR: Failed to deploy program configs.")
        return False

    result = supervisorctl("reread")
    print(f"  reread: {result.stdout.strip()}")
    if result.returncode != 0 and result.stderr:
        print(f"  reread WARNING: {result.stderr.strip()}")
    result = supervisorctl("update")
    print(f"  update: {result.stdout.strip()}")
    if result.returncode != 0 and result.stderr:
        print(f"  update WARNING: {result.stderr.strip()}")

    # If update restarted any services (config changed), wait
    # briefly for them to settle before the caller does another
    # explicit restart — avoids spawn errors from port/resource
    # conflicts between the stopping and starting processes.
    update_output = result.stdout.lower()
    if "restarted" in update_output or "started" in update_output:
        print("  Waiting for update-triggered restart to settle...")
        time.sleep(3)

    return True


def cmd_start(args: argparse.Namespace) -> int:
    """Deploy program configs and start services."""
    sup_pid = get_supervisord_pid()
    if not sup_pid:
        print("supervisord is not running. Start it first:")
        print("  service supervisord start")
        return 1

    rc = _check_supervisord_socket()
    if rc:
        return rc

    if not _prepare_and_deploy():
        return 1

    # Ensure all services are started (update may not start
    # already-known but stopped services)
    rc = 0
    for svc in SERVICE_CONFIG:
        result = supervisorctl("start", svc)
        if result.returncode != 0:
            if "already started" not in result.stdout:
                print(
                    f"  {svc}: FAILED — "
                    f"{result.stdout.strip()} "
                    f"{result.stderr.strip()}"
                )
                print(
                    f"  {svc}: waiting for "
                    f"autorestart recovery..."
                )
                if _wait_for_service_running(svc):
                    print(
                        f"  {svc}: recovered "
                        f"(RUNNING via autorestart)"
                    )
                else:
                    _print_supervisord_log_snippet(svc)
                    rc = 1
            else:
                print(f"  {svc}: already started")
        else:
            print(f"  {svc}: started")

    print(f"\n  Logs: {LOG_DIR}/")
    cmd_status(args)
    if rc:
        print(
            "\nERROR: One or more services failed to start. "
            "Check logs:"
            f"\n  Service logs: python3 ./examples/dev_services.py logs -f"
            f"\n  Supervisord log: {SUPERVISORD_LOGFILE}"
        )
    return rc


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop all services (supervisord itself keeps running)."""
    rc = _check_supervisord_socket()
    if rc:
        return rc

    print("Stopping all services...")
    rc = 0
    for svc in SERVICE_CONFIG:
        result = supervisorctl("stop", svc)
        status = result.stdout.strip() or result.stderr.strip()
        print(f"  {svc}: {status}")
        if result.returncode != 0:
            rc = 1
    return rc


def cmd_status(args: argparse.Namespace) -> int:
    """Show status for dev_services managed services only."""
    result = supervisorctl("status")
    if result.returncode != 0 and result.stderr:
        print(result.stderr.strip())
        return result.returncode

    for line in result.stdout.strip().splitlines():
        name = line.split()[0] if line.strip() else ""
        if name in SERVICE_CONFIG:
            print(line)
    return result.returncode


def _tail_lines(log_path: Path, n_lines: int) -> list[str]:
    """Read last N lines from a log file using tail."""
    try:
        result = subprocess.run(
            ["/usr/bin/tail", "-n", str(n_lines), str(log_path)],
            capture_output=True, text=True, check=True,
            timeout=10,
        )
        return result.stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _print_logs(services: list[str], n_lines: int) -> None:
    """Print last N lines from each service log."""
    for s in services:
        log_path = get_log_path(s)
        if not log_path.exists():
            print(f"[{s}] No log file found at {log_path}")
            continue
        lines = _tail_lines(log_path, n_lines)
        if lines:
            print(
                f"\n{'=' * 20} {s} (last {n_lines} lines) "
                f"{'=' * 20}"
            )
            for line in lines:
                print(f"[{s}] {line}")


def _read_new_log_lines(
    log_path: Path, positions: dict[str, int], svc: str
) -> list[str]:
    """Read new lines from a log file since last position."""
    try:
        current_size = log_path.stat().st_size
    except OSError:
        return []
    if current_size < positions.get(svc, 0):
        positions[svc] = 0
    try:
        with open(log_path, "rb") as f:
            f.seek(positions.get(svc, 0))
            new_data = f.read().decode("utf-8", errors="replace")
            positions[svc] = f.tell()
    except OSError:
        return []
    if not new_data:
        return []
    return new_data.splitlines(keepends=True)


def _follow_logs(services: list[str]) -> None:
    """Follow service logs in real-time."""
    positions: dict[str, int] = {}
    for s in services:
        log_path = get_log_path(s)
        try:
            positions[s] = log_path.stat().st_size
        except OSError:
            positions[s] = 0

    print("Following logs (Ctrl+C to stop)...")
    try:
        while True:
            for s in services:
                log_path = get_log_path(s)
                try:
                    for line in _read_new_log_lines(
                        log_path, positions, s
                    ):
                        print(
                            f"[{s}] {line}", end="", flush=True
                        )
                except OSError:
                    pass
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped following logs.")


def cmd_logs(args: argparse.Namespace) -> int:
    """View service logs (optionally follow)."""
    svc = args.service
    if svc and svc not in SERVICE_CONFIG:
        available = ", ".join(SERVICE_CONFIG)
        print(f"Unknown service: {svc}. Available: {available}")
        return 1

    services = [svc] if svc else list(SERVICE_CONFIG)
    if args.follow:
        _follow_logs(services)
    else:
        _print_logs(services, args.lines)
    return 0


class LogLevel(enum.Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


def _read_log_level_from_config() -> str:
    """Read LOG_LEVEL from existing supervisord config file.

    Returns the level string (e.g. "DEBUG") or empty string
    if not set or config not found.
    """
    for svc in SERVICE_CONFIG:
        conf_file = SUPERVISORD_CONF_DIR / f"dev_services-{svc}.conf"
        if not conf_file.exists():
            continue
        try:
            text = conf_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("environment=") and "LOG_LEVEL" in line:
                m = re.search(r'LOG_LEVEL="([^"]+)"', line)
                if m:
                    return m.group(1)
        break
    return ""


def cmd_set_log_level(args: argparse.Namespace) -> int:
    """Set log level for services and reload supervisord config.

    Writes environment=LOG_LEVEL="<level>" into the supervisord
    program config, then runs reread + update.  Call restart
    afterwards to apply the new level to running services.
    """
    level = args.level.upper()
    try:
        log_level = LogLevel(level)
    except ValueError:
        valid = ", ".join(sorted(m.value for m in LogLevel))
        print(
            f"Invalid log level: {level}. "
            f"Valid: {valid}"
        )
        return 1

    level = log_level.value
    print(f"Setting log level to {level} ...")
    if not generate_program_configs(log_level=level):
        print("ERROR: Failed to write program configs.")
        return 1

    rc = _check_supervisord_socket()
    if rc:
        print(
            f"Config written, but supervisord not reachable. "
            f"Run 'restart' after supervisord is available."
        )
        return 0

    supervisorctl("reread")
    supervisorctl("update")
    print(f"Log level set to {level}. Run 'restart' to apply.")
    return 0


def cmd_get_log_level(args: argparse.Namespace) -> int:
    """Show current log level from supervisord config."""
    for svc in SERVICE_CONFIG:
        conf_file = SUPERVISORD_CONF_DIR / f"dev_services-{svc}.conf"
        if not conf_file.exists():
            print(f"  {svc}: config not found ({conf_file})")
            continue
        try:
            text = conf_file.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  {svc}: error reading config: {e}")
            continue
        level = "INFO (default)"
        for line in text.splitlines():
            if line.startswith("environment=") and "LOG_LEVEL" in line:
                m = re.search(r'LOG_LEVEL="([^"]+)"', line)
                if m:
                    level = m.group(1)
                break
        print(f"  {svc}: {level}")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    """Restart one or all services via supervisorctl."""
    rc = _check_supervisord_socket()
    if rc:
        return rc

    if args.service:
        if args.service not in SERVICE_CONFIG:
            available = ", ".join(SERVICE_CONFIG)
            print(
                f"Unknown service: {args.service}. "
                f"Available: {available}"
            )
            return 1
        print(f"Checking orphans for {args.service}...")
        killed = kill_orphans(args.service)
        if killed > 0:
            print(f"  Killed {killed} orphan process(es).")
        if args.service == "r2c-client":
            print("Installing transformer package...")
            ok, msg = install_transformer()
            print(f"  {msg}")
            if not ok:
                print("  WARNING: r2c-client may fail to start.")
        print(f"Restarting {args.service}...")
        return _stop_clean_start(args.service)

    if not _prepare_and_deploy():
        return 1
    print("Restarting all services...")
    rc = 0
    for svc in SERVICE_CONFIG:
        svc_rc = _stop_clean_start(svc)
        if svc_rc != 0:
            rc = 1
    if rc:
        print(
            "\nERROR: One or more services failed to restart. "
            "Check logs:"
            f"\n  Service logs: python3 ./examples/dev_services.py logs -f"
            f"\n  Supervisord log: {SUPERVISORD_LOGFILE}"
        )
    return rc


# ── Main ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="dev_services.py",
        description="Dev Services - manages r2c-client "
        "via system-level supervisord",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    p_start = sub.add_parser(
        "start", help="Deploy configs and start services"
    )
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop all services")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show service status")
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="View logs")
    p_logs.add_argument(
        "service",
        nargs="?",
        default=None,
        help="Service name (r2c-client)",
    )
    p_logs.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output",
    )
    p_logs.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of lines to show (default: 50)",
    )
    p_logs.set_defaults(func=cmd_logs)

    p_restart = sub.add_parser("restart", help="Restart a service")
    p_restart.add_argument(
        "service",
        nargs="?",
        default=None,
        help="Service name (r2c-client)",
    )
    p_restart.set_defaults(func=cmd_restart)

    p_set_log_level = sub.add_parser(
        "set_log_level",
        help=f"Set log level ({', '.join(sorted(m.value for m in LogLevel))})",
    )
    p_set_log_level.add_argument(
        "level",
        type=str,
        help=f"Log level: {', '.join(sorted(m.value for m in LogLevel))}",
    )
    p_set_log_level.set_defaults(func=cmd_set_log_level)

    p_get_log_level = sub.add_parser(
        "get_log_level", help="Show current log level"
    )
    p_get_log_level.set_defaults(func=cmd_get_log_level)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

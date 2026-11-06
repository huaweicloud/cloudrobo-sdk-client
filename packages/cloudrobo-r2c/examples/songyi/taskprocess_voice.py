#!/usr/bin/env python3
"""任务失败语音播报器。

订阅 ``{project_id}/{device_id}/taskprocess``，遍历 nodes 内的
``result_state``：任一节点为 3（失败）时播放「任务失败.mp3」；
持续失败期间不重复播放；所有 result_state 均不为 3 时复位告警状态
（此后再次失败可再次播报）。

- 音频文件：``--audio``（缺省本包 ``assets/任务失败.mp3``）
- 播放器：``--player`` 或自动探测（mpv / ffplay / mpg123 / cvlc / paplay）
- 播放在后台线程执行；上一段未播完时忽略新触发（不叠音）
- 连接方式同其他联调示例

示例：
    python examples/songyi/taskprocess_voice.py \
        --endpoints tcp/127.0.0.1:7447
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

from cloudrobo_r2c import ClientConfig, R2CClient
from cloudrobo_r2c.common.models import TaskProcessMessage

logger = logging.getLogger("songyi_taskprocess_voice")

DEFAULT_AUDIO = Path(__file__).resolve().parent / "任务失败.mp3"

# 自动探测的本地播放器（依次取第一个可执行者）
_PLAYERS: List[List[str]] = [
    ["mpv", "--really-quiet", "--no-video"],
    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
    ["mpg123", "-q"],
    ["cvlc", "--play-and-exit", "--quiet"],
    ["paplay"],
]

# 任务节点执行结果状态：3 = 失败
RESULT_STATE_FAILED = 3

_playing_lock = threading.Lock()
_is_playing = False  # 播放状态标志；与 _playing_lock 配合做原子置位/复位


def parse_endpoints(raw: Optional[str]) -> List[str]:
    return [ep.strip() for ep in (raw or "").split(",") if ep.strip()]


def pick_player(forced: str) -> Optional[List[str]]:
    """选择播放器命令：--player 指定或按 _PLAYERS 顺序探测。"""
    if forced and forced != "auto":
        if shutil.which(forced):
            return [forced]
        logger.error("指定的播放器 %r 不存在（PATH 中未找到）", forced)
        return None
    for cmd in _PLAYERS:
        if shutil.which(cmd[0]):
            return cmd
    return None


def play_audio(audio_path: Path, player_cmd: List[str]) -> None:
    """后台播放音频；播放中重复触发忽略（原子置位 _is_playing）。"""
    global _is_playing
    if not audio_path.exists():
        logger.error("音频文件不存在: %s", audio_path)
        return
    with _playing_lock:
        if _is_playing:
            logger.info("上一段语音仍在播放，忽略本次触发")
            return
        _is_playing = True

    def _run() -> None:
        try:
            logger.info("playing: %s (via %s)", audio_path.name, player_cmd[0])
            try:
                subprocess.run(
                    player_cmd + [str(audio_path)],
                    check=False,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                logger.warning("语音播放超时（>120s），已强制终止进程 (%s)", player_cmd[0])
            except Exception:
                logger.exception("语音播放失败 (%s)", player_cmd[0])
            logger.info("playback finished")
        finally:
            with _playing_lock:
                _is_playing = False

    threading.Thread(target=_run, daemon=True, name="task-fail-voice").start()


def any_failed(message: TaskProcessMessage) -> bool:
    """nodes 内任一 result_state == 3 即失败；nodes 为空视为无失败。"""
    return any(
        int(getattr(node, "result_state", 0) or 0) == RESULT_STATE_FAILED
        for node in (getattr(message, "nodes", None) or [])
    )


def build_client(args: argparse.Namespace):
    """连接方式同其他联调示例：--bundle 或显式参数。"""
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id or None)

    if not args.project_id:
        raise ValueError("project_id is required when --bundle is not provided")
    if not args.device_id:
        raise ValueError("device_id is required when --bundle is not provided")

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "songyi-taskprocess-voice",
        endpoints=parse_endpoints(args.endpoints),
        endpoint_role=args.endpoint_role,
        mode=args.mode,
    )
    logger.info(
        "Connecting: client_id=%s project=%s device=%s mode=%s endpoints=%s",
        config.client_id, config.project_id, config.device_id, config.mode,
        list(config.endpoints),
    )
    return R2CClient.connect(config)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="taskprocess 节点失败（result_state=3）触发播放任务失败语音"
    )
    parser.add_argument("--bundle", type=str, default=None,
                        help="Platform credential bundle (optional)")
    parser.add_argument("--project-id", type=str, default="r2c",
                        help="Project ID (required without --bundle)")
    parser.add_argument("--device-id", type=str, default="local",
                        help="Device ID (required without --bundle)")
    parser.add_argument("--client-id", type=str,
                        default="songyi-taskprocess-voice",
                        help="Client ID (default: songyi-taskprocess-voice)")
    parser.add_argument("--endpoints", type=str, default="tcp/127.0.0.1:7447",
                        help="Comma-separated endpoints")
    parser.add_argument("--endpoint-role", type=str, default="connect",
                        choices=["connect", "listen"])
    parser.add_argument("--mode", type=str, default="client",
                        choices=["peer", "client"])
    parser.add_argument("--audio", type=str, default=str(DEFAULT_AUDIO),
                        help="音频文件路径（缺省 examples/songyi/assets/任务失败.mp3）")
    parser.add_argument("--player", type=str, default="auto",
                        help="播放器命令（缺省 auto：mpv/ffplay/mpg123/"
                             "cvlc/paplay 自动探测）")
    parser.add_argument("--timeout", type=float, default=0.0,
                        help="Stop after N seconds (0 = run until Ctrl+C)")

    args = parser.parse_args()
    if args.timeout < 0:
        raise ValueError("--timeout must be >= 0")

    audio_path = Path(args.audio)
    player_cmd = pick_player(args.player)
    if player_cmd is None:
        logger.error(
            "未找到可用的音频播放器（尝试安装 mpv / ffmpeg / mpg123 / vlc 任一）"
            "；将仅打印触发日志"
        )

    topic = f"{args.project_id}/{args.device_id}/job/task"
    logger.info("subscribing %s | audio=%s player=%s",
                topic, audio_path, player_cmd or "(none)")

    client = build_client(args)

    # 告警状态机：失败触发一次播报；所有 result_state 不为 3 时复位
    alerted = {"flag": False}

    def _handler(payload: bytes) -> None:
        try:
            msg = TaskProcessMessage.from_protobuf(bytes(payload))
        except Exception:
            logger.warning("taskprocess 解码失败（%d bytes）", len(payload))
            return
        failed = any_failed(msg)
        if failed:
            if alerted["flag"]:
                logger.debug("任务失败告警已激活；忽略重复帧")
                return
            alerted["flag"] = True
            logger.info("检测到任务失败（nodes 含 result_state=3）→ 播放任务失败语音")
            if player_cmd is not None:
                play_audio(audio_path, player_cmd)
        else:
            if alerted["flag"]:
                logger.info("所有节点 result_state 均不为 3；复位任务失败告警")
            alerted["flag"] = False

    client.transport.subscribe(topic, _handler)
    logger.info("Waiting for taskprocess frames (Ctrl+C to stop)...")

    start = time.monotonic()
    try:
        while True:
            if args.timeout > 0 and time.monotonic() - start >= args.timeout:
                logger.info("Timeout reached, stopping.")
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        client.close()
        logger.info("taskprocess voice stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()

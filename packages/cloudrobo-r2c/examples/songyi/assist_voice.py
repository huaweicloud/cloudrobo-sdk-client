#!/usr/bin/env python3
"""ASSIST_ENTER 语音播报器（现场协助提醒）。

订阅 ``{project_id}/{device_id}/command``，收到 ``command_type=ASSIST_ENTER``
（大小写不敏感，归一化）时播放现场协助语音（辅助接管指令）。

- 音频文件：``--audio``（缺省本包 ``assets/现场协助语音.mp3``）
- 播放器：``--player`` 或自动探测（mpv / ffplay / mpg123 / cvlc / paplay）
- 播放在后台线程执行；上一段未播完时重复触发忽略（不叠音）
- 其余 command_type 忽略；连接方式同其他联调示例

示例：
    python examples/songyi/assist_voice.py
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
from cloudrobo_r2c.common.models import CommandMessage

logger = logging.getLogger("songyi_assist_voice")

DEFAULT_AUDIO = Path(__file__).resolve().parent / "现场协助语音.mp3"

# 自动探测的本地播放器（依次取第一个可执行者）
_PLAYERS: List[List[str]] = [
    ["mpv", "--really-quiet", "--no-video"],
    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
    ["mpg123", "-q"],
    ["cvlc", "--play-and-exit", "--quiet"],
    ["paplay"],
]

_playing_lock = threading.Lock()


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
    """后台播放音频；播放中重复触发忽略（_playing_lock 非阻塞获取）。"""
    if not _playing_lock.acquire(blocking=False):
        logger.info("上一段语音仍在播放，忽略本次触发")
        return
    if not audio_path.exists():
        logger.error("音频文件不存在: %s", audio_path)
        _playing_lock.release()
        return

    def _run() -> None:
        try:
            logger.info("playing: %s (via %s)", audio_path.name, player_cmd[0])
            try:
                subprocess.run(
                    player_cmd + [str(audio_path)],
                    check=False,
                    timeout=120,
                )
            except Exception:
                logger.exception("语音播放失败 (%s)", player_cmd[0])
            logger.info("playback finished")
        finally:
            _playing_lock.release()

    threading.Thread(target=_run, daemon=True, name="assist-voice").start()


def build_client(args: argparse.Namespace):
    """连接方式同其他联调示例：--bundle 或显式参数。"""
    if args.bundle:
        logger.info("Connecting with platform credential bundle: %s", args.bundle)
        return R2CClient.connect(args.bundle, client_id=args.client_id or None)

    config = ClientConfig(
        project_id=args.project_id,
        device_id=args.device_id,
        client_id=args.client_id or "songyi-assist-voice",
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
        description="ASSIST_ENTER 指令触发播放现场协助语音"
    )
    parser.add_argument("--bundle", type=str, default=None,
                        help="Platform credential bundle (optional)")
    parser.add_argument("--project-id", type=str, default="r2c",
                        help="Project ID (required without --bundle)")
    parser.add_argument("--device-id", type=str, default="local",
                        help="Device ID (required without --bundle)")
    parser.add_argument("--client-id", type=str,
                        default="songyi-assist-voice",
                        help="Client ID (default: songyi-assist-voice)")
    parser.add_argument("--endpoints", type=str, default="tcp/127.0.0.1:7447",
                        help="Comma-separated endpoints")
    parser.add_argument("--endpoint-role", type=str, default="connect",
                        choices=["connect", "listen"])
    parser.add_argument("--mode", type=str, default="client",
                        choices=["peer", "client"])
    parser.add_argument("--audio", type=str, default=str(DEFAULT_AUDIO),
                        help="音频文件路径（缺省 examples/songyi/assets/现场协助语音.mp3）")
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

    # 先建连，再用返回 session 的真实身份构建 topic：bundle 模式下
    # project_id/device_id 由凭证解析，避免 /command 退化成 //command
    client = build_client(args)
    topic = f"{client.project_id}/{client.device_id}/job/command"
    logger.info("subscribing %s | audio=%s player=%s",
                topic, audio_path, player_cmd or "(none)")

    def _handler(payload: bytes) -> None:
        try:
            msg = CommandMessage.from_protobuf(bytes(payload))
        except Exception:
            logger.warning("command 解码失败（%d bytes）", len(payload))
            return
        command_type = str(
            getattr(msg, "command_type", "") or ""
        ).strip().lower()
        if command_type != "assist_enter":
            return
        logger.info("收到 ASSIST_ENTER → 播放现场协助语音")
        if player_cmd is not None:
            play_audio(audio_path, player_cmd)

    client.transport.subscribe(topic, _handler)
    logger.info("Waiting for ASSIST_ENTER (Ctrl+C to stop)...")

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
        logger.info("assist voice stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    main()

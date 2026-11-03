#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""独立 mock：信号驱动的 TaskProcess 模拟器（直连 zenoh，不依赖 bridge/agent）.

== 它做什么 ==

模拟器自身按真实节奏推进一次抓取任务的时间线（5 节点：开始/预定位/抓取商品/
转身/放置商品，步骤文案与 ``gui/humanize``/``agent/task_process`` 同款），向
``{pid}/{did}/job/task`` 发布 TaskProcess 快照（protobuf 编码，与
``bridge_main`` 逐字段一致）；同时旁路订阅 ``{pid}/{did}/job/command``
（同款 ``command_pb2.Command``），**实时响应人工介入信号**。command_type
大小写不敏感（``TELEOP_ENTER``/``teleop_enter`` 均可，判断前统一转小写）。

== 信号 → 响应对照 ==

=============== ==========================================================
command_type    响应
=============== ==========================================================
teleop_enter    "遥操作执行中（抓取「商品」）"：活跃节点 result_state=4，
                任务时间线冻结，心跳保持 4；若此前有失败标记(3)则一并清除
teleop_exit     "遥操作结束"：活跃节点回 1 → 被打断/被救的步骤以
                "xx已完成（遥操完成）"收尾 → 时间线继续；被遥操救过的节点
                最终完成态 = 5（通过摇操完成，粘滞），其余节点仍 2
assist_enter    "工单处理中"：活跃节点 6（更强的介入，覆盖进行中的遥操）
assist_exit     "工单结束"：**最后一条**消息——活跃节点保持 6，其后所有
                节点置 8（置灰/不再执行），整单终止，无任务级终态帧
=============== ==========================================================

正常跑完全部步骤后发"任务完成"终态帧（全节点 2/5）。

== 场景开关 ==

- 缺省：正常成功剧本。
- ``--fail-at N``：节点 N(1-based) 的 VLA 步判失败（3，"VLA 任务超时未判定
  成功"）后**一直心跳等待**遥操/工单信号，不自行结束——云端可借此联调
  失败态(3) → 遥操(4) → 恢复(1) → … → 完成(5) 的完整序列。
- ``--retries K``：失败先自动重跑 K 次（节点 retry_count 实时递增 0→1→…，
  重试期间节点保持 3），耗尽才进入等待——联调重试计数可见性。
- ``--teleop/--assist`` 无需预声明：信号随时可发，运行中直接接管同样生效。

== 时间形状（对齐真实上报） ==

各节点有自己的 start_timestamp；已完成节点 duration 冻结；活跃/等待/遥操/
工单期间 duration 随墙钟增长；长步与等待期按 ``--interval`` 心跳重发当前
状态；首条消息携带编译 think（think_duration≈2.8s）；
``显示动作起点 + action_duration == action_timestamp`` 恒成立；
同一消息流内 action_timestamp 严格递增。

session_id 每轮随机（``mock-{uuid4[:8]}``），``--loop`` 每轮新 session_id。

== 典型用法 ==

1) 双终端 zenoh 联调（需宿主机 jiuwen 环境，含 zenoh + cloudrobo_r2c）::

       # 终端 1：跑模拟器（失败后等待信号）
       python examples/mock_taskprocess_pub.py --fail-at 3 --retries 2

       # 终端 2：按需发信号（大写小写均可），或由真遥操设备直接发同 topic
       python examples/mock_taskprocess_pub.py --send-command teleop_enter
       python examples/mock_taskprocess_pub.py --send-command TELEOP_EXIT
       python examples/mock_taskprocess_pub.py --send-command assist_enter
       python examples/mock_taskprocess_pub.py --send-command assist_exit

   期望消息序列：1(进行中)…→3(失败，重试期 retry_count 递增)→4(遥操)→
   1(遥操作结束)→"已完成（遥操完成）"→…→5(节点完成)→"任务完成"；
   工单场景：…→6(工单处理中)→7(工单结束+其后 8 置灰，到此为止)。

2) 无 zenoh 环境时本地联调（--dry-run：快照打 stdout，信号从 stdin 逐行
   敲，同样大小写均可）::

       python examples/mock_taskprocess_pub.py --dry-run --fail-at 3
       # 另起进程向其 stdin 写：teleop_enter⏎ … teleop_exit⏎

3) 回放历史抓拍（无信号交互，时间戳按发送时刻实算；旧路径保留）::

       python examples/mock_taskprocess_pub.py --file example.jsonl

== 其他参数 ==

``--project-id``/``--device-id``（必填，拼 topic 与 source）、
``--endpoint``（zenoh 端点，默认本机 7447）、``--mode peer|client``、
``--topic``、``--item``（抓取商品名，叙述文案与缺省 session_name 用）、
``--session-name``、``--interval``（心跳节奏秒）、``--loop``。

依赖：stdlib + 宿主机 jiuwen 环境的 ``zenoh`` 与 ``cloudrobo_r2c``（懒导入；
本仓 venv 没有时用 ``--dry-run``）。不 import 本仓任何代码；单元测试直接
驱动 ``MockEngine``（注入时钟，确定性）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, "src")

from cloudrobo_r2c import ClientConfig, R2CClient

# result_state enum（外部 proto）：0=未开始 1=进行中 2=完成 3=失败
# 4=遥操执行中 5=通过摇操完成 6=工单处理中 7=工单结束 8=置灰（未执行）
STATE_NOT_STARTED = 0
STATE_RUNNING = 1
STATE_DONE = 2
STATE_FAILED = 3
STATE_TELEOP_RUNNING = 4
STATE_TELEOP_DONE = 5
STATE_ASSIST_RUNNING = 6
STATE_ASSIST_DONE = 7
STATE_ASSIST_GREY = 8

_DOING = "正在执行中"
_DONE = "已完成"
_FAIL_REASON = "VLA 任务超时未判定成功（run_s 兜底耗尽）"
_TELEOP_DOING = "遥操作执行中"
_TELEOP_DONE = "遥操作结束"
_ASSIST_DOING = "工单处理中"
_ASSIST_DONE = "工单结束"
_TERMINAL_OK = "任务完成"

COMMAND_TYPES = ("teleop_enter", "teleop_exit", "assist_enter", "assist_exit")

# 节点 → 步骤（label, 时长秒, 是否 VLA 长步）；label 与 humanize/真实上报同款。
# 同名相邻步骤折叠为一组连续"正在执行中"（与真实 reporter 折叠规则一致）。
_THINK_DURATION_MS = 2800


def _plan(item: str) -> list[tuple[str, list[tuple[str, float, bool]]]]:
    grasp = f"抓取「{item}」"
    return [
        ("开始", [("回到初始位置", 1.5, False), ("张开夹爪", 0.8, False)]),
        ("预定位", [("转动腰部到90度", 2.0, False)]),
        ("抓取商品", [("移动手臂", 1.2, False), (grasp, 12.0, True), ("移动手臂", 1.2, False)]),
        ("转身", [("转动腰部到0度", 2.0, False)]),
        ("放置商品", [("移动手臂", 1.2, False), ("张开夹爪", 0.8, False)]),
    ]


class MockEngine:
    """有状态任务时间线：``poll(now)`` 到点产帧，``on_command`` 四信号切状态.

    纯逻辑、无 IO：``now`` 由调用方注入（毫秒），可完全确定性地驱动（测试用
    ``time_scale`` 缩短步长）。帧 = TaskProcess snapshot dict（与 bridge 发布
    的形状逐字段一致）。状态机::

        running ──步失败──▶ waiting ──teleop_enter──▶ teleop ──teleop_exit──▶ running
           ▲                  │心跳等待信号                │（被救步"（遥操完成）"续跑）
           └──────────────────┴── assist_enter ──▶ assist ──assist_exit──▶ ended（7+置灰8）
        全部步完成 ──▶ done（"任务完成"终态帧）
    """

    def __init__(
        self,
        *,
        item: str,
        session_id: str,
        session_name: str,
        fail_at: int = 0,
        retries: int = 0,
        heartbeat_s: float = 1.0,
        time_scale: float = 1.0,
        start_ms: int | None = None,
    ) -> None:
        self.session_id = session_id
        self.session_name = session_name
        self.start_ms = start_ms if start_ms is not None else _now_ms()
        self._hb_ms = max(int(heartbeat_s * 1000), 1)
        self._now = self.start_ms
        self._finished: str | None = None
        self._state = "running"  # running|waiting|teleop|assist
        # 展开扁平步骤
        self._steps: list[dict[str, Any]] = []
        self._node_names: list[str] = []
        for node_name, step_defs in _plan(item):
            self._node_names.append(node_name)
            for label, dur_s, is_vla in step_defs:
                self._steps.append(
                    {
                        "node": len(self._node_names) - 1,
                        "label": label,
                        "dur_ms": max(int(dur_s * 1000 * time_scale), 1),
                        "vla": is_vla,
                    }
                )
        self._nodes: list[dict[str, Any]] = [
            {
                "name": name,
                "start_ms": 0,
                "done": 0,
                "total": 0,
                "frozen_ms": 0,
                "teleop": False,
                "failed": False,
                "retry": 0,
            }
            for name in self._node_names
        ]
        for s in self._steps:
            self._nodes[s["node"]]["total"] += 1
        self._fail_idx = self._find_fail_step(fail_at)
        self._retries_left = max(int(retries), 0)
        # 运行态
        self._i = -1
        self._next_due: int | None = None
        self._display_start = 0
        self._display_label = ""
        self._last_text = ""
        self._hb_due = self.start_ms + self._hb_ms
        self._think = (self.start_ms + 30, _THINK_DURATION_MS)
        self._lock = threading.Lock()
        self._out: list[dict] = []
        self._begin_step(0, self.start_ms)

    # ------------------------------------------------------------- 查询
    @property
    def finished(self) -> str | None:
        """None=进行中；"done"=任务完成；"ended"=工单终止。"""
        return self._finished

    def pending(self) -> bool:
        return bool(self._out)

    # ------------------------------------------------------------- 信号
    def on_command(self, cmd: str) -> None:
        """处理一条 command_type（teleop/assist enter/exit）；未知信号忽略。"""
        with self._lock:
            cmd = (cmd or "").strip().lower()
            if self._finished or cmd not in COMMAND_TYPES:
                return
            now = self._now
            if cmd == "teleop_enter":
                if self._state == "teleop":
                    return
                nd = self._nodes[self._steps[self._i]["node"]]
                nd["teleop"] = True  # 粘滞 5
                nd["failed"] = False  # 人工补救取代自主失败标记（接管即清，同真实收集器）
                self._next_due = None  # 挂起当前步：遥操期间任务时间线冻结
                self._display_start = now
                self._state = "teleop"
                deco = f"（{self._display_label}）" if self._display_label.startswith("抓取「") else ""
                self._emit(f"{_TELEOP_DOING}{deco}", now)
            elif cmd == "teleop_exit":
                if self._state != "teleop":
                    return
                self._state = "running"
                self._emit(_TELEOP_DONE, now)  # 结束帧不覆盖节点状态：活跃节点回 1
                self._complete_step(now, note="遥操完成")  # 被打断/被救步骤按遥操成功收尾
                self._advance(now)
            elif cmd == "assist_enter":
                if self._state == "assist":
                    return
                self._display_start = now
                self._state = "assist"  # 覆盖进行中的遥操（遥操按结束处理）
                self._emit(_ASSIST_DOING, now)
            elif cmd == "assist_exit":
                if self._state != "assist":
                    return
                self._finished = "ended"
                self._emit(_ASSIST_DONE, now)  # 最后一条：活跃 6、其后 8、无终态帧

    # ------------------------------------------------------------- 推进
    def poll(self, now_ms: int) -> list[dict]:
        """推进到 ``now_ms``，返回到点应发布的帧（含心跳）；终态后仅排空余帧。"""
        with self._lock:
            out: list[dict] = list(self._out)
            self._out = []
            if self._finished:
                return out
            now = max(int(now_ms), self._now)
            self._now = now
            while not self._finished:
                due, which = self._next_event()
                if due is None or due > now:
                    break
                if which == "step":
                    self._on_step_due(due)
                else:
                    self._heartbeat(due)
                out += self._out
                self._out = []
            return out

    def _next_event(self) -> tuple[int | None, str | None]:
        due: int | None = None
        which: str | None = None
        if self._next_due is not None:
            due, which = self._next_due, "step"
        if self._hb_due < (due if due is not None else self._hb_due + 1):
            due, which = self._hb_due, "hb"
        return due, which

    def _on_step_due(self, now: int) -> None:
        s = self._steps[self._i]
        if self._i != self._fail_idx:
            self._complete_step(now)
            self._advance(now)
            return
        nd = self._nodes[s["node"]]
        nd["failed"] = True
        self._emit(_FAIL_REASON, now)  # 失败上报（节点 3）
        if self._retries_left > 0:
            self._retries_left -= 1
            nd["retry"] += 1
            self._emit(_FAIL_REASON, now)  # bump_retry 心跳（retry_count 已 +1）
            self._next_due = now + s["dur_ms"]  # 重跑：同名折叠，不重发 doing
        else:
            self._state = "waiting"  # 心跳重发失败原因，直到遥操/工单信号
            self._next_due = None

    def _heartbeat(self, now: int) -> None:
        if self._last_text:
            self._emit(self._last_text, now)
        else:
            self._hb_due = now + self._hb_ms

    def _complete_step(self, now: int, note: str = "") -> None:
        s = self._steps[self._i]
        nd = self._nodes[s["node"]]
        nd["done"] += 1
        if nd["done"] >= nd["total"]:
            nd["frozen_ms"] = now  # 已完成节点 duration 冻结
        self._emit(f"{s['label']}{_DONE}（{note}）" if note else f"{s['label']}{_DONE}", now)

    def _advance(self, now: int) -> None:
        if self._i + 1 >= len(self._steps):
            self._finished = "done"
            self._next_due = None  # 终态后不再有步事件（防 poll 重复终态帧）
            self._emit(_TERMINAL_OK, now, since=self.start_ms)  # 终态按任务整长口径
        else:
            self._begin_step(self._i + 1, now)

    def _begin_step(self, i: int, now: int) -> None:
        self._i = i
        s = self._steps[i]
        nd = self._nodes[s["node"]]
        if nd["start_ms"] == 0:
            nd["start_ms"] = now
        self._next_due = now + s["dur_ms"]
        if s["label"] != self._display_label:  # 同名相邻折叠：不重发 doing
            self._display_label = s["label"]
            self._display_start = now
            self._emit(f"{s['label']}{_DOING}", now)

    # ------------------------------------------------------------- 建帧
    def _emit(self, text: str, now: int, since: int | None = None) -> None:
        self._last_text = text
        dur = max(now - (self._display_start if since is None else since), 0)
        self._out.append(self._build(text, now, dur))
        self._hb_due = now + self._hb_ms

    def _build(self, text: str, now: int, dur: int) -> dict:
        active = self._steps[self._i]["node"] if 0 <= self._i < len(self._steps) else len(self._nodes) - 1
        nodes_out = [
            self._node_payload(j, nd, active, now)
            for j, nd in enumerate(self._nodes)
        ]
        think_ts, think_dur = self._think
        self._think = (0, 0)
        return {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "timestamp": self.start_ms,
            "nodes": nodes_out,
            "current_step": {
                "action_timestamp": now,
                "task_id": f"node_{active + 1}",
                "action_str": text,
                "action_duration": dur,
                "think_timestamp": think_ts,
                "think_duration": think_dur,
            },
        }

    def _node_payload(self, j: int, nd: dict[str, Any], active: int, now: int) -> dict:
        start, duration = self._node_timing(nd, now)
        return {
            "task_id": f"node_{j + 1}",
            "task_name": nd["name"],
            "progress": round(nd["done"] / nd["total"], 4) if nd["total"] else 0.0,
            "result_state": self._node_state(nd, j, active),
            "retry_count": nd["retry"],
            "start_timestamp": start,
            "duration": duration,
        }

    def _node_state(self, nd: dict[str, Any], j: int, active: int) -> int:
        if self._finished == "ended":
            return (
                STATE_ASSIST_RUNNING
                if j == active
                else (STATE_ASSIST_GREY if j > active else self._done_state(nd))
            )
        if self._finished == "done":
            return self._done_state(nd)
        if j < active:
            return self._done_state(nd)
        if j == active:
            if self._state == "teleop":
                return STATE_TELEOP_RUNNING
            if self._state == "assist":
                return STATE_ASSIST_RUNNING
            if nd["failed"]:  # 失败/重试期保持 3，成功（含遥操补救）才清除
                return STATE_FAILED
            return STATE_RUNNING
        return STATE_NOT_STARTED

    def _node_timing(self, nd: dict[str, Any], now: int) -> tuple:
        if nd["start_ms"] == 0:
            return 0, 0
        if nd["frozen_ms"]:
            return nd["start_ms"], nd["frozen_ms"] - nd["start_ms"]
        return nd["start_ms"], now - nd["start_ms"]

    def _done_state(self, nd: dict[str, Any]) -> int:
        return STATE_TELEOP_DONE if nd["teleop"] else STATE_DONE

    def _find_fail_step(self, fail_at: int) -> int:
        if not fail_at:
            return -1
        idxs = [i for i, s in enumerate(self._steps) if s["node"] == fail_at - 1]
        if not idxs:
            raise ValueError(f"--fail-at {fail_at} 超出节点范围 1..{len(self._node_names)}")
        vla = [i for i in idxs if self._steps[i]["vla"]]
        return (vla or idxs)[-1]


def _now_ms() -> int:
    return int(time.time() * 1000)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="独立 mock：信号驱动发布 TaskProcess 快照（响应遥操/工单 command）")
    conn = parser.add_mutually_exclusive_group()
    conn.add_argument("--bundle", default=None, help="platform credential bundle (.zip/dir)")
    conn.add_argument("--client-config", default=None, help="client config YAML")
    parser.add_argument("--project-id", default="r2c", help="Project ID（topic 与 source 拼接用）")
    parser.add_argument("--device-id", default="local", help="Device ID（topic 与 source 拼接用）")
    parser.add_argument("--endpoint", default="tcp/127.0.0.1:7447", help="zenoh 端点（单端点简写，--endpoints 优先）")
    parser.add_argument("--endpoints", default=None, help="逗号分隔多端点（优先于 --endpoint）")
    parser.add_argument("--mode", default="client", choices=["peer", "client"], help="zenoh 会话模式")
    parser.add_argument("--endpoint-role", default="connect", choices=["connect", "listen"], help="zenoh 端点角色")
    parser.add_argument("--client-id", default="mock-taskprocess", help="client ID")
    parser.add_argument("--topic", default=None,
                        help="TaskProcess 发布 topic（缺省 {project_id}/{device_id}/job/task）")
    parser.add_argument("--item", default="乐事薯片", help="抓取商品名（叙述文案与缺省 session_name 用）")
    parser.add_argument("--session-name", default=None, help="快照 session_name（缺省 {item}订单）")
    parser.add_argument("--interval", type=float, default=1.0, help="心跳节奏秒（长步/等待期按此重发当前状态）")
    parser.add_argument(
        "--fail-at",
        type=int,
        default=0,
        metavar="N",
        help="内置时间线：节点 N(1-based) 的 VLA 步判失败（3）后心跳等待遥操/工单信号",
    )
    parser.add_argument(
        "--retries", type=int, default=0, metavar="K", help="失败先自动重跑 K 次（retry_count 递增），耗尽才等待"
    )
    parser.add_argument(
        "--send-command",
        default=None,
        metavar="CMD",
        help=f"发一条 command 后退出，大小写均可（{('|').join(COMMAND_TYPES)}）",
    )
    parser.add_argument(
        "--file", default=None, help="回放指定 jsonl（沿用其节点/文案结构；时间戳发送时实算，无信号交互）"
    )
    parser.add_argument("--loop", action="store_true", help="跑完一轮歇 2s 再来一轮（session_id 每轮新随机）")
    parser.add_argument("--dry-run", action="store_true", help="不连 zenoh，快照打 stdout；信号从 stdin 逐行读")
    return parser.parse_args(argv)


# ------------------------------------------------------------------ 引擎驱动


def _drive(engine: MockEngine, on_frame: Callable[[dict], None], stop: threading.Event) -> None:
    """实时推进引擎直到终态/停止：到点帧交给 ``on_frame`` 发布。"""
    while True:
        for frame in engine.poll(_now_ms()):
            on_frame(frame)
        if engine.finished:
            return
        if stop.wait(0.05):
            return


def _log_line(counter: list[int], snapshot: dict[str, Any], topic: str) -> None:
    counter[0] += 1
    nodes = snapshot.get("nodes", [])
    mark = next(
        (n for n in nodes if n.get("result_state") in (STATE_FAILED, STATE_TELEOP_RUNNING, STATE_ASSIST_RUNNING)), None
    )
    if mark is None:
        mark = next((n for n in nodes if n.get("result_state") == STATE_RUNNING), None)
    if mark is None:
        done = [n for n in nodes if n.get("result_state") in (STATE_DONE, STATE_TELEOP_DONE)]
        mark = done[-1] if done else (nodes[0] if nodes else {})
    step = (snapshot.get("current_step") or {}).get("action_str", "-")
    print(
        f"[mock-tp] #{counter[0]} session={snapshot['session_id']} "
        f"{mark.get('task_name')}({mark.get('result_state')},{mark.get('progress')}) "
        f'step="{step}" -> {topic}',
        flush=True,
    )


# ------------------------------------------------------------------ zenoh / dry-run


def _parse_endpoints(args: argparse.Namespace) -> list[str]:
    """Resolve endpoints from --endpoints (comma-separated) or --endpoint."""
    raw = args.endpoints or args.endpoint
    return [ep.strip() for ep in raw.split(",") if ep.strip()] if raw else []


def build_client(args: argparse.Namespace):
    """Build R2CSession from --bundle / --client-config / explicit params."""
    if args.bundle:
        return R2CClient.connect(args.bundle, client_id=args.client_id)
    if args.client_config:
        config = ClientConfig.from_yaml(
            args.client_config,
            project_id=args.project_id,
            device_id=args.device_id,
            client_id=args.client_id,
            endpoints=_parse_endpoints(args) or None,
            mode=args.mode,
        )
    else:
        config = ClientConfig(
            project_id=args.project_id,
            device_id=args.device_id,
            client_id=args.client_id,
            endpoints=_parse_endpoints(args),
            endpoint_role=args.endpoint_role,
            mode=args.mode,
        )
    return R2CClient.connect(config)


def run_live(args: argparse.Namespace, engine: MockEngine) -> int:
    try:
        from cloudrobo_r2c.common.models.generated import command_pb2, task_process_pb2
    except ImportError as exc:  # pragma: no cover - 宿主机 jiuwen 环境才有
        print(f"[error] 无法导入 cloudrobo_r2c：{exc}。请激活 jiuwen 环境（或用 --dry-run）。", file=sys.stderr)
        return 2

    client = build_client(args)
    stop = threading.Event()
    counter = [0]

    def _on_command(payload: bytes) -> None:
        try:
            command = command_pb2.Command.FromString(payload)
            engine.on_command(str(command.command_type or ""))
        except Exception as exc:  # noqa: BLE001 - 单帧坏不应打断模拟
            print(f"[mock-tp] command 帧解析失败（跳过）: {exc}", flush=True)

    def on_frame(frame: dict) -> None:
        pb = task_process_pb2.TaskProcess()
        fill_taskprocess(pb, frame, source=f"{args.project_id}/{args.device_id}")
        client.transport.publish(args.topic, pb.SerializeToString())
        _log_line(counter, frame, args.topic)

    try:
        command_topic = f"{args.project_id}/{args.device_id}/job/command"
        client.transport.subscribe(command_topic, _on_command)
        print(f"[mock-tp] taskprocess -> {args.topic}，command <- {command_topic}", flush=True)
        _drive(engine, on_frame, stop)
    except KeyboardInterrupt:
        print("[mock-tp] stopping", flush=True)
    finally:
        stop.set()
        client.close()
    return 0


def run_dryrun(args: argparse.Namespace, engine: MockEngine) -> int:
    stop = threading.Event()
    counter = [0]

    def on_frame(frame: dict) -> None:
        print(json.dumps(frame, ensure_ascii=False), flush=True)
        _log_line(counter, frame, "(stdout)")

    def _read_stdin() -> None:
        try:
            for line in sys.stdin:
                cmd = line.strip().lower()
                if cmd:
                    print(f"[mock-tp] <- {cmd}", flush=True)
                    engine.on_command(cmd)
        except (ValueError, OSError):  # stdin 关闭（pytest/管道）静默退出
            return

    threading.Thread(target=_read_stdin, daemon=True).start()
    try:
        _drive(engine, on_frame, stop)
    except KeyboardInterrupt:
        print("[mock-tp] stopping", flush=True)
    finally:
        stop.set()
    return 0


def send_command(args: argparse.Namespace) -> int:
    cmd = (args.send_command or "").strip().lower()
    if cmd not in COMMAND_TYPES:
        print(f"[error] 未知 command {cmd!r}；可选：{COMMAND_TYPES}", file=sys.stderr)
        return 2
    try:
        from cloudrobo_r2c.common.models.generated import command_pb2
    except ImportError as exc:  # pragma: no cover - 宿主机 jiuwen 环境才有
        print(f"[error] 无法导入 cloudrobo_r2c：{exc}。请激活 jiuwen 环境。", file=sys.stderr)
        return 2
    client = build_client(args)
    try:
        pb = command_pb2.Command()
        pb.command_type = cmd
        topic = f"{args.project_id}/{args.device_id}/job/command"
        client.transport.publish(topic, pb.SerializeToString())
        print(f"[mock-tp] {cmd} -> {topic}", flush=True)
    finally:
        client.close()
    return 0


# ------------------------------------------------------------------ 旧回放路径（--file）


def replay_snapshots(path: str, session_name: str) -> list[dict]:
    """回放 jsonl 抓拍：仅沿用节点/文案结构（session_id 每轮随机、时间戳发送时实算）。"""
    snaps: list[dict] = []
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError) as e:
        print(f"[error] 无法读取文件 {path!r}: {e}", file=sys.stderr)
        return snaps
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            snap = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"[error] JSON 解析失败: {e}", file=sys.stderr)
            continue
        snap["session_name"] = session_name
        snaps.append(snap)
    return snaps


def _restamp(snapshot: dict[str, Any], start_ms: int) -> dict[str, Any]:
    """回放路径：按发送时刻实算全部时间字段（来源文件里的旧值一律不用）。"""
    now = _now_ms()
    snapshot["timestamp"] = start_ms
    for node in snapshot.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if node.get("start_timestamp"):
            node["start_timestamp"] = start_ms
            node["duration"] = now - start_ms
    step = snapshot.get("current_step")
    if isinstance(step, dict) and step:
        step["action_timestamp"] = now
        step["action_duration"] = now - start_ms
    return snapshot


def run_replay(args: argparse.Namespace) -> int:
    session_name = args.session_name or f"{args.item}订单"
    snapshots = replay_snapshots(args.file, session_name)
    if not snapshots:
        print(f"[error] 没有可发布的快照（--file {args.file!r} 为空？）", file=sys.stderr)
        return 2
    session_id = f"mock-{uuid4().hex[:8]}"
    for snap in snapshots:
        snap["session_id"] = session_id
    start_ms = _now_ms()
    counter = [0]
    if args.dry_run:  # 不连 zenoh：只打 stdout
        for i, snapshot in enumerate(snapshots, start=1):
            snapshot = _restamp(snapshot, start_ms)
            print(json.dumps(snapshot, ensure_ascii=False), flush=True)
            _log_line(counter, snapshot, "(stdout)")
            if i < len(snapshots):
                time.sleep(max(args.interval, 0.0))
        return 0
    try:
        from cloudrobo_r2c.common.models.generated import task_process_pb2
    except ImportError as exc:  # pragma: no cover - 宿主机 jiuwen 环境才有
        print(f"[error] 无法导入 cloudrobo_r2c：{exc}。请激活 jiuwen 环境。", file=sys.stderr)
        return 2
    client = build_client(args)
    try:
        for i, snapshot in enumerate(snapshots, start=1):
            snapshot = _restamp(snapshot, start_ms)
            pb = task_process_pb2.TaskProcess()
            fill_taskprocess(pb, snapshot, source=f"{args.project_id}/{args.device_id}")
            client.transport.publish(args.topic, pb.SerializeToString())
            _log_line(counter, snapshot, args.topic)
            if i < len(snapshots):
                time.sleep(max(args.interval, 0.0))
    except KeyboardInterrupt:
        print("[mock-tp] stopping", flush=True)
    finally:
        client.close()
    return 0


def _v_str(data: dict, key: str, default: str = "") -> str:
    """Coerce ``data[key] or default`` to ``str`` (or-empty idiom)."""
    return str(data.get(key) or default)


def _v_int(data: dict, key: str, default: int = 0) -> int:
    """Coerce ``data[key] or default`` to ``int`` (or-empty idiom)."""
    return int(data.get(key) or default)


def _v_float(data: dict, key: str, default: float = 0.0) -> float:
    """Coerce ``data[key] or default`` to ``float`` (or-empty idiom)."""
    return float(data.get(key) or default)


def _is_snapshot_valid(snapshot: Any) -> bool:
    """True when *snapshot* is a dict with a non-empty ``session_id``."""
    if not isinstance(snapshot, dict):
        return False
    return bool(str(snapshot.get("session_id") or "").strip())


def fill_taskprocess(pb: Any, snapshot: dict[str, Any], *, source: str = "") -> None:
    """snapshot dict → TaskProcess protobuf（与 bridge_main.fill_taskprocess 同映射）。"""
    if not _is_snapshot_valid(snapshot):
        raise ValueError("taskprocess snapshot 必须是含非空 session_id 的 dict")
    pb.source = source
    pb.session_id = str(snapshot["session_id"])
    pb.session_name = _v_str(snapshot, "session_name")
    pb.timestamp = _v_int(snapshot, "timestamp")
    for node in snapshot.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        entry = pb.nodes.add()
        entry.task_id = _v_str(node, "task_id")
        entry.task_name = _v_str(node, "task_name")
        entry.progress = _v_float(node, "progress")
        entry.result_state = _v_int(node, "result_state")
        entry.retry_count = _v_int(node, "retry_count")
        entry.start_timestamp = _v_int(node, "start_timestamp")
        entry.duration = _v_int(node, "duration")
    step = snapshot.get("current_step")
    if isinstance(step, dict):
        cs = pb.current_step
        cs.action_timestamp = _v_int(step, "action_timestamp")
        cs.task_id = _v_str(step, "task_id")
        cs.action_str = _v_str(step, "action_str")
        cs.action_duration = _v_int(step, "action_duration")
        cs.think_timestamp = _v_int(step, "think_timestamp")
        cs.think_duration = _v_int(step, "think_duration")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.topic:
        # 通用化（收编批 1）：topic 不再硬编码默认值，按身份展开（云侧 job/task）
        args.topic = f"{args.project_id}/{args.device_id}/job/task"
    if args.send_command:
        return send_command(args)
    if args.file:
        return run_replay(args)
    while True:
        session_id = f"mock-{uuid4().hex[:8]}"  # 每轮随机，不写死
        try:
            engine = MockEngine(
                item=args.item,
                session_id=session_id,
                session_name=args.session_name or f"{args.item}订单",
                fail_at=args.fail_at,
                retries=args.retries,
                heartbeat_s=args.interval,
            )
            code = run_dryrun(args, engine) if args.dry_run else run_live(args, engine)
        except ValueError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2
        if code != 0 or not args.loop:
            return code
        time.sleep(2.0)


if __name__ == "__main__":
    sys.exit(main())
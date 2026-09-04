from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Union

from cloudrobo_r2c.client.session import R2CSession
from cloudrobo_r2c.common.models import Actions, Observations
from cloudrobo_r2c.common.utils.keyboard_controller import KeyboardController
from cloudrobo_r2c.common.utils.logging_sanitizer import (
    fmt_size,
    summarize_observation_for_log,
)
from cloudrobo_r2c.common.utils.recorder import ObservationRecorder
from cloudrobo_r2c.core.fusion import (
    FusionEngine,
    ScheduledActionStep,
    _QueueJointSnapshot,
)
from cloudrobo_r2c.core.interfaces import (
    IDeviceTranslator,
    IRobotHardwareAdapter,
)
from cloudrobo_r2c.robots.robot_factory import RobotFactory
from cloudrobo_r2c.translators.translator_factory import DeviceTranslatorFactory

logger = logging.getLogger(__name__)

_RESERVED_KEYBOARD_COMMANDS = {"pause_resume", "graceful_stop"}
_RESERVED_KEYBOARD_KEYS = {" ", "q"}  # must match keyboard_mapper.RESERVED_KEYS


def _validate_keymap_commands(keymap: dict, adapter: Any) -> None:
    """Validate that every command name in *keymap* references a
    registered command instance or is a reserved lifecycle command.

    Raises :class:`ValueError` on unknown command names.
    """
    registered = set(getattr(adapter, "_adapter_commands", {}).keys())
    for key, cmd_name in sorted(keymap.items()):
        if not cmd_name:  # empty string = disabled
            continue
        if cmd_name in _RESERVED_KEYBOARD_COMMANDS:
            continue
        if cmd_name not in registered:
            available = sorted(registered) if registered else ["(none)"]
            raise ValueError(
                f"keyboard_control.keymap[{key!r}] references command "
                f"{cmd_name!r}, but no such command is registered.\n"
                f"  Available commands in this adapter: {available}\n"
                f"  Reserved commands (always available): "
                f"{sorted(_RESERVED_KEYBOARD_COMMANDS)}\n"
                f"  Fix: either register a command named {cmd_name!r} "
                f"in your adapter, or change the keymap value to one of "
                f"the available commands."
            )


def _validate_keyboard_config(cfg: Mapping[str, Any]) -> None:
    """Validate ``runtime.keyboard_control`` section at startup.

    Raises :class:`ValueError` on any configuration error with a
    detailed message explaining what is wrong and how to fix it.
    """
    if not cfg:
        return

    # ── enabled ─────────────────────────────────────────────────
    enabled = cfg.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(
            f"keyboard_control.enabled must be true or false, "
            f"got {enabled!r} (type: {type(enabled).__name__}).\n"
            f"  Fix: set enabled: true or enabled: false in YAML."
        )

    keymap = cfg.get("keymap")
    if keymap is None:
        return

    # ── keymap type ─────────────────────────────────────────────
    if not isinstance(keymap, dict):
        raise ValueError(
            f"keyboard_control.keymap must be a mapping of key → command-name, "
            f"got {type(keymap).__name__!r}.\n"
            f"  Fix: use a YAML dict, e.g. keymap: {{\"x\": \"go_home\"}}"
        )

    # ── key/value types ─────────────────────────────────────────
    for key, value in sorted(keymap.items()):
        if not isinstance(key, str) or not key:
            raise ValueError(
                f"keyboard_control.keymap: every key must be a non-empty string "
                f"(the key character to press), got {key!r} "
                f"(type: {type(key).__name__}).\n"
                f"  Fix: use a string key like \"x\" or \"h\"."
            )
        if not isinstance(value, str):
            raise ValueError(
                f"keyboard_control.keymap[{key!r}]: value must be a string "
                f"(the command name or \"\" to disable), "
                f"got {value!r} (type: {type(value).__name__})."
            )

    # ── reserved key / command checks ────────────────────────────
    from cloudrobo_r2c.common.utils.keyboard_mapper import DEFAULT_KEYMAP

    for key, value in sorted(keymap.items()):
        default_cmd = DEFAULT_KEYMAP.get(key)

        # Reserved KEY: cannot be remapped or disabled (silently ignored by mapper)
        if key in _RESERVED_KEYBOARD_KEYS:
            raise ValueError(
                f"keyboard_control.keymap[{key!r}] = {value!r}: "
                f"key {key!r} is reserved ({default_cmd!r}) and "
                f"cannot be remapped or disabled.\n"
                f"  Reserved keys: {sorted(_RESERVED_KEYBOARD_KEYS)}.\n"
                f"  Use other keys for custom mappings."
            )

        # Disabling a key that maps to a reserved command
        # (only possible for non-reserved keys that happen to map to reserved commands)
        if value == "" and default_cmd in _RESERVED_KEYBOARD_COMMANDS:
            raise ValueError(
                f"keyboard_control.keymap[{key!r}] = \"\": "
                f"cannot disable the reserved command {default_cmd!r}.\n"
                f"  Reserved commands (cannot be disabled): "
                f"{sorted(_RESERVED_KEYBOARD_COMMANDS)}.\n"
                f"  You may remap this key to another key, e.g.: "
                f"keymap: {{\"p\": \"{default_cmd}\"}}."
            )


ActionStepPayload = Dict[str, Any]


def _try_get_id(obj: Any) -> Optional[int]:
    """Best-effort extract an integer id from an observation-like value."""
    if isinstance(obj, Mapping):
        raw = obj.get("id")
        if isinstance(raw, int):
            return raw
        return None
    if hasattr(obj, "id"):
        raw = getattr(obj, "id", None)
        if isinstance(raw, int):
            return raw
    return None


def _estimate_raw_size(obj: Any) -> int:
    """Approximate the total byte size of a nested observation structure.

    Counts the ``nbytes`` of numpy arrays and the ``len`` of bytes-like
    values.  Scalars are ignored.  Deeply nested dicts and lists are
    traversed recursively.
    """
    import numpy as np

    total = 0
    if isinstance(obj, Mapping):
        for v in obj.values():
            total += _estimate_raw_size(v)
    elif isinstance(obj, (list, tuple)) and not isinstance(obj, (str, bytes)):
        for item in obj:
            total += _estimate_raw_size(item)
    elif isinstance(obj, np.ndarray):
        total += obj.nbytes
    elif isinstance(obj, (bytes, bytearray, memoryview)):
        total += len(obj)
    return total


@dataclass
class SyncRobotClient:
    """Threading-based robot edge client for the Observations/Actions flow.

    Responsibilities:
    - Pull raw device observations from ``IRobotHardwareAdapter``
    - Translate and publish observations through ``R2CSession``
    - Subscribe cloud inference ``Actions`` from ``R2CSession``
    - Translate actions into device-native commands and drive hardware
    """

    def __init__(
        self,
        session: R2CSession,
        hardware_adapter: IRobotHardwareAdapter,
        translator: IDeviceTranslator,
        target_fps: int = 30,
        max_action_backlog: int = 1000,
        action_response_timeout_s: float = 1.0,
        action_response_timeout_initial_s: float = 3.0,
        action_response_timeout_backoff: float = 2.0,
        max_enqueue_actions_per_chunk: int = -1,
        enable_action_chunk_alignment: bool = False,
        keyboard_control_enabled: bool = False,
        keyboard_keymap: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
        recorder: Optional[ObservationRecorder] = None,
        publish_trigger_threshold: int = 0,
        fusion_strategy: str = "replace",
        fusion_window_size: int = 10,
        skip_initial_observations: int = 1,
        state_types: Optional[List[str]] = None,
        state_type_order: Optional[Dict[str, str]] = None,
    ) -> None:
        if target_fps <= 0:
            raise ValueError("target_fps must be > 0")
        if max_action_backlog <= 0:
            raise ValueError("max_action_backlog must be > 0")
        if action_response_timeout_s <= 0:
            raise ValueError("action_response_timeout_s must be > 0")
        if action_response_timeout_initial_s <= 0:
            raise ValueError("action_response_timeout_initial_s must be > 0")
        if action_response_timeout_backoff < 1.0:
            raise ValueError("action_response_timeout_backoff must be >= 1.0")
        if publish_trigger_threshold < 0:
            raise ValueError("publish_trigger_threshold must be >= 0")
        if skip_initial_observations < 0:
            raise ValueError("skip_initial_observations must be >= 0")
        self._fusion_engine = FusionEngine(
            state_types=state_types,
            state_type_order=state_type_order,
            strategy=fusion_strategy,
            window_size=fusion_window_size,
        )

        self.session = session
        self.hardware_adapter = hardware_adapter
        self.translator = translator
        self.environment_dt = 1.0 / target_fps
        self.max_action_backlog = max_action_backlog
        self.action_response_timeout_s = float(action_response_timeout_s)
        self.action_response_timeout_initial_s = float(action_response_timeout_initial_s)
        self.action_response_timeout_backoff = float(action_response_timeout_backoff)
        self._consecutive_timeouts: int = 0
        self.max_enqueue_actions_per_chunk = int(max_enqueue_actions_per_chunk)
        self.enable_action_chunk_alignment = bool(enable_action_chunk_alignment)
        self.keyboard_control_enabled = bool(keyboard_control_enabled)
        self.dry_run = bool(dry_run)
        self._keyboard_controller: Optional[KeyboardController] = None
        self._keyboard_keymap = keyboard_keymap

        self._running = threading.Event()
        self._stop_requested = threading.Event()
        self._control_thread: Optional[threading.Thread] = None
        self._action_queue: "queue.Queue[ScheduledActionStep]" = queue.Queue(
            maxsize=max_action_backlog
        )
        self._awaiting_action = threading.Event()
        self._paused = threading.Event()
        # Set while fusion is draining+blending+re-enqueuing so the
        # control loop does not observe a briefly empty queue and
        # publish a premature observation.
        self._fusion_in_progress = threading.Event()
        # Guards `_actions_consumed_since_publish`, `_consecutive_timeouts`,
        # `_latest_observation_id`, and `_awaiting_action` toggle, which are
        # read/written from both the subscriber callback thread
        # (_enqueue_action_chunk) and the control loop thread
        # (_apply_one_action_step / _publish_latest_observation).
        #
        # `_last_executed_chunk_action` is intentionally lock-free: it is a
        # Python list ref assignment which is atomic under the GIL.  Stale
        # reads are acceptable: the worst case is aligning to a slightly
        # outdated position.
        self._action_state_lock = threading.Lock()

        self._recorder = recorder

        self.publish_trigger_threshold = int(publish_trigger_threshold)
        self.skip_initial_observations = int(skip_initial_observations)
        self._skipped_initial_observations: int = 0
        self.latest_action_timestep = -1
        self.latest_observation: Optional[Union[Observations, Mapping[str, Any]]] = None
        self._last_observation_publish_monotonic: Optional[float] = None
        self._last_executed_chunk_action: Optional[List[float]] = None

        self._actions_consumed_since_publish: int = 0
        self._latest_observation_id: int = 0

    @property
    def _async_enabled(self) -> bool:
        """``True`` when async request with preemptive publishing is enabled.

        Derived from ``publish_trigger_threshold``, which ``from_config``
        forces to 0 when ``async_request.enabled`` is false.
        """
        return self.publish_trigger_threshold > 0

    @classmethod
    def from_config(
        cls,
        *,
        session: R2CSession,
        robot_config: Mapping[str, Any],
        hardware_class: Optional[str] = None,
        translator_class: Optional[str] = None,
        max_action_backlog: int = 1000,
        recorder: Optional[ObservationRecorder] = None,
    ) -> "SyncRobotClient":
        """Create client from edge config while allowing custom class overrides."""
        mutable_robot_config = dict(robot_config)

        if hardware_class:
            hardware_section = mutable_robot_config.get("hardware")
            merged_hardware = (
                dict(hardware_section) if isinstance(hardware_section, Mapping) else {}
            )
            merged_hardware["type"] = "custom"
            merged_hardware["class_path"] = hardware_class
            mutable_robot_config["hardware"] = merged_hardware

        hardware_adapter = RobotFactory.create_hardware_adapter(mutable_robot_config)
        translator = DeviceTranslatorFactory.create_device_translator(
            mutable_robot_config,
            translator_class=translator_class,
        )
        runtime_cfg = robot_config.get("runtime", {})
        if not isinstance(runtime_cfg, Mapping):
            runtime_cfg = {}
        target_fps = int(runtime_cfg.get("publish_hz", 30))
        action_response_timeout_s = float(
            runtime_cfg.get("action_response_timeout_s", 1.0)
        )
        action_response_timeout_initial_s = float(
            runtime_cfg.get("action_response_timeout_initial_s", 3.0)
        )
        action_response_timeout_backoff = float(
            runtime_cfg.get("action_response_timeout_backoff", 2.0)
        )
        max_action_backlog = int(
            runtime_cfg.get("max_action_backlog", max_action_backlog)
        )
        max_enqueue_actions_per_chunk = int(
            runtime_cfg.get("max_enqueue_actions_per_chunk", -1)
        )
        enable_action_chunk_alignment = bool(
            runtime_cfg.get("enable_action_chunk_alignment", False)
        )
        keyboard_cfg = runtime_cfg.get("keyboard_control", {})
        if not isinstance(keyboard_cfg, Mapping):
            keyboard_cfg = {}
        keyboard_control_enabled = bool(keyboard_cfg.get("enabled", False))
        _validate_keyboard_config(keyboard_cfg)
        # ── async_request config section ──────────────────────────────
        async_req_cfg_raw = runtime_cfg.get("async_request")
        async_req_cfg: Mapping[str, Any] = (
            async_req_cfg_raw if isinstance(async_req_cfg_raw, Mapping) else {}
        )
        async_request_enabled = bool(async_req_cfg.get("enabled", False))

        fusion_cfg_raw = async_req_cfg.get("fusion")
        fusion_cfg: Mapping[str, Any] = (
            fusion_cfg_raw if isinstance(fusion_cfg_raw, Mapping) else {}
        )

        if async_request_enabled:
            publish_trigger_threshold = int(
                async_req_cfg.get("publish_trigger_threshold", 0)
            )
            fusion_strategy = str(fusion_cfg.get("strategy", "replace"))
            fusion_window_size = int(fusion_cfg.get("window_size", 10))
            raw_state_types = fusion_cfg.get("state_types")
            state_types = (
                [str(s) for s in raw_state_types]
                if isinstance(raw_state_types, list)
                else None
            )
            raw_order = fusion_cfg.get("state_type_order", {})
            state_type_order = (
                {str(k): str(v) for k, v in raw_order.items()}
                if isinstance(raw_order, Mapping)
                else {}
            )
        else:
            publish_trigger_threshold = 0
            fusion_strategy = "replace"
            fusion_window_size = 10
            state_types = None
            state_type_order = {}
        # ── end async_request ─────────────────────────────────────────
        skip_initial_observations = int(runtime_cfg.get("skip_initial_observations", 1))
        dry_run = bool(runtime_cfg.get("dry_run", False))
        raw_keymap = keyboard_cfg.get("keymap")
        keyboard_keymap = (
            {str(k): str(v) for k, v in raw_keymap.items()}
            if isinstance(raw_keymap, dict)
            else None
        )
        if keyboard_control_enabled and keyboard_keymap:
            _validate_keymap_commands(keyboard_keymap, hardware_adapter)
        return cls(
            session=session,
            hardware_adapter=hardware_adapter,
            translator=translator,
            target_fps=target_fps,
            max_action_backlog=max_action_backlog,
            action_response_timeout_s=action_response_timeout_s,
            action_response_timeout_initial_s=action_response_timeout_initial_s,
            action_response_timeout_backoff=action_response_timeout_backoff,
            max_enqueue_actions_per_chunk=max_enqueue_actions_per_chunk,
            enable_action_chunk_alignment=enable_action_chunk_alignment,
            keyboard_control_enabled=keyboard_control_enabled,
            keyboard_keymap=keyboard_keymap,
            dry_run=dry_run,
            recorder=recorder,
            publish_trigger_threshold=publish_trigger_threshold,
            fusion_strategy=fusion_strategy,
            fusion_window_size=fusion_window_size,
            skip_initial_observations=skip_initial_observations,
            state_types=state_types,
            state_type_order=state_type_order,
        )

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def _log_config_summary(self) -> None:
        """Log a summary of key runtime configuration at startup."""
        lines = [
            "── R2C SyncRobotClient Config ──────────────────────────────",
            f"  Loop rate:              {1.0 / self.environment_dt:.0f} Hz "
            f"(dt={self.environment_dt:.3f}s)",
            f"  Dry run:                {self.dry_run}",
            f"  Skip initial obs:       {self.skip_initial_observations}",
            f"  Keyboard control:       {self.keyboard_control_enabled}",
            f"  Action queue backlog:   {self.max_action_backlog}",
            f"  Chunk alignment:        {self.enable_action_chunk_alignment}",
            f"  Max enqueue per chunk:  {self.max_enqueue_actions_per_chunk}",
            "── Timeout ──────────────────────────────────────────────────",
            f"  Max timeout:            {self.action_response_timeout_s:.1f}s",
            f"  Initial timeout:        {self.action_response_timeout_initial_s:.1f}s",
            f"  Backoff multiplier:     {self.action_response_timeout_backoff:.1f}x",
            "── Async Request ───────────────────────────────────────────",
            f"  Enabled:                {self._async_enabled}",
            f"  Publish trigger:        {self.publish_trigger_threshold}",
            "── Fusion ──────────────────────────────────────────────────",
            f"  Strategy:               {self._fusion_engine.strategy}",
            f"  Window size:            {self._fusion_engine.window_size}",
            f"  Is active:              {self._fusion_engine.is_active}",
        ]
        if self._fusion_engine.state_types:
            lines.append(
                f"  State types:            {self._fusion_engine.state_types}")
        if self._fusion_engine.state_type_order:
            lines.append(
                f"  State type order:       {self._fusion_engine.state_type_order}")
        lines.append(
            "────────────────────────────────────────────────────────────"
        )
        for line in lines:
            logger.info(line)

    def start(self) -> None:
        if self.running:
            logger.warning("SyncRobotClient already running")
            return

        self._log_config_summary()

        self._stop_requested.clear()
        self._running.set()
        self._subscribe_actions()
        if self.keyboard_control_enabled:
            self._keyboard_controller = KeyboardController(
                adapter=self.hardware_adapter,
                keymap=self._keyboard_keymap,
                on_pause_resume=self._toggle_pause_and_reset_flow,
                on_graceful_stop=self._request_graceful_stop,
                is_paused=lambda: self._paused.is_set(),
            )
            self._keyboard_controller.start()
        self._control_thread = threading.Thread(
            target=self._control_loop,
            name="r2c-sync-control-loop",
            daemon=True,
        )
        self._control_thread.start()
        logger.info("SyncRobotClient started")

    def stop(self, timeout: float = 2.0) -> None:
        if not self.running:
            if self._recorder is not None:
                self._recorder.close()
                self._recorder = None
            return

        # Signal the control / keyboard threads to stop FIRST, then join them,
        # so they are no longer touching the recorder when we close it below.
        # Closing the recorder while the control loop is mid-record() would
        # race and corrupt the recording.
        self._stop_requested.set()
        self._running.clear()

        if self._control_thread and self._control_thread.is_alive():
            self._control_thread.join(timeout=timeout)

        self._control_thread = None

        if self._keyboard_controller is not None:
            self._keyboard_controller.stop()
            self._keyboard_controller = None

        # Now that the control thread is stopped, it is safe to close the
        # recorder (no concurrent record() calls can be in flight).
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

        self._drain_action_queue()
        logger.info("SyncRobotClient stopped")

    def _subscribe_actions(self) -> None:
        def _on_actions(actions: Actions) -> None:
            try:
                logger.debug("actions: %s", actions)
                self._enqueue_action_chunk(actions)
            except Exception as exc:  # defensive callback boundary
                logger.exception("Failed to enqueue action chunk: %s", exc)

        self.session.subscribe_actions(_on_actions)

    def _enqueue_action_chunk(self, actions: Actions) -> None:
        if self._paused.is_set():
            logger.debug("Dropping incoming action chunk because flow is paused.")
            return

        # Stale response check is deferred to the lock-protected enqueue
        # section below so it sees a consistent view of _latest_observation_id
        # with respect to _publish_latest_observation.
        action_id = int(actions.id)

        # Gate the control-loop publish check for the entire async
        # enqueue window.  Clearing _awaiting_action signals "actions
        # received" and allows the control loop to publish a new
        # observation whenever the queue runs below the threshold.
        # But the queue is briefly drained during fusion (and steps are
        # pushed back one-by-one), so we defer the signal until after
        # all steps have been enqueued.
        if self._async_enabled:
            self._fusion_in_progress.set()

        # Capture consumed count before clearing the awaiting flag.
        # Both operations must be atomic w.r.t. the control loop's
        # counter reset/publish to avoid a lost-update race.
        with self._action_state_lock:
            consumed_since_publish = self._actions_consumed_since_publish
        self._reset_timeout_backoff()
        chunk_size = max(int(actions.chunk_size or 1), 1)
        align_start_index = self._align_actions_to_last_executed(actions, chunk_size)
        aligned_chunk_size = max(chunk_size - align_start_index, 0)
        steps_to_enqueue = aligned_chunk_size
        if self.max_enqueue_actions_per_chunk >= 0:
            steps_to_enqueue = min(steps_to_enqueue, self.max_enqueue_actions_per_chunk)
            if aligned_chunk_size > steps_to_enqueue:
                logger.debug(
                    "Dropping %d action steps from chunk due to "
                    "max_enqueue_actions_per_chunk=%d",
                    aligned_chunk_size - steps_to_enqueue,
                    self.max_enqueue_actions_per_chunk,
                )
        base_timestep = self._resolve_action_base_timestep(actions)
        new_steps = self._build_new_steps(
            actions, align_start_index, steps_to_enqueue, base_timestep
        )

        logger.debug(
            "Action chunk received: id=%d chunk_size=%d enqueue=%d "
            "align_start=%d consumed_since_publish=%d async=%s queue_depth=%d",
            action_id,
            chunk_size,
            steps_to_enqueue,
            align_start_index,
            consumed_since_publish,
            self._async_enabled,
            self._action_queue.qsize(),
        )

        if self._async_enabled:
            new_steps = self._apply_n_skip(new_steps, consumed_since_publish)
            with self._action_state_lock:
                if self._paused.is_set():
                    logger.debug(
                        "Dropping incoming action chunk because flow is paused."
                    )
                    self._awaiting_action.clear()
                elif self._is_stale_action(action_id):
                    self._awaiting_action.clear()
                else:
                    self._enqueue_with_fusion(new_steps)
                    self._awaiting_action.clear()
            self._fusion_in_progress.clear()
        else:
            with self._action_state_lock:
                if self._paused.is_set():
                    logger.debug(
                        "Dropping incoming action chunk because flow is paused."
                    )
                    self._awaiting_action.clear()
                elif self._is_stale_action(action_id):
                    self._awaiting_action.clear()
                else:
                    for step in new_steps:
                        self._push_action_step(step)
                    self._awaiting_action.clear()

    def _align_actions_to_last_executed(self, actions: Actions, chunk_size: int) -> int:
        if not self.enable_action_chunk_alignment or chunk_size <= 0:
            return 0
        if self._last_executed_chunk_action is None:
            logger.debug(
                "Chunk alignment is enabled but no previously executed chunk is "
                "available; accepting all actions in the current chunk."
            )
            return 0

        positions = actions.joint_states.position
        if not isinstance(positions, list) or not positions:
            return 0
        if not isinstance(positions[0], list):
            return 0

        max_index = min(len(positions), chunk_size)
        if max_index <= 0:  # pragma: no cover
            return 0

        last_joint = self._last_executed_chunk_action[:6]
        best_index = 0
        nearest_distance = float("inf")

        for idx in range(max_index):
            candidate = positions[idx]
            if not isinstance(candidate, list):
                continue
            compare_dims = min(len(last_joint), len(candidate), 6)
            if compare_dims <= 0:
                continue
            distance = math.sqrt(
                sum(
                    float(last_joint[j] - candidate[j]) ** 2
                    for j in range(compare_dims)
                )
            )
            if distance < nearest_distance:
                nearest_distance = distance
                best_index = idx

        if nearest_distance == float("inf"):
            return 0

        if best_index > 0:
            logger.info(
                "Aligned new chunk to last executed action: discarding %d leading "
                "actions, nearest_joint_distance=%.6f rad, last_executed=%s, "
                "aligned_action=%s",
                best_index,
                nearest_distance,
                last_joint,
                positions[best_index][:6],
            )
        else:
            logger.info(
                "Aligned new chunk to last executed action without dropping "
                "leading actions: nearest_joint_distance=%.6f rad",
                nearest_distance,
            )

        return best_index

    @staticmethod
    def _resolve_action_base_timestep(actions: Actions) -> int:
        """Resolve action step timeline base.

        Prefer ``Actions.id`` because cloud services may echo the source
        ``Observations.id`` as timestep. Fallback to ``timestamp`` for backward
        compatibility with legacy publishers.
        """
        if int(actions.id) > 0:
            return int(actions.id)
        return int(actions.timestamp)

    def _is_stale_action(self, action_id: int) -> bool:
        """Return ``True`` if *action_id* does not match the latest observation.

        Must be called while holding ``_action_state_lock`` to ensure a
        consistent read of ``_latest_observation_id``.

        A negative ``_latest_observation_id`` acts as a sentinel set on
        resume-from-pause: all actions are discarded until the first
        post-resume observation is published.
        """
        latest_id = self._latest_observation_id
        if latest_id < 0:
            logger.debug(
                "Discarding action chunk (id=%d); "
                "waiting for first observation after resume.",
                action_id,
            )
            return True
        if latest_id > 0 and action_id > 0 and action_id != latest_id:
            logger.info(
                "Discarding stale action chunk (id=%d, latest_observation_id=%d). "
                "A new observation has been published since this action was requested.",
                action_id,
                latest_id,
            )
            return True
        return False

    def _extract_action_step(self, actions: Actions, index: int) -> ActionStepPayload:
        def _select_step(values: Any) -> Any:
            if not isinstance(values, list):
                return values
            if not values:
                return []
            if isinstance(values[0], list):
                if index < len(values):
                    return values[index]
                return values[-1]
            return values

        end_effector_poses = {
            name: {"pose": _select_step(chunk.pose)}
            for name, chunk in actions.end_effector_poses.items()
        }

        return {
            "timestamp": actions.timestamp,
            "id": actions.id,
            "chunk_index": index,
            "joint_states": {
                "names": list(actions.joint_states.names),
                "position": _select_step(actions.joint_states.position),
                "velocity": _select_step(actions.joint_states.velocity),
                "torque": _select_step(actions.joint_states.torque),
            },
            "end_effector_poses": end_effector_poses,
            "end_effector_states": {
                "names": list(actions.end_effector_states.names),
                "position": _select_step(actions.end_effector_states.position),
                "velocity": _select_step(actions.end_effector_states.velocity),
                "torque": _select_step(actions.end_effector_states.torque),
            },
            "localization": {
                "odom_pose": _select_step(actions.localization.odom_pose),
                "map_pose": _select_step(actions.localization.map_pose),
            },
            # chunk 级共享给每个 step（Actions.extensions 是整块一份，见协议语义）
            "extensions": dict(actions.extensions),
        }

    def _build_new_steps(
        self,
        actions: Actions,
        align_start_index: int,
        steps_to_enqueue: int,
        base_timestep: int,
    ) -> List[ScheduledActionStep]:
        """Build a list of ``ScheduledActionStep`` from an incoming chunk.

        Pure data transformation; does not touch the queue or runtime state.
        """
        new_steps: List[ScheduledActionStep] = []
        for index in range(steps_to_enqueue):
            source_index = align_start_index + index
            payload = self._extract_action_step(actions, source_index)
            step = ScheduledActionStep(
                timestep=max(base_timestep + source_index, 0),
                payload=payload,
            )
            new_steps.append(step)
        return new_steps

    def _apply_n_skip(
        self,
        new_steps: List[ScheduledActionStep],
        consumed_since_publish: int,
    ) -> List[ScheduledActionStep]:
        """Trim already-consumed leading actions from *new_steps*.

        Only meaningful when ``_async_enabled`` is ``True``, because
        observations may arrive before the previous chunk has been fully
        consumed.  The caller is expected to guard the call with
        ``_async_enabled``.
        """
        if consumed_since_publish <= 0:
            logger.debug(
                "N-skip: no actions consumed since publish, accepting all %d steps.",
                len(new_steps),
            )
            return new_steps

        if consumed_since_publish < len(new_steps):
            logger.info(
                "Skipping %d already-consumed actions from new chunk "
                "(chunk_size=%d, remaining=%d)",
                consumed_since_publish,
                len(new_steps),
                len(new_steps) - consumed_since_publish,
            )
            return new_steps[consumed_since_publish:]

        if new_steps:
            logger.warning(
                "Consumed more actions since publish (%d) than new "
                "chunk size (%d); dropping entire chunk.",
                consumed_since_publish,
                len(new_steps),
            )
        return []

    def _enqueue_with_fusion(self, new_steps: List[ScheduledActionStep]) -> None:
        """Push *new_steps* onto the action queue, fusing with residual
        queue steps when type-aware fusion is active.

        The caller is responsible for gating the control loop via
        ``_fusion_in_progress`` — this method only does fusion+push.
        """
        # ── resolve current robot position for nearest_neighbor ──────
        current_position = self._extract_current_position()

        if self._fusion_engine.is_active and not self._action_queue.empty():
            old_depth = self._action_queue.qsize()
            old_snapshot = self._drain_queue_joint_snapshot()
            fused_steps = self._fusion_engine.apply(
                old_snapshot, new_steps, current_position=current_position
            )
            logger.info(
                "Fusion applied: strategy=%s window=%d old=%d new=%d → fused=%d",
                self._fusion_engine.strategy,
                self._fusion_engine.window_size,
                old_depth,
                len(new_steps),
                len(fused_steps),
            )
            for step in fused_steps:
                self._push_action_step(step)
        else:
            logger.debug(
                "Fusion skipped (active=%s, queue_empty=%s) — appending %d steps.",
                self._fusion_engine.is_active,
                self._action_queue.empty(),
                len(new_steps),
            )
            for step in new_steps:
                self._push_action_step(step)

    def _push_action_step(self, step: ScheduledActionStep) -> None:
        while True:
            try:
                self._action_queue.put_nowait(step)
                return
            except queue.Full:
                try:
                    self._action_queue.get_nowait()
                except queue.Empty:
                    return

    def _extract_current_position(self) -> Optional[List[float]]:
        """Extract ``joint_states.position`` from the latest observation.

        Returns ``None`` when no observation has been captured yet or the
        observation does not contain joint positions.
        """
        obs = self.latest_observation
        if obs is None:
            return None

        # Observations dataclass
        joint_states = getattr(obs, "joint_states", None)
        if joint_states is not None:
            pos = getattr(joint_states, "position", None)
            if isinstance(pos, list):
                return pos

        # Plain Mapping (dict) fallback
        if isinstance(obs, Mapping):
            jss = obs.get("joint_states")
            if isinstance(jss, Mapping):
                pos = jss.get("position")
                if isinstance(pos, list):
                    return pos

        return None

    def _drain_action_queue(self) -> None:
        while not self._action_queue.empty():
            try:
                self._action_queue.get_nowait()
            except queue.Empty:
                return

    def _drain_queue_joint_snapshot(self) -> _QueueJointSnapshot:
        """Export all queued action steps as a ``_QueueJointSnapshot``.

        Drains every step currently in the queue so the caller can fuse
        them with an incoming chunk.
        """
        positions: List[List[float]] = []
        steps: List[ScheduledActionStep] = []
        while True:
            try:
                step = self._action_queue.get_nowait()
            except queue.Empty:
                break
            raw_pos = step.payload.get("joint_states", {}).get("position", [])
            if isinstance(raw_pos, list):
                positions.append(
                    [float(v) for v in raw_pos if isinstance(v, (int, float))]
                )
            else:
                positions.append([])
            steps.append(step)
        return _QueueJointSnapshot(positions=positions, steps=steps)

    def _control_loop(self) -> None:
        while self.running and not self._stop_requested.is_set():
            loop_start = time.perf_counter()

            # During fusion the old queue is drained, blended with the
            # new chunk, and re-enqueued.  Executing an action step
            # while this is in progress would consume a stale step
            # that is about to be replaced by the fused result.
            # Skip the entire tick — do not pop, do not check publish.
            if self._fusion_in_progress.is_set():
                elapsed = time.perf_counter() - loop_start
                sleep_duration = max(0.0, self.environment_dt - elapsed)
                if sleep_duration > 0:
                    time.sleep(sleep_duration)
                continue

            self._apply_one_action_step()
            if self._observation_publish_enabled():
                self._check_and_publish()

            elapsed = time.perf_counter() - loop_start
            sleep_duration = max(0.0, self.environment_dt - elapsed)
            if sleep_duration > 0:
                time.sleep(sleep_duration)

    def _check_and_publish(self) -> None:
        queue_depth = self._action_queue.qsize()
        if queue_depth <= self.publish_trigger_threshold:
            if not self._awaiting_action.is_set():
                if self._async_enabled:
                    logger.debug(
                        "Queue depth %d <= threshold %d — publishing new observation.",
                        queue_depth,
                        self.publish_trigger_threshold,
                    )
                self._publish_latest_observation()
            elif self._action_wait_timed_out():
                self._bump_timeout()
                logger.warning(
                    "Timed out waiting for actions (%.1fs/%.1fs), republishing observations.",
                    self._effective_timeout,
                    self.action_response_timeout_s,
                )
                self._publish_latest_observation()

    def _publish_latest_observation(self) -> None:
        if not self._observation_publish_enabled():
            return
        try:
            raw_observation = self.hardware_adapter.get_observation()
            raw_bytes = _estimate_raw_size(raw_observation)
            logger.debug(
                "Fetched raw observation (%s): %s",
                fmt_size(raw_bytes),
                summarize_observation_for_log(raw_observation),
            )
            observation = self.translator.device_to_r2c(raw_observation)
        except Exception as exc:
            logger.warning(
                "Failed to fetch observation in control loop; will retry next tick: %s",
                exc,
                exc_info=True,
            )
            return

        # The translator returns None when required source fields are not
        # ready (e.g. during startup, camera frames not yet available).
        # It already logs a single-line WARNING listing the missing fields,
        # so we just skip this tick without publishing.
        if observation is None:
            return

        if self._skipped_initial_observations < self.skip_initial_observations:
            self._skipped_initial_observations += 1
            logger.debug(
                "Skipping initial observation %d/%d",
                self._skipped_initial_observations,
                self.skip_initial_observations,
            )
            return

        try:
            if self._recorder is not None:
                self._recorder.record(raw_observation)
            logger.debug(
                "Publishing observation: %s",
                summarize_observation_for_log(observation),
            )
            self.latest_observation = observation
            self.session.publish_observations(observation)
        except Exception as exc:
            logger.warning(
                "Failed to publish observation in control loop; will retry next tick: %s",
                exc,
                exc_info=True,
            )
            return

        self._last_observation_publish_monotonic = time.perf_counter()
        obs_id = _try_get_id(observation)
        with self._action_state_lock:
            if obs_id is not None:
                self._latest_observation_id = obs_id
            self._awaiting_action.set()
            self._actions_consumed_since_publish = 0

    def _observation_publish_enabled(self) -> bool:
        return not self._paused.is_set()

    def _request_graceful_stop(self) -> None:
        self._stop_requested.set()
        self._running.clear()

    def _toggle_pause_and_reset_flow(self) -> None:
        if self._paused.is_set():
            with self._action_state_lock:
                self._paused.clear()
                self._latest_observation_id = -1
            logger.info(
                "Resumed via keyboard [space]: observation publishing and action execution enabled."
            )
            return

        with self._action_state_lock:
            self._paused.set()
            self._drain_action_queue()
            self._awaiting_action.clear()
        logger.info(
            "Paused via keyboard [space]: action execution stopped, action queue cleared, observation publishing paused."
        )

    def _action_wait_timed_out(self) -> bool:
        """Return ``True`` when the current wait exceeds the effective timeout.

        Uses incremental backoff: starts at *action_response_timeout_initial_s*,
        doubles (or multiplies by *action_response_timeout_backoff*) on each
        consecutive timeout, capped at *action_response_timeout_s*.
        """
        if not self._awaiting_action.is_set():
            return False
        if self._last_observation_publish_monotonic is None:
            return False
        wait_s = time.perf_counter() - self._last_observation_publish_monotonic
        return wait_s >= self._effective_timeout

    @property
    def _effective_timeout(self) -> float:
        """Current effective timeout with incremental backoff applied."""
        count = self._consecutive_timeouts  # int read is GIL-atomic
        if count == 0:
            return self.action_response_timeout_initial_s
        # Clamp count to the saturation point where backoff reaches
        # action_response_timeout_s.  Beyond that, increasing count does
        # not change the result but can overflow float (e.g. 2.0**1024).
        if self.action_response_timeout_backoff > 1.0:
            # initial * backoff**n >= max  ⇒  backoff**n >= max/initial
            ratio = self.action_response_timeout_s / self.action_response_timeout_initial_s
            if ratio <= 1.0:
                # initial >= max: already saturated at count == 1
                count = min(count, 1)
            else:
                saturate_at = int(
                    math.ceil(math.log(ratio) / math.log(self.action_response_timeout_backoff))
                )
                if saturate_at > 0:
                    count = min(count, saturate_at)
        backoff = self.action_response_timeout_initial_s * (
            self.action_response_timeout_backoff ** count
        )
        return min(backoff, self.action_response_timeout_s)

    def _bump_timeout(self) -> None:
        """Increment the consecutive timeout counter (called on each timeout).

        Only called from the control-loop thread, so no lock needed.
        """
        self._consecutive_timeouts += 1
        current = self._effective_timeout
        max_t = self.action_response_timeout_s
        if current >= max_t:
            logger.warning(
                "Action response timeout reached maximum (%.1fs after %d timeouts).",
                max_t,
                self._consecutive_timeouts,
            )
        else:
            logger.info(
                "Action response timeout bumped to %.1fs (timeout #%d, max=%.1fs).",
                current,
                self._consecutive_timeouts,
                max_t,
            )

    def _reset_timeout_backoff(self) -> None:
        """Reset the consecutive timeout counter (called when actions arrive).

        Called from the subscriber-callback thread.  The lock is needed
        because the control-loop thread may concurrently increment
        ``_consecutive_timeouts`` in ``_bump_timeout``.
        """
        with self._action_state_lock:
            if self._consecutive_timeouts > 0:
                logger.debug(
                    "Action arrived; resetting timeout backoff from %d "
                    "consecutive timeouts.",
                    self._consecutive_timeouts,
                )
            self._consecutive_timeouts = 0

    def _apply_one_action_step(self) -> bool:
        try:
            scheduled_action = self._action_queue.get_nowait()
        except queue.Empty:
            return False

        # Re-check after popping: _toggle_pause_and_reset_flow drains
        # the queue under lock, but it cannot drain a step we already
        # popped.  Bail out here so we never execute a step after the
        # user has requested pause.
        if self._paused.is_set():
            return False

        # Wrap translation + device send so a single malformed action or a
        # hardware send failure does not kill the control thread (which would
        # leave the client in a zombie state with the robot no longer moving
        # and no error surfaced). The offending step is logged and dropped;
        # the loop continues with the next step on the next tick.
        try:
            device_command = self.translator.r2c_to_device(scheduled_action.payload)
            if not isinstance(device_command, Mapping):
                raise TypeError(
                    "translator.r2c_to_device must return a mapping for "
                    "hardware_adapter.send_action"
                )
            if not self.dry_run:
                self.hardware_adapter.send_action(device_command)
        except Exception as exc:
            logger.error(
                "Failed to apply action step (timestep=%s); dropping step and "
                "continuing: %s",
                scheduled_action.timestep,
                exc,
                exc_info=True,
            )
            return False

        joint_position = scheduled_action.payload.get("joint_states", {}).get(
            "position"
        )
        if isinstance(joint_position, list):
            self._last_executed_chunk_action = [
                float(value)
                for value in joint_position
                if isinstance(value, (int, float))
            ]
        self.latest_action_timestep = scheduled_action.timestep
        with self._action_state_lock:
            self._actions_consumed_since_publish += 1
        return True
"""Type-aware action fusion engine for Euler/Quaternion SLERP interpolation.

Extracted from ``SyncRobotClient`` so fusion logic is testable in isolation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from cloudrobo_r2c.common.utils.rotation import (
    euler_to_quat,
    normalize_quat,
    quat_angular_distance,
    quat_to_euler,
    slerp,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_EULER_ORDER_MAP: Dict[str, List[int]] = {
    "rpy": [0, 1, 2],
    "ypr": [2, 0, 1],
    "ryp": [0, 2, 1],
    "pyr": [1, 2, 0],
}

_QUAT_ORDER_MAP: Dict[str, List[int]] = {
    "wxyz": [0, 1, 2, 3],
    "xyzw": [1, 2, 3, 0],
}

_VALID_STRATEGIES = frozenset({"replace", "weighted_average", "nearest_neighbor"})


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class StateType(Enum):
    """Physical semantics of each element in ``joint_states.position``.

    Absolute value types (use SLERP for orientations):
        ``joint_angle``  — absolute joint angle (rad)
        ``position_xyz`` — absolute end-effector position (m)
        ``euler``        — absolute end-effector orientation as RPY Euler angles (rad)
        ``quaternion``   — absolute end-effector orientation as quaternion

    Delta value types (use linear interpolation, NO SLERP):
        ``joint_delta``  — per-step joint angle increment (rad/step)
        ``euler_delta``  — per-step end-effector orientation increment as RPY (rad/step)
    """

    JOINT_ANGLE = "joint_angle"
    POSITION_XYZ = "position_xyz"
    EULER = "euler"
    QUATERNION = "quaternion"
    JOINT_DELTA = "joint_delta"
    EULER_DELTA = "euler_delta"


@dataclass
class _TypeGroup:
    """Contiguous run of position elements sharing the same ``StateType``."""

    start_index: int
    end_index: int  # exclusive
    state_type: StateType

    @property
    def size(self) -> int:
        return self.end_index - self.start_index


@dataclass
class ScheduledActionStep:
    """A single action step with a timestep and payload."""

    timestep: int
    payload: Dict[str, Any]


@dataclass
class _QueueJointSnapshot:
    """Snapshot of unconsumed action steps exported from the action queue."""

    positions: List[List[float]]
    steps: List[ScheduledActionStep]


# ---------------------------------------------------------------------------
# FusionEngine
# ---------------------------------------------------------------------------


class FusionEngine:
    """Blend consecutive action chunks with type-aware interpolation.

    Supports three strategies:

    - ``"replace"`` — new chunk replaces the old queue entirely (no-op).
    - ``"weighted_average"`` — cross-fade between old and new over a window.
    - ``"nearest_neighbor"`` — find the best-matching step in the new chunk.

    When *state_types* is configured, each contiguous group of elements
    is interpolated according to its physical semantics: linear for
    joint angles / positions / delta values, Euler-angle SLERP, and
    Quaternion SLERP.

    Usage::

        # Absolute joint + pose
        engine = FusionEngine(
            state_types=["joint_angle"] * 6 + ["euler"] * 3,
            state_type_order={"euler": "rpy"},
            strategy="weighted_average",
            window_size=10,
        )
        fused = engine.apply(old_snapshot, new_steps)

        # Delta joint + delta pose (linear interpolation, no SLERP)
        engine = FusionEngine(
            state_types=["joint_delta"] * 6 + ["euler_delta"] * 3,
            strategy="weighted_average",
            window_size=10,
        )
        fused = engine.apply(old_snapshot, new_steps)
    """

    def __init__(
        self,
        *,
        state_types: Optional[List[str]] = None,
        state_type_order: Optional[Dict[str, str]] = None,
        strategy: str = "replace",
        window_size: int = 10,
    ) -> None:
        _validate_fusion_config(state_types, state_type_order, strategy, window_size)

        self.strategy = strategy
        self.window_size = window_size
        self.state_types: Optional[List[str]] = (
            list(state_types) if state_types is not None else None
        )
        self.state_type_order: Dict[str, str] = (
            dict(state_type_order) if state_type_order is not None else {}
        )
        self._type_groups: Optional[List[_TypeGroup]] = (
            self._build_type_groups(self.state_types) if self.state_types else None
        )

    @property
    def is_active(self) -> bool:
        """True when type-aware fusion is fully configured and enabled."""
        return bool(self.state_types) and self.strategy != "replace"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(
        self,
        old: _QueueJointSnapshot,
        new_steps: List[ScheduledActionStep],
        *,
        current_position: Optional[List[float]] = None,
    ) -> List[ScheduledActionStep]:
        """Fuse *old* queue snapshot with incoming *new_steps*.

        Args:
            old: Snapshot of the current (old) action queue.
            new_steps: Incoming action steps to fuse.
            current_position: Current robot joint position, used as the
                reference anchor by ``nearest_neighbor`` strategy.  When
                ``None``, falls back to ``old.positions[0]``.
        """
        if not new_steps:
            return []

        if self.strategy == "replace":
            return list(new_steps)
        elif self.strategy == "weighted_average":
            if not old.steps:
                return list(new_steps)
            return self._fusion_weighted_average(old, new_steps)
        elif self.strategy == "nearest_neighbor":
            return self._fusion_nearest_neighbor(
                old, new_steps, current_position=current_position
            )
        else:
            return list(new_steps)

    # ------------------------------------------------------------------
    # Type group construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_type_groups(state_types: List[str]) -> List[_TypeGroup]:
        """Scan ``state_types`` and merge consecutive same-type elements."""
        if not state_types:
            raise ValueError("state_types must be non-empty when provided")

        groups: List[_TypeGroup] = []
        current_type = StateType(state_types[0])
        start = 0

        for i in range(1, len(state_types)):
            t = StateType(state_types[i])
            if t != current_type:
                groups.append(
                    _TypeGroup(start_index=start, end_index=i, state_type=current_type)
                )
                current_type = t
                start = i

        groups.append(
            _TypeGroup(
                start_index=start,
                end_index=len(state_types),
                state_type=current_type,
            )
        )
        return groups

    # ------------------------------------------------------------------
    # Order resolution
    # ------------------------------------------------------------------

    def _get_order(self, kind: str, default: str) -> List[int]:
        raw = self.state_type_order.get(kind, default)
        if kind == "euler":
            return _EULER_ORDER_MAP[raw]
        elif kind == "quaternion":
            return _QUAT_ORDER_MAP[raw]
        return list(range(3 if kind == "euler" else 4))

    # ------------------------------------------------------------------
    # Interpolation
    # ------------------------------------------------------------------

    def _interpolate_group(
        self,
        group: _TypeGroup,
        old_positions: List[float],
        new_positions: List[float],
        alpha: float,
        beta: float,
    ) -> List[float]:
        """Blend one ``_TypeGroup`` span with awareness of its physical semantics."""
        dims = min(len(old_positions), len(new_positions))
        end = min(group.end_index, dims)
        start = min(group.start_index, end)

        if start >= end:
            return []

        if group.state_type == StateType.EULER:
            if end - start < 3:
                return [
                    alpha * old_positions[start + k] + beta * new_positions[start + k]
                    for k in range(end - start)
                ]
            order = self._get_order("euler", "rpy")
            old_vals = [old_positions[start + order[k]] for k in range(3)]
            new_vals = [new_positions[start + order[k]] for k in range(3)]
            q1 = list(euler_to_quat(old_vals[0], old_vals[1], old_vals[2]))
            q2 = list(euler_to_quat(new_vals[0], new_vals[1], new_vals[2]))
            q_blended = slerp(q1, q2, beta)
            r, p, y = quat_to_euler(
                q_blended[0], q_blended[1], q_blended[2], q_blended[3]
            )
            result = [0.0, 0.0, 0.0]
            result[order[0]] = r
            result[order[1]] = p
            result[order[2]] = y
            return result

        elif group.state_type == StateType.QUATERNION:
            if end - start < 4:
                return [
                    alpha * old_positions[start + k] + beta * new_positions[start + k]
                    for k in range(end - start)
                ]
            order = self._get_order("quaternion", "wxyz")
            old_q = [old_positions[start + order[k]] for k in range(4)]
            new_q = [new_positions[start + order[k]] for k in range(4)]
            q1 = normalize_quat(old_q)
            q2 = normalize_quat(new_q)
            q_blended = slerp(q1, q2, beta)
            result = [0.0, 0.0, 0.0, 0.0]
            for k in range(4):
                result[order[k]] = q_blended[k]
            return result

        else:
            return [
                alpha * old_positions[start + k] + beta * new_positions[start + k]
                for k in range(end - start)
            ]

    # ------------------------------------------------------------------
    # Distance
    # ------------------------------------------------------------------

    def _group_distance(
        self,
        group: _TypeGroup,
        a: List[float],
        b: List[float],
    ) -> float:
        """Compute squared distance for one ``_TypeGroup`` span."""
        dims = min(len(a), len(b))
        end = min(group.end_index, dims)
        start = min(group.start_index, end)

        if start >= end:
            return 0.0

        if group.state_type == StateType.EULER:
            if end - start < 3:
                return sum(
                    (a[start + k] - b[start + k]) ** 2 for k in range(end - start)
                )
            order = self._get_order("euler", "rpy")
            old_vals = [a[start + order[k]] for k in range(3)]
            new_vals = [b[start + order[k]] for k in range(3)]
            q1 = list(euler_to_quat(old_vals[0], old_vals[1], old_vals[2]))
            q2 = list(euler_to_quat(new_vals[0], new_vals[1], new_vals[2]))
            d = quat_angular_distance(q1, q2)
            return d * d

        elif group.state_type == StateType.QUATERNION:
            if end - start < 4:
                return sum(
                    (a[start + k] - b[start + k]) ** 2 for k in range(end - start)
                )
            order = self._get_order("quaternion", "wxyz")
            old_q = [a[start + order[k]] for k in range(4)]
            new_q = [b[start + order[k]] for k in range(4)]
            q1 = normalize_quat(old_q)
            q2 = normalize_quat(new_q)
            d = quat_angular_distance(q1, q2)
            return d * d

        else:
            return sum(
                (a[start + k] - b[start + k]) ** 2 for k in range(end - start)
            )

    # ------------------------------------------------------------------
    # Fusion strategies
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fused_step(
        old_step: ScheduledActionStep,
        blended_position: List[float],
    ) -> ScheduledActionStep:
        payload = dict(old_step.payload)
        joint_states = dict(payload.get("joint_states", {}))
        joint_states["position"] = list(blended_position)
        payload["joint_states"] = joint_states
        return ScheduledActionStep(timestep=old_step.timestep, payload=payload)

    def _fusion_weighted_average(
        self,
        old: _QueueJointSnapshot,
        new_steps: List[ScheduledActionStep],
    ) -> List[ScheduledActionStep]:
        W = self.window_size
        M = len(old.steps)
        N = len(new_steps)
        W_actual = min(W, M, N)
        if W_actual == 0:
            return list(new_steps)

        new_positions = [
            step.payload.get("joint_states", {}).get("position", [])
            for step in new_steps
        ]

        result: List[ScheduledActionStep] = []
        for i in range(W_actual):
            alpha = 1.0 - i / W_actual
            beta = i / W_actual

            if self._type_groups:
                blended: List[float] = []
                for group in self._type_groups:
                    blended.extend(
                        self._interpolate_group(
                            group,
                            old.positions[i],
                            new_positions[i],
                            alpha,
                            beta,
                        )
                    )
            else:
                blended = [
                    alpha * old.positions[i][j] + beta * new_positions[i][j]
                    for j in range(min(len(old.positions[i]), len(new_positions[i])))
                ]
            result.append(self._build_fused_step(old.steps[i], blended))

        result.extend(new_steps[W_actual:])
        return result

    def _fusion_nearest_neighbor(
        self,
        old: _QueueJointSnapshot,
        new_steps: List[ScheduledActionStep],
        *,
        current_position: Optional[List[float]] = None,
    ) -> List[ScheduledActionStep]:
        """Find the step in *new_steps* closest to the reference anchor.

        Uses *current_position* (the robot's actual joint state) as the
        reference anchor when available.  Falls back to
        ``old.positions[0]`` when *current_position* is ``None``.  If
        neither is available the full *new_steps* chunk is returned
        unchanged.
        """
        # ── resolve reference anchor ─────────────────────────────────
        reference: Optional[List[float]] = None
        if current_position is not None:
            reference = current_position
        elif old.positions:
            reference = old.positions[0]

        if reference is None:
            return list(new_steps)

        new_positions = [
            step.payload.get("joint_states", {}).get("position", [])
            for step in new_steps
        ]

        best_index = 0
        best_distance = float("inf")
        for idx, pos in enumerate(new_positions):
            if not isinstance(pos, list):
                continue
            if not pos:
                continue

            if self._type_groups:
                dist_sq = 0.0
                for group in self._type_groups:
                    dist_sq += self._group_distance(group, reference, pos)
                dist = math.sqrt(dist_sq)
            else:
                dims = min(len(reference), len(pos))
                dist = math.sqrt(
                    sum((reference[d] - float(pos[d])) ** 2 for d in range(dims))
                )

            if dist < best_distance:
                best_distance = dist
                best_index = idx

        if best_distance == float("inf"):
            return list(new_steps)

        return list(new_steps[best_index:])


# ---------------------------------------------------------------------------
# Config validation (extracted from SyncRobotClient.__init__)
# ---------------------------------------------------------------------------


def _validate_fusion_config(
    state_types: Optional[List[str]],
    state_type_order: Optional[Dict[str, str]],
    strategy: str,
    window_size: int,
) -> None:
    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"fusion_strategy must be one of {_VALID_STRATEGIES}, "
            f"got '{strategy}'"
        )
    if window_size < 0:
        raise ValueError("fusion_window_size must be >= 0")

    _VALID_STATE_TYPES = {e.value for e in StateType}
    if state_types is not None:
        for i, st in enumerate(state_types):
            if st not in _VALID_STATE_TYPES:
                raise ValueError(
                    f"state_types[{i}] must be one of {_VALID_STATE_TYPES}, got {st!r}"
                )

    _VALID_EULER_ORDERS = {"rpy", "ypr", "ryp", "pyr"}
    _VALID_QUAT_ORDERS = {"wxyz", "xyzw"}
    if state_type_order is not None:
        euler_order = state_type_order.get("euler", "rpy")
        quat_order = state_type_order.get("quaternion", "wxyz")
        if euler_order not in _VALID_EULER_ORDERS:
            raise ValueError(
                f"state_type_order['euler'] must be one of {_VALID_EULER_ORDERS}, "
                f"got {euler_order!r}"
            )
        if quat_order not in _VALID_QUAT_ORDERS:
            raise ValueError(
                f"state_type_order['quaternion'] must be one of {_VALID_QUAT_ORDERS}, "
                f"got {quat_order!r}"
            )

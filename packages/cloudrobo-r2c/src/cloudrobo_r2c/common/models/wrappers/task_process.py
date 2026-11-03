"""TaskProcess data model (task orchestration progress)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .validators import (
    ValidationError,
    validate_required_fields,
    validate_string,
    validate_type,
)

logger = logging.getLogger(__name__)

try:
    from ..generated import task_process_pb2
except Exception:
    task_process_pb2 = None


@dataclass
class TaskNodeInfo:
    task_id: str = ""
    task_name: str = ""
    progress: float = 0.0
    result_state: int = 0
    retry_count: int = 0
    start_timestamp: int = 0
    duration: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskNodeInfo":
        validate_type(data, dict, "data", "TaskNodeInfo")
        v = data.get("progress")
        progress = float(v) if v is not None else 0.0
        v = data.get("result_state")
        result_state = int(v) if v is not None else 0
        v = data.get("retry_count")
        retry_count = int(v) if v is not None else 0
        v = data.get("start_timestamp")
        start_timestamp = int(v) if v is not None else 0
        v = data.get("duration")
        duration = int(v) if v is not None else 0
        return cls(
            task_id=data.get("task_id", ""),
            task_name=data.get("task_name", ""),
            progress=progress,
            result_state=result_state,
            retry_count=retry_count,
            start_timestamp=start_timestamp,
            duration=duration,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "progress": self.progress,
            "result_state": self.result_state,
            "retry_count": self.retry_count,
            "start_timestamp": self.start_timestamp,
            "duration": self.duration,
        }


@dataclass
class TaskStepInfo:
    action_timestamp: int = 0
    task_id: str = ""
    action_str: str = ""
    action_duration: int = 0
    think_timestamp: int = 0
    think_duration: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskStepInfo":
        validate_type(data, dict, "data", "TaskStepInfo")
        v = data.get("action_timestamp")
        action_timestamp = int(v) if v is not None else 0
        v = data.get("action_duration")
        action_duration = int(v) if v is not None else 0
        v = data.get("think_timestamp")
        think_timestamp = int(v) if v is not None else 0
        v = data.get("think_duration")
        think_duration = int(v) if v is not None else 0
        return cls(
            action_timestamp=action_timestamp,
            task_id=data.get("task_id", ""),
            action_str=data.get("action_str", ""),
            action_duration=action_duration,
            think_timestamp=think_timestamp,
            think_duration=think_duration,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_timestamp": self.action_timestamp,
            "task_id": self.task_id,
            "action_str": self.action_str,
            "action_duration": self.action_duration,
            "think_timestamp": self.think_timestamp,
            "think_duration": self.think_duration,
        }


@dataclass
class TaskProcessMessage:
    # 数据来源（发布时由 session._ensure_state_source 填充）
    source: str = ""
    session_id: str = ""
    session_name: str = ""
    timestamp: int = 0
    nodes: List[TaskNodeInfo] = field(default_factory=list)
    current_step: Optional[TaskStepInfo] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskProcessMessage":
        validate_type(data, dict, "data", "TaskProcessMessage")
        validate_required_fields(data, ["timestamp"], "TaskProcessMessage")
        if not isinstance(data["timestamp"], int) or isinstance(data["timestamp"], bool):
            raise ValidationError(
                "TaskProcessMessage: Field 'timestamp' must be int (epoch ms), "
                f"got {type(data['timestamp']).__name__}"
            )

        raw_nodes = data.get("nodes", [])
        if not isinstance(raw_nodes, list):
            raise ValidationError(
                "TaskProcessMessage: Field 'nodes' must be a list, "
                f"got {type(raw_nodes).__name__}"
            )

        current_step = data.get("current_step")
        return cls(
            source=data.get("source", ""),
            session_id=data.get("session_id", ""),
            session_name=data.get("session_name", ""),
            timestamp=data["timestamp"],
            nodes=[TaskNodeInfo.from_dict(n) for n in raw_nodes],
            current_step=TaskStepInfo.from_dict(current_step) if current_step else None,
        )

    @classmethod
    def from_protobuf(cls, payload: bytes) -> "TaskProcessMessage":
        if not task_process_pb2:
            raise ImportError("Protobuf code not generated")

        pb = task_process_pb2.TaskProcess()
        pb.ParseFromString(payload)

        current_step: Optional[TaskStepInfo] = None
        if pb.HasField("current_step"):
            current_step = TaskStepInfo(
                action_timestamp=pb.current_step.action_timestamp,
                task_id=pb.current_step.task_id,
                action_str=pb.current_step.action_str,
                action_duration=pb.current_step.action_duration,
                think_timestamp=pb.current_step.think_timestamp,
                think_duration=pb.current_step.think_duration,
            )

        return cls(
            source=pb.source,
            session_id=pb.session_id,
            session_name=pb.session_name,
            timestamp=pb.timestamp,
            nodes=[
                TaskNodeInfo(
                    task_id=n.task_id,
                    task_name=n.task_name,
                    progress=n.progress,
                    result_state=n.result_state,
                    retry_count=n.retry_count,
                    start_timestamp=n.start_timestamp,
                    duration=n.duration,
                )
                for n in pb.nodes
            ],
            current_step=current_step,
        )

    def to_protobuf(self) -> Any:
        if not task_process_pb2:
            raise ImportError("Protobuf code not generated")

        pb = task_process_pb2.TaskProcess()
        pb.source = self.source
        pb.session_id = self.session_id
        pb.session_name = self.session_name
        pb.timestamp = int(self.timestamp)
        for n in self.nodes:
            pb_node = pb.nodes.add()
            pb_node.task_id = n.task_id
            pb_node.task_name = n.task_name
            pb_node.progress = n.progress
            pb_node.result_state = n.result_state
            pb_node.retry_count = n.retry_count
            pb_node.start_timestamp = n.start_timestamp
            pb_node.duration = n.duration
        if self.current_step is not None:
            pb.current_step.action_timestamp = self.current_step.action_timestamp
            pb.current_step.task_id = self.current_step.task_id
            pb.current_step.action_str = self.current_step.action_str
            pb.current_step.action_duration = self.current_step.action_duration
            pb.current_step.think_timestamp = self.current_step.think_timestamp
            pb.current_step.think_duration = self.current_step.think_duration
        return pb

    def serialize(self) -> bytes:
        return self.to_protobuf().SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "session_id": self.session_id,
            "session_name": self.session_name,
            "timestamp": self.timestamp,
            "nodes": [n.to_dict() for n in self.nodes],
            "current_step": self.current_step.to_dict() if self.current_step else None,
        }

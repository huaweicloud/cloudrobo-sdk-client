"""Session APIs for publishing/subscribing R2C messages."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union, cast

import numpy as np

from cloudrobo_r2c.common.credential_bundle import BundleResourceContext
from cloudrobo_r2c.common.models import (
    Actions,
    EndEffectorState,
    Heartbeat,
    IMUState,
    JointStateMessage,
    LocalizationState,
    Observations,
    ObservationsH264,
)
from cloudrobo_r2c.common.utils.h264_decoder import H264Decoder
from cloudrobo_r2c.common.utils.logging_sanitizer import fmt_size
from cloudrobo_r2c.common.utils.h264_encoder import H264ImageEncoder

from ._heartbeat_loop import HeartbeatLoop, HeartbeatReportStats
from ._time_utils import _now_ms, _safe_error
from .types import (
    ActionCallbackType,
    AsyncActionCallbackType,
    AsyncObservationCallbackType,
    ObservationCallbackType,
    TCallback,
)

logger = logging.getLogger(__name__)

try:
    from cloudrobo_r2c.common.models.generated import observation_pb2
except Exception:
    observation_pb2 = None

from cloudrobo_r2c.transport import ITransport, ZenohTransport


@dataclass
class R2CSession:
    """Wraps a client session, responsible for observation upload and command subscription."""

    transport: ITransport
    client_id: str
    project_id: str
    device_id: str
    _conn_info: Optional[Dict[str, Any]] = None

    _shared_h264_encoder: Optional[H264ImageEncoder] = None
    _resource_context: Optional[BundleResourceContext] = None
    # Decoders created internally by subscribe_observations (when no decoder
    # is passed in). Tracked so close() can release their native av codec
    # contexts rather than leaking them across repeated subscribe calls.
    _internal_decoders: List[H264Decoder] = field(
        default_factory=list, init=False, repr=False
    )

    _hb_loop: Optional[HeartbeatLoop] = None
    _hb_stats: HeartbeatReportStats = field(default_factory=HeartbeatReportStats)
    _hb_lock: threading.Lock = field(default_factory=threading.Lock)
    _actions_queue: Optional[asyncio.Queue[Actions]] = field(
        default=None,
        init=False,
        repr=False,
    )
    _actions_subscription_started: bool = field(default=False, init=False, repr=False)
    # Serialises the check-then-subscribe in get_actions_async so two
    # concurrent coroutines cannot each create a subscription (which would
    # double-enqueue every action). Lazily created on first use because
    # asyncio.Lock() binds to the running loop at construction time.
    _actions_sub_lock: Optional[asyncio.Lock] = field(
        default=None, init=False, repr=False
    )

    def publish_observations(
        self,
        observation: Union[Observations, ObservationsH264, Dict[str, Any], bytes],
        image_encode: str = "raw",
        preset: Optional[str] = None,
    ) -> None:
        """Publish Observation to {project_id}/{device_id}/inference/observations."""
        topic = f"{self.project_id}/{self.device_id}/inference/observations"
        start = time.perf_counter()

        if isinstance(observation, dict):
            build_start = time.perf_counter()
            observation = self._build_observation_from_dict(
                observation, image_encode=image_encode, preset=preset
            )
            logger.debug(
                "Observation dict converted to model in %.2f ms (encoding=%s)",
                (time.perf_counter() - build_start) * 1000.0,
                image_encode,
            )

        if isinstance(observation, (Observations, ObservationsH264)):
            try:
                pb_msg = observation.to_protobuf()
                payload = pb_msg.SerializeToString()
            except Exception as e:
                # Do NOT fall back to JSON bytes: subscribers parse the
                # payload with ParseFromString (protobuf wire format) and
                # would silently misparse / drop a JSON payload while we
                # report the publish as successful. Fail loud instead so
                # the caller knows the observation was not published.
                raise RuntimeError(
                    f"Failed to serialize observation to protobuf: {e}. "
                    f"Recompile protos (scripts/recompile_protos.py) if the "
                    f"generated _pb2 modules are missing or stale."
                ) from e
            self.transport.publish(topic, payload)
            logger.debug(
                "Published observations payload (%s) in %.2f ms",
                fmt_size(len(payload)),
                (time.perf_counter() - start) * 1000.0,
            )
            return

        self._publish_model(topic, observation)
        logger.debug(
            "Published observations via generic model path in %.2f ms",
            (time.perf_counter() - start) * 1000.0,
        )

    def subscribe_observations(
        self,
        callback: ObservationCallbackType,
        target_device_id: Optional[str] = None,
        decode_images: bool = True,
        decoder: Optional[H264Decoder] = None,
    ) -> None:
        """Subscribe to observation topic and deserialize payloads."""
        dev_id = target_device_id if target_device_id else self.device_id
        topic = f"{self.project_id}/{dev_id}/inference/observations"
        if decoder is not None:
            _decoder = decoder
        elif decode_images:
            _decoder = H264Decoder()
            # Track internally-created decoders so close() can release them.
            self._internal_decoders.append(_decoder)
        else:
            _decoder = None

        def _wrapper(payload: bytes):
            start = time.perf_counter()
            try:
                if observation_pb2 is None:
                    raise ImportError(
                        "Protobuf code not valid. Please run compile_package_protos()."
                    )

                pb_msg = observation_pb2.Observations()
                pb_msg.ParseFromString(payload)

                is_h264 = False
                if getattr(pb_msg, "image_encoding", "") == "h264":
                    is_h264 = True
                elif hasattr(pb_msg, "HasField") and pb_msg.HasField("h264_images"):
                    is_h264 = True

                if is_h264:
                    obs_h264 = ObservationsH264.from_pb_object(pb_msg)

                    if decode_images and _decoder:
                        obj = self._decode_h264_observation(obs_h264, _decoder)
                    else:
                        obj = obs_h264
                else:
                    obj = Observations.from_pb_object(pb_msg)

                callback(obj)
                logger.debug(
                    "Observation callback executed in %.2f ms (payload=%d bytes, h264=%s)",
                    (time.perf_counter() - start) * 1000.0,
                    len(payload),
                    is_h264,
                )
            except Exception as e:
                logger.error(
                    "Error parsing observation: %s. Payload size: %d bytes",
                    e,
                    len(payload),
                )

        self.transport.subscribe(topic, _wrapper)

    def publish_actions(
        self,
        action: Union[Actions, Dict[str, Any], bytes],
    ) -> None:
        """Publish Action to {project_id}/{device_id}/inference/actions."""
        topic = f"{self.project_id}/{self.device_id}/inference/actions"
        start = time.perf_counter()

        if isinstance(action, dict):
            action = Actions.from_dict(action)

        if isinstance(action, Actions):
            try:
                pb_msg = action.to_protobuf()
                payload = pb_msg.SerializeToString()
            except Exception as e:
                # See publish_observations: do not fall back to JSON bytes
                # subscribers expect protobuf wire format and would silently
                # misparse JSON while we report success.
                raise RuntimeError(
                    f"Failed to serialize action to protobuf: {e}. "
                    f"Recompile protos (scripts/recompile_protos.py) if the "
                    f"generated _pb2 modules are missing or stale."
                ) from e
            self.transport.publish(topic, payload)
            logger.debug(
                "Published action payload (%d bytes) in %.2f ms",
                len(payload),
                (time.perf_counter() - start) * 1000.0,
            )
            return

        self._publish_model(topic, action)
        logger.debug(
            "Published actions via generic model path in %.2f ms",
            (time.perf_counter() - start) * 1000.0,
        )

    async def publish_observations_async(
        self,
        observation: Union[Observations, ObservationsH264, Dict[str, Any], bytes],
        image_encode: str = "raw",
        preset: Optional[str] = None,
    ) -> None:
        """Async version of publish_observations."""
        await asyncio.to_thread(
            self.publish_observations,
            observation,
            image_encode,
            preset,
        )

    async def publish_actions_async(
        self,
        action: Union[Actions, Dict[str, Any], bytes],
    ) -> None:
        """Async version of publish_actions."""
        await asyncio.to_thread(self.publish_actions, action)

    def subscribe_actions(
        self,
        callback: ActionCallbackType,
        target_device_id: Optional[str] = None,
    ) -> None:
        """Subscribe to Action."""
        dev_id = target_device_id if target_device_id else self.device_id
        topic = f"{self.project_id}/{dev_id}/inference/actions"

        def _wrapper(payload: bytes):
            start = time.perf_counter()
            try:
                action_obj = Actions.from_protobuf(payload)
                callback(action_obj)
                logger.debug(
                    "Action callback executed in %.2f ms (payload=%s)",
                    (time.perf_counter() - start) * 1000.0,
                    fmt_size(len(payload)),
                )
            except Exception as e:
                logger.error(
                    "Error parsing action: %s. Payload size: %d bytes",
                    e,
                    len(payload),
                )

        self.transport.subscribe(topic, _wrapper)

    def publish_joint_states(self, message: Union[JointStateMessage, bytes]) -> None:
        """Publish joint states to .../state/joint_states."""
        topic = f"{self.project_id}/{self.device_id}/state/joint_states"
        self._publish_model(topic, self._ensure_state_source(message))

    async def publish_joint_states_async(
        self,
        message: Union[JointStateMessage, bytes],
    ) -> None:
        """Async version of publish_joint_states."""
        await asyncio.to_thread(self.publish_joint_states, message)

    def publish_end_effector_states(
        self,
        message: Union[EndEffectorState, bytes],
    ) -> None:
        """Publish end-effector states to .../state/end_effector_states."""
        topic = f"{self.project_id}/{self.device_id}/state/end_effector_states"
        self._publish_model(topic, self._ensure_state_source(message))

    async def publish_end_effector_states_async(
        self,
        message: Union[EndEffectorState, bytes],
    ) -> None:
        """Async version of publish_end_effector_states."""
        await asyncio.to_thread(self.publish_end_effector_states, message)

    def publish_localization_states(
        self,
        message: Union[LocalizationState, bytes],
    ) -> None:
        """Publish localization states to .../state/localization_states."""
        topic = f"{self.project_id}/{self.device_id}/state/localization_states"
        self._publish_model(topic, self._ensure_state_source(message))

    async def publish_localization_states_async(
        self,
        message: Union[LocalizationState, bytes],
    ) -> None:
        """Async version of publish_localization_states."""
        await asyncio.to_thread(self.publish_localization_states, message)

    def publish_imu_states(self, message: Union[IMUState, bytes]) -> None:
        """Publish IMU states to .../state/imu_states."""
        topic = f"{self.project_id}/{self.device_id}/state/imu_states"
        self._publish_model(topic, self._ensure_state_source(message))

    async def publish_imu_states_async(self, message: Union[IMUState, bytes]) -> None:
        """Async version of publish_imu_states."""
        await asyncio.to_thread(self.publish_imu_states, message)

    def publish_heartbeats(
        self,
        message: Union[Heartbeat, Dict[str, Any], bytes],
    ) -> None:
        """Publish heartbeats to .../state/heartbeats."""
        topic = f"{self.project_id}/{self.device_id}/state/heartbeats"

        payload: Optional[bytes] = None
        try:
            if isinstance(message, dict):
                data = dict(message)
                if not data.get("timestamp"):
                    data["timestamp"] = _now_ms()
                message = Heartbeat.from_dict(data)

            if isinstance(message, Heartbeat):
                message = cast(Heartbeat, self._ensure_state_source(message))
                payload = message.serialize()
            elif isinstance(message, (bytes, bytearray)):
                payload = bytes(message)
            else:
                raise TypeError(
                    "Unsupported type for heartbeat publication: "
                    f"{type(message).__name__}"
                )

            self.transport.publish(topic, payload)

            with self._hb_lock:
                self._hb_stats.sent_messages += 1
                self._hb_stats.sent_bytes += len(payload)
            return
        except Exception as e:
            with self._hb_lock:
                self._hb_stats.failed_messages += 1
                self._hb_stats.last_error = _safe_error(e)
                self._hb_stats.last_error_ts_ms = _now_ms()
            raise

    def start_heartbeats(
        self,
        provider: Callable[[], Union[Heartbeat, Dict[str, Any], bytes]],
        interval_ms: int,
        jitter_ms: int = 0,
    ) -> None:
        """Start periodic heartbeat reporting in a background thread."""
        if not callable(provider):
            raise TypeError("provider must be callable")
        if interval_ms <= 0:
            raise ValueError("interval_ms must be > 0")
        if jitter_ms < 0:
            raise ValueError("jitter_ms must be >= 0")

        with self._hb_lock:
            if self._hb_loop and self._hb_loop.is_running():
                raise RuntimeError("heartbeats already running")
            self._hb_loop = HeartbeatLoop(
                provider=provider,
                publish_once=self.publish_heartbeats,
                interval_ms=int(interval_ms),
                jitter_ms=int(jitter_ms),
                stats=self._hb_stats,
                lock=self._hb_lock,
            )
            self._hb_loop.start()

    def stop_heartbeats(self) -> None:
        """Stop the background heartbeat loop and reclaim resources."""
        loop: Optional[HeartbeatLoop] = None
        with self._hb_lock:
            loop = self._hb_loop
        if loop:
            loop.stop(timeout_s=2.0)
        with self._hb_lock:
            if self._hb_loop is loop:
                self._hb_loop = None

    def get_state_report_stats(self, reset: bool = False) -> Dict[str, Any]:
        """Return stats for state reporting (heartbeats), optionally reset counters."""
        with self._hb_lock:
            hb = self._hb_stats.to_dict()
            hb["running"] = bool(self._hb_loop and self._hb_loop.is_running())
            hb["interval_ms"] = self._hb_loop.interval_ms if self._hb_loop else None
            hb["jitter_ms"] = self._hb_loop.jitter_ms if self._hb_loop else None
            out = {"heartbeats": hb}
            if reset:
                self._hb_stats.reset()
            return out

    async def publish_heartbeats_async(
        self,
        message: Union[Heartbeat, Dict[str, Any], bytes],
    ) -> None:
        """Async version of publish_heartbeats."""
        await asyncio.to_thread(self.publish_heartbeats, message)

    async def subscribe_observations_async(
        self,
        callback: Union[ObservationCallbackType, AsyncObservationCallbackType],
        target_device_id: Optional[str] = None,
        decode_images: bool = True,
        decoder: Optional[H264Decoder] = None,
    ) -> None:
        """Async version of subscribe_observations."""
        sync_callback = self._wrap_maybe_async_callback(callback)
        await asyncio.to_thread(
            self.subscribe_observations,
            sync_callback,
            target_device_id,
            decode_images,
            decoder,
        )

    async def subscribe_actions_async(
        self,
        callback: Union[ActionCallbackType, AsyncActionCallbackType],
        target_device_id: Optional[str] = None,
    ) -> None:
        """Async version of subscribe_actions."""
        sync_callback = self._wrap_maybe_async_callback(callback)
        await asyncio.to_thread(self.subscribe_actions, sync_callback, target_device_id)

    async def get_actions_async(
        self,
        timeout: Optional[float] = None,
        target_device_id: Optional[str] = None,
    ) -> Optional[Actions]:
        """Await next action message from inference/actions."""
        if self._actions_queue is None:
            self._actions_queue = asyncio.Queue()
        if self._actions_sub_lock is None:
            self._actions_sub_lock = asyncio.Lock()

        async with self._actions_sub_lock:
            if not self._actions_subscription_started:
                loop = asyncio.get_running_loop()

                def _on_action(action: Actions) -> None:
                    if self._actions_queue is None:
                        return
                    loop.call_soon_threadsafe(self._actions_queue.put_nowait, action)

                await self.subscribe_actions_async(_on_action, target_device_id)
                self._actions_subscription_started = True

        if timeout is None:
            return await self._actions_queue.get()

        try:
            return await asyncio.wait_for(self._actions_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def _get_h264_encoder(self) -> H264ImageEncoder:
        """Get shared H264 encoder instance (lazy-loaded)."""
        if self._shared_h264_encoder is None:
            self._shared_h264_encoder = H264ImageEncoder()
        return self._shared_h264_encoder

    def _build_observation_from_dict(
        self,
        data: Dict[str, Any],
        image_encode: str = "raw",
        preset: Optional[str] = None,
    ) -> Union[Observations, ObservationsH264]:
        """Construct Observation object from dict."""
        start = time.perf_counter()
        images_data = data.get("images", {})

        has_numpy_images = False
        numpy_images: Dict[str, Any] = {}

        if isinstance(images_data, dict):
            for key, value in images_data.items():
                if isinstance(value, np.ndarray):
                    has_numpy_images = True
                    numpy_images[key] = value
                elif isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        if isinstance(sub_value, np.ndarray):
                            has_numpy_images = True
                            numpy_images[sub_key] = sub_value

        if has_numpy_images and image_encode == "h264":
            encoder = self._get_h264_encoder()
            data_without_images = {
                k: v
                for k, v in data.items()
                if k != "images"
            }
            model = ObservationsH264.from_dict_and_encode_images(
                data_without_images,
                image_sources=numpy_images,
                encoder=encoder,
                preset=preset,
            )
            logger.debug(
                "Built H264 observation model in %.2f ms (image_count=%d, preset=%s)",
                (time.perf_counter() - start) * 1000.0,
                len(numpy_images),
                preset or "default",
            )
            return model
        model = Observations.from_dict(data)
        logger.debug(
            "Built raw observation model in %.2f ms (numpy_images_detected=%s)",
            (time.perf_counter() - start) * 1000.0,
            has_numpy_images,
        )
        return model

    def _decode_h264_observation(
        self,
        obs_h264: ObservationsH264,
        decoder: H264Decoder,
    ) -> Observations:
        """Decode ObservationsH264 to Observations."""
        start = time.perf_counter()
        from cloudrobo_r2c.common.models.wrappers.observations import ImageGroups

        metadata = obs_h264.images.metadata
        processed_data = {
            "images": {
                "h264_data": obs_h264.images.h264_data,
                "metadata": {
                    "image_keys": metadata.image_keys,
                    "original_shapes": [
                        (
                            (s.height, s.width, s.channels)
                            if hasattr(s, "height")
                            else tuple(s)
                        )
                        for s in metadata.original_shapes
                    ],
                    "combined_shape": (
                        (
                            metadata.combined_shape.height,
                            metadata.combined_shape.width,
                            metadata.combined_shape.channels,
                        )
                        if metadata.combined_shape
                        and hasattr(metadata.combined_shape, "height")
                        else (
                            tuple(metadata.combined_shape)
                            if metadata.combined_shape
                            else None
                        )
                    ),
                    "merge_direction": metadata.merge_direction,
                    "frame_count": metadata.frame_count,
                },
            }
        }

        decoded_data, _ = decoder.decode_and_split(processed_data)
        decoded_images = decoded_data.get("images", {})

        images = ImageGroups(color=decoded_images, depth={})

        decoded = Observations(
            timestamp=obs_h264.timestamp,
            task=obs_h264.task,
            id=obs_h264.id,
            images=images,
            joint_states=obs_h264.joint_states,
            end_effector_poses=obs_h264.end_effector_poses,
            end_effector_states=obs_h264.end_effector_states,
            localization=obs_h264.localization,
            pointclouds=obs_h264.pointclouds,
        )
        logger.debug(
            "Decoded H264 observation in %.2f ms (frames=%d)",
            (time.perf_counter() - start) * 1000.0,
            metadata.frame_count,
        )
        return decoded

    def _publish_model(self, topic: str, model_or_bytes: Union[object, bytes]) -> None:
        start = time.perf_counter()
        if isinstance(model_or_bytes, bytes):
            payload = model_or_bytes
        elif hasattr(model_or_bytes, "serialize"):
            payload = model_or_bytes.serialize()
        elif hasattr(model_or_bytes, "SerializeToString"):
            payload = model_or_bytes.SerializeToString()
        else:
            raise TypeError(
                "Unsupported type for publication: "
                f"{type(model_or_bytes).__name__}"
            )

        self.transport.publish(topic, payload)
        logger.debug(
            "Published payload to topic %s in %.2f ms (%d bytes)",
            topic,
            (time.perf_counter() - start) * 1000.0,
            len(payload),
        )

    def _ensure_state_source(
        self,
        model_or_bytes: Union[object, bytes],
    ) -> Union[object, bytes]:
        """Auto-fill state message source as "{project_id}/{device_id}"."""
        if isinstance(model_or_bytes, bytes):
            return model_or_bytes
        if hasattr(model_or_bytes, "source"):
            model_or_bytes.source = f"{self.project_id}/{self.device_id}"
        return model_or_bytes

    def close(self) -> None:
        """Actively release underlying connection and owned local resources."""
        start = time.perf_counter()
        try:
            self.stop_heartbeats()
        except Exception as e:
            logger.debug("stop_heartbeats() failed during close(): %s", type(e).__name__)

        # Release H264 codec resources: the shared encoder (lazily created by
        # publish_observations) and any decoders created internally by
        # subscribe_observations. Each holds a native av.CodecContext that
        # would otherwise leak across repeated subscribe/publish/close cycles.
        encoder = self._shared_h264_encoder
        if encoder is not None:
            try:
                encoder.close()
            except Exception as e:
                logger.debug(
                    "h264 encoder close failed during close(): %s", type(e).__name__
                )
            finally:
                self._shared_h264_encoder = None

        for dec in self._internal_decoders:
            try:
                dec.close()
            except Exception as e:
                logger.debug(
                    "h264 decoder close failed during close(): %s", type(e).__name__
                )
        self._internal_decoders.clear()

        # Reset the async actions-subscription state so a reconnect (close
        # get_actions_async again) re-subscribes to the new transport
        # instead of reusing the stale flag/queue from the previous session.
        self._actions_subscription_started = False
        self._actions_queue = None
        self._actions_sub_lock = None

        try:
            self.transport.close()
        finally:
            if self._resource_context is not None:
                try:
                    self._resource_context.cleanup()
                except Exception as e:
                    logger.debug(
                        "resource cleanup failed during close(): %s",
                        type(e).__name__,
                    )
                finally:
                    self._resource_context = None
        logger.debug(
            "R2CSession.close completed in %.2f ms (client_id=%s)",
            (time.perf_counter() - start) * 1000.0,
            self.client_id,
        )

    def connection_info(self) -> Dict[str, Any]:
        """Return safe connection diagnostics summary (sanitized)."""
        info = None
        try:
            info = self.transport.connection_info()
        except Exception as e:
            logger.debug("transport.connection_info() failed: %s", type(e).__name__)

        if info:
            try:
                return dict(info)
            except Exception as e:
                logger.debug(
                    "Failed to convert transport.connection_info() to dict: %s",
                    type(e).__name__,
                )

        return dict(self._conn_info or {})

    async def close_async(self) -> None:
        """Async version of close."""
        await asyncio.to_thread(self.close)

    def _wrap_maybe_async_callback(self, callback: TCallback) -> TCallback:
        """Wrap async callback into thread-safe sync callback accepted by transport."""
        if not inspect.iscoroutinefunction(callback):
            return callback

        async_callback = cast(Callable[[Any], Awaitable[None]], callback)
        loop = asyncio.get_running_loop()

        def _wrapper(message: Any) -> None:
            loop.call_soon_threadsafe(asyncio.create_task, async_callback(message))

        return cast(TCallback, _wrapper)
#!/usr/bin/env python3
"""Mock R2C Policy Server for end-to-end testing.

Connects to Zenoh, subscribes to observations, and publishes mock action chunks.
Designed to work with dummy robot configs to test the full R2C pipeline without
requiring torch/lerobot or real hardware.

Usage:
    python scripts/mock_policy_server.py \\
        --client-config config/client_config.yaml \\
        --chunk-size 100 \\
        --action-dim 6 \\
        --delay-ms 50

The server logs every observation received and action published so the test
runner can verify the pipeline is working end-to-end.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import time
from typing import Optional, Sequence

from cloudrobo_r2c.client import R2CClient
from cloudrobo_r2c.common.cli_utils import build_session_simple, parse_endpoints
from cloudrobo_r2c.common.config import ClientConfig
from cloudrobo_r2c.common.models import Actions, Observations

logger = logging.getLogger("mock-policy-server")

# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mock R2C Policy Server")
    p.add_argument("--bundle", type=str, default=None)
    p.add_argument("--client-config", default="config/client_config.yaml")
    p.add_argument("--project-id", type=str, default=None)
    p.add_argument("--device-id", type=str, default=None)
    p.add_argument("--client-id", type=str, default=None)
    p.add_argument("--endpoints", type=str, default="")
    p.add_argument("--mode", type=str, default=None, choices=["peer", "client"])
    p.add_argument(
        "--listen", action="store_true",
        help="Run as a listen server (endpoint_role='listen') so edge clients "
             "can connect directly without a Zenoh router.",
    )
    p.add_argument(
        "--chunk-size", type=int, default=100,
        help="Number of action steps per chunk (default: 100)",
    )
    p.add_argument(
        "--action-dim", type=int, default=6,
        help="Dimension of each action step (default: 6)",
    )
    p.add_argument(
        "--delay-ms", type=int, default=50,
        help="Simulated inference delay in ms (default: 50)",
    )
    p.add_argument(
        "--delay-return", type=float, default=0.0,
        help="Extra delay in seconds before publishing actions (simulates network latency, default: 0)",
    )
    p.add_argument(
        "--log-level", default="INFO",
        help="Python logging level",
    )
    p.add_argument(
        "--target-device-id", type=str, default=None,
        help="Only respond to observations from this device",
    )
    return p.parse_args(argv)


# ── Mock action generator ────────────────────────────────────────────────────


class MockActionGenerator:
    """Generate realistic-looking action trajectories.

    Produces smooth sinusoidal trajectories centered around a base position,
    with configurable chunk size and action dimension.
    """

    def __init__(self, chunk_size: int = 100, action_dim: int = 6) -> None:
        self.chunk_size = chunk_size
        self.action_dim = action_dim
        self._step = 0
        # Base positions (in degrees, roughly matching dummy default)
        self._base = [0.0, 0.1, -0.2, 0.0, 0.0, 0.5][:action_dim]
        # Amplitude per joint
        self._amp = [0.05, 0.05, 0.03, 0.04, 0.02, 0.01][:action_dim]
        # Frequency per joint
        self._freq = [0.02, 0.015, 0.025, 0.018, 0.03, 0.01][:action_dim]

    def generate(self) -> list[list[float]]:
        """Return a new action chunk of shape [chunk_size, action_dim]."""
        chunk: list[list[float]] = []
        for i in range(self.chunk_size):
            t = self._step + i
            positions: list[float] = []
            for j in range(self.action_dim):
                val = self._base[j] + self._amp[j] * math.sin(
                    t * self._freq[j] * math.pi
                )
                positions.append(round(val, 6))
            chunk.append(positions)
        self._step += self.chunk_size
        return chunk


# ── Main server loop ─────────────────────────────────────────────────────────


class MockPolicyServer:
    """Subscribe observations, publish mock action chunks."""

    def __init__(
        self,
        session,
        chunk_size: int = 100,
        action_dim: int = 6,
        delay_ms: int = 50,
        delay_return: float = 0.0,
    ) -> None:
        self._session = session
        self._generator = MockActionGenerator(chunk_size, action_dim)
        self._delay_ms = delay_ms
        self._delay_return = delay_return
        self._action_dim = action_dim
        self._observation_count = 0
        self._action_count = 0

    def start(self, target_device_id: Optional[str] = None) -> None:
        self._session.subscribe_observations(
            self._on_observations,
            target_device_id=target_device_id,
        )
        logger.info(
            "MockPolicyServer subscribed (target_device_id=%s, chunk_size=%d, action_dim=%d)",
            target_device_id,
            self._generator.chunk_size,
            self._action_dim,
        )

    def close(self) -> None:
        self._session.close()

    def _on_observations(self, observations: Observations) -> None:
        self._observation_count += 1
        logger.info(
            "Received observation id=%s (total=%d)",
            observations.id,
            self._observation_count,
        )

        # Simulate inference delay
        if self._delay_ms > 0:
            time.sleep(self._delay_ms / 1000.0)

        # Simulate network return delay
        if self._delay_return > 0:
            time.sleep(self._delay_return)

        # Generate mock action chunk
        positions = self._generator.generate()
        self._action_count += 1

        actions = Actions.from_dict({
            "timestamp": int(time.time() * 1000),
            "id": observations.id,
            "chunk_size": len(positions),
            "joint_states": {
                "names": [f"joint_{i+1}" for i in range(self._action_dim)],
                "position": positions,
                "velocity": [],
                "torque": [],
            },
        })
        self._session.publish_actions(actions)
        logger.info(
            "Published actions id=%s chunk_size=%d (total_actions=%d)",
            observations.id,
            len(positions),
            self._action_count,
        )


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    # Resolve config paths against the config shipped inside the installed
    # package, while still honoring explicit / source-checkout relative paths.
    from cloudrobo_r2c.common.config_path import resolve_config_path

    if args.client_config:
        args.client_config = resolve_config_path(args.client_config)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if args.listen:
        # Build config with endpoint_role="listen" — the mock server acts
        # as the Zenoh peer, and edge clients connect directly to it.
        config = ClientConfig.from_yaml(
            args.client_config,
            args.project_id,
            args.device_id,
            args.client_id or "mock-policy-server",
        )
        # Override endpoints/mode/endpoint_role for listen mode
        resolved_eps = parse_endpoints(args.endpoints) or ["tcp/0.0.0.0:7447"]
        config = ClientConfig(
            project_id=config.project_id,
            device_id=config.device_id,
            client_id=config.client_id,
            endpoint_role="listen",
            endpoints=tuple(resolved_eps),
            protocol=config.protocol,
            mode=args.mode or config.mode,
            authentication=config.authentication,
            connect=config.connect,
            tls=config.tls,
        )
        session = R2CClient.connect(config)
        logger.info(
            "MockPolicyServer listening on %s (mode=%s)",
            list(config.resolved_endpoints()),
            config.mode,
        )
    else:
        session = build_session_simple(args, default_client_id="mock-policy-server")
    server = MockPolicyServer(
        session=session,
        chunk_size=args.chunk_size,
        action_dim=args.action_dim,
        delay_ms=args.delay_ms,
        delay_return=args.delay_return,
    )
    server.start(target_device_id=args.target_device_id)

    logger.info("MockPolicyServer running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping MockPolicyServer...")
    finally:
        server.close()
        logger.info(
            "MockPolicyServer stopped (observations=%d, actions=%d)",
            server._observation_count,
            server._action_count,
        )


if __name__ == "__main__":
    main()

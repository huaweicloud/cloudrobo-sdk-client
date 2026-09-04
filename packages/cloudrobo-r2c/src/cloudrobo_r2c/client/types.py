"""Type aliases used by the client API."""

from __future__ import annotations

from typing import Awaitable, Callable, TypeVar, Union

from cloudrobo_r2c.common.models import (
    Actions,
    Observations,
    ObservationsH264,
)

ActionCallbackType = Callable[[Actions], None]
ObservationCallbackType = Callable[[Union[Observations, ObservationsH264]], None]

AsyncActionCallbackType = Callable[[Actions], Awaitable[None]]
AsyncObservationCallbackType = Callable[
    [Union[Observations, ObservationsH264]], Awaitable[None]
]

TCallback = TypeVar("TCallback")

"""openflight-range v1 codec: ResolvedShot -> newline-delimited JSON.

OpenFlight Range is a desktop driving-range visualizer. Unlike OpenConnect,
each shot carries OpenFlight's own committed RK4 flight path, so the range draws
exactly the carry the kiosk shows. The range never sends anything back.
Protocol: docs/using/simulator/openflight-range.md.
"""

import json
import logging
import math
import time
import uuid
from typing import List, Optional

from openflight import __version__
from openflight.sim.types import InboundEvent, ResolvedShot

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
YD_TO_M = 0.9144
YD_TO_FT = 3.0
MPH_TO_MPS = 0.44704

# Logical fields this codec transmits (drives the UI provenance badges).
_RANGE_FIELDS = ["ball_speed", "vla", "hla", "total_spin", "spin_axis", "carry", "club_speed"]


def _line(message: dict) -> bytes:
    # allow_nan=False is a backstop: _sanitize() below strips every non-finite
    # scalar and drops points/landing on a non-finite trajectory point, so this
    # should never actually raise. If it does, that is a bug in _sanitize().
    return (json.dumps(message, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _is_finite(value) -> bool:
    return not (isinstance(value, float) and not math.isfinite(value))


def _sanitize_scalars(obj, shot_number: int, sanitized: list) -> None:
    """Recursively replace any non-finite float in obj's dict values with None."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, dict):
                _sanitize_scalars(value, shot_number, sanitized)
            elif isinstance(value, float) and not math.isfinite(value):
                obj[key] = None
                sanitized.append(key)


class OpenFlightRangeCodec:
    """Protocol v1 for OpenFlight Range. ``name`` is the connector target."""

    def __init__(
        self, device_id: str = "OpenFlight", units: str = "Yards", name: str = "openflight-range"
    ):
        self.name = name
        self.device_id = device_id
        self.units = "meters" if str(units).lower().startswith("m") else "yards"
        # Shot identity on the range is session_uuid + shot_number; one per run.
        self.session_uuid = str(uuid.uuid4())

    def on_connect_bytes(self) -> Optional[bytes]:
        """Hello frame sent once, right after the TCP connection is established."""
        return _line(
            {
                "type": "hello",
                "v": PROTOCOL_VERSION,
                "app_version": __version__,
                "units": self.units,
                "device_id": self.device_id,
            }
        )

    def heartbeat_bytes(self) -> Optional[bytes]:
        """Keepalive frame; the transport sends this on every heartbeat tick."""
        return _line({"type": "heartbeat", "v": PROTOCOL_VERSION, "ts": round(time.time(), 3)})

    def parse_inbound(self, _frame: bytes) -> List[InboundEvent]:
        """The range never sends anything back; whatever arrives is ignored."""
        return []

    def fields_for_target(self) -> List[str]:
        """Logical ResolvedShot fields this codec transmits, for UI provenance badges."""
        return list(_RANGE_FIELDS)

    def build_shot(self, resolved: ResolvedShot) -> bytes:
        """Encode a resolved shot, with its committed flight path if present."""
        flight = resolved.flight
        spin_rpm = flight.conditions.spin_rpm if flight else resolved.total_spin_rpm
        spin_axis = flight.conditions.spin_axis_deg if flight else resolved.spin_axis_deg
        flew_measured = flight is None or flight.conditions.spin_source == "measured"
        spin_label = (
            "measured"
            if flew_measured and resolved.provenance.get("total_spin") == "measured"
            else "estimated"
        )
        message = {
            "type": "shot",
            "v": PROTOCOL_VERSION,
            "shot_number": resolved.shot_number,
            "session_uuid": self.session_uuid,
            "club": resolved.club.value,
            "ball": {
                "speed_mph": round(resolved.ball_speed_mph, 2),
                "launch_v_deg": round(resolved.vla, 2),
                "launch_h_deg": round(resolved.hla, 2),
                "spin_rpm": round(spin_rpm, 1),
                "spin_axis_deg": round(spin_axis, 2),
            },
            "club_data": {
                "speed_mph": resolved.club_speed_mph,
                "attack_angle_deg": None,
                "path_deg": (
                    resolved.club_path_deg
                    if resolved.provenance.get("club_path") == "measured"
                    else None
                ),
                "face_deg": None,
            },
            "sources": {
                "spin": spin_label,
                "launch_v": resolved.provenance.get("vla", "unknown"),
                "carry": resolved.provenance.get("carry", "unknown"),
            },
            "result": _result(resolved, flight),
        }
        if flight is not None and _trajectory_is_finite(flight.trajectory):
            message["points"] = _points(flight.trajectory)
            message["landing"] = {
                "speed_mps": round(flight.trajectory.landing_speed_mph * MPH_TO_MPS, 2),
                "angle_deg": round(flight.trajectory.landing_angle_deg, 1),
            }
        # No special case for result.carry_yd: if resolved.carry_yards is itself
        # non-finite, this sweep sends it as null on purpose. The range treats a
        # null carry as a protocol warning, visible on the HUD, which is the right
        # outcome here - carry normally comes straight from the committed
        # simulate() result or the resolver's fallback table, so a non-finite
        # value here means the physics broke, not that a field is merely unknown.
        sanitized_keys: List[str] = []
        _sanitize_scalars(message, resolved.shot_number, sanitized_keys)
        if sanitized_keys:
            logger.warning(
                "[sim] shot %s had non-finite value(s) in %s; sent as null",
                resolved.shot_number,
                sorted(set(sanitized_keys)),
            )
        return _line(message)


def _result(resolved: ResolvedShot, flight) -> dict:
    trajectory = flight.trajectory if flight else None
    return {
        "carry_yd": round(resolved.carry_yards, 2),
        "apex_ft": round(trajectory.apex_yards * YD_TO_FT, 1) if trajectory else None,
        "offline_yd": round(trajectory.lateral_yards, 2) if trajectory else None,
        "flight_time_s": round(trajectory.flight_time_s, 2) if trajectory else None,
    }


def _points(trajectory) -> list:
    # Upstream points: yards, x downrange, y right, z up. Protocol: metres, x, y up, z right.
    return [
        [round(p.t, 3), round(p.x * YD_TO_M, 3), round(p.z * YD_TO_M, 3), round(p.y * YD_TO_M, 3)]
        for p in trajectory.points
    ]


def _trajectory_is_finite(trajectory) -> bool:
    """False if any trajectory point, or a summary field, is NaN/inf.

    A degenerate point can't be sanitized field-by-field the way scalar shot
    fields are (a null x/y/z would draw a broken path), so the whole points
    array (and the landing summary derived from the same run) is omitted.
    """
    for p in trajectory.points:
        if not (_is_finite(p.t) and _is_finite(p.x) and _is_finite(p.y) and _is_finite(p.z)):
            return False
    return _is_finite(trajectory.landing_speed_mph) and _is_finite(trajectory.landing_angle_deg)

"""openflight-range v1 codec: wire format for the OpenFlight Range visualizer."""

import dataclasses
import json
import uuid

import pytest

from openflight.ballistics import CommittedFlight, LaunchConditions, simulate
from openflight.clubs import ClubType
from openflight.range.codec import PROTOCOL_VERSION, OpenFlightRangeCodec
from openflight.sim.types import ResolvedShot

YD_TO_M = 0.9144


def _strict_json_loads(raw: bytes):
    def _reject_constant(name):
        raise ValueError(f"non-finite constant in JSON: {name}")

    return json.loads(raw, parse_constant=_reject_constant)


def _flight(launch_h: float = 0.0, spin_axis: float = 0.0, spin: float = 6000.0) -> CommittedFlight:
    conditions = LaunchConditions(
        ball_speed_mph=120.0,
        launch_angle_v=18.0,
        launch_angle_h=launch_h,
        spin_rpm=spin,
        spin_axis_deg=spin_axis,
        spin_source="measured",
    )
    return CommittedFlight(conditions=conditions, trajectory=simulate(conditions))


def _resolved(flight=None, **kw) -> ResolvedShot:
    base = dict(
        shot_number=1_780_000_001,
        ball_speed_mph=120.0,
        vla=18.0,
        hla=0.0,
        total_spin_rpm=7000.0,
        spin_axis_deg=0.0,
        back_spin_rpm=7000.0,
        side_spin_rpm=0.0,
        carry_yards=flight.trajectory.carry_yards if flight else 150.0,
        club_path_deg=0.0,
        club=ClubType.IRON_7,
        club_speed_mph=88.0,
        provenance={
            "total_spin": "measured",
            "vla": "measured",
            "carry": "measured",
            "club_path": "estimated",
        },
        flight=flight,
    )
    base.update(kw)
    return ResolvedShot(**base)


def _decode(raw: bytes) -> dict:
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    return json.loads(raw)


def test_hello_on_connect():
    msg = _decode(OpenFlightRangeCodec(device_id="pi-1").on_connect_bytes())
    assert msg["type"] == "hello"
    assert msg["v"] == PROTOCOL_VERSION == 1
    assert msg["units"] == "yards"
    assert msg["device_id"] == "pi-1"
    assert msg["app_version"]


def test_units_meters_maps_to_protocol_value():
    assert _decode(OpenFlightRangeCodec(units="Meters").on_connect_bytes())["units"] == "meters"


def test_heartbeat_shape():
    msg = _decode(OpenFlightRangeCodec().heartbeat_bytes())
    assert msg["type"] == "heartbeat" and msg["v"] == 1
    assert isinstance(msg["ts"], (int, float))


def test_range_sends_nothing_back_so_inbound_is_ignored():
    assert OpenFlightRangeCodec().parse_inbound(b'{"anything": 1}') == []


def test_name_and_session_identity():
    codec = OpenFlightRangeCodec()
    assert codec.name == "openflight-range"
    uuid.UUID(codec.session_uuid)
    assert OpenFlightRangeCodec().session_uuid != codec.session_uuid


def test_shot_envelope_and_committed_carry():
    flight = _flight()
    resolved = _resolved(flight)
    msg = _decode(OpenFlightRangeCodec().build_shot(resolved))
    assert msg["type"] == "shot" and msg["v"] == 1
    assert msg["shot_number"] == resolved.shot_number
    assert msg["club"] == "7-iron"
    assert msg["result"]["carry_yd"] == pytest.approx(resolved.carry_yards, abs=0.01)


def test_points_are_metres_in_protocol_frame_and_land_at_carry():
    flight = _flight()
    msg = _decode(OpenFlightRangeCodec().build_shot(_resolved(flight)))
    pts = msg["points"]
    assert len(pts) == len(flight.trajectory.points)
    assert pts[0][1:] == [0.0, 0.0, 0.0]
    last = pts[-1]
    assert last[1] == pytest.approx(msg["result"]["carry_yd"] * YD_TO_M, abs=0.05)
    assert last[2] == pytest.approx(0.0, abs=0.01)


def test_height_is_protocol_y_and_matches_apex():
    msg = _decode(OpenFlightRangeCodec().build_shot(_resolved(_flight())))
    peak_y = max(p[2] for p in msg["points"])
    assert peak_y == pytest.approx(msg["result"]["apex_ft"] / 3.0 * YD_TO_M, abs=0.1)
    assert max(abs(p[3]) for p in msg["points"]) < 0.01


def test_launch_right_is_positive_z_and_offline():
    msg = _decode(OpenFlightRangeCodec().build_shot(_resolved(_flight(launch_h=5.0))))
    assert msg["points"][-1][3] > 1.0
    assert msg["result"]["offline_yd"] > 0


def test_ball_spin_is_the_spin_that_flew():
    # The resolver's fallback spin (7000 here) differs from the conditions the
    # simulator used (6000); the card must show the spin behind the drawn path.
    msg = _decode(OpenFlightRangeCodec().build_shot(_resolved(_flight(spin=6000.0))))
    assert msg["ball"]["spin_rpm"] == pytest.approx(6000.0)


def test_spin_label_measured_only_when_flight_and_resolver_agree():
    measured = _decode(OpenFlightRangeCodec().build_shot(_resolved(_flight())))
    assert measured["sources"]["spin"] == "measured"
    table = _resolved(_flight(), provenance={"total_spin": "estimated", "vla": "measured"})
    assert _decode(OpenFlightRangeCodec().build_shot(table))["sources"]["spin"] == "estimated"


def test_shot_without_flight_keeps_carry_and_omits_points():
    resolved = _resolved(None, carry_yards=131.4)
    msg = _decode(OpenFlightRangeCodec().build_shot(resolved))
    assert msg["result"]["carry_yd"] == pytest.approx(131.4)
    assert "points" not in msg
    assert msg["ball"]["spin_rpm"] == pytest.approx(resolved.total_spin_rpm)
    assert msg["result"]["apex_ft"] is None


def test_club_path_only_when_measured():
    msg = _decode(OpenFlightRangeCodec().build_shot(_resolved(_flight())))
    assert msg["club_data"]["speed_mph"] == pytest.approx(88.0)
    assert msg["club_data"]["path_deg"] is None


def test_fields_for_target():
    fields = OpenFlightRangeCodec().fields_for_target()
    assert {"ball_speed", "vla", "total_spin", "carry"} <= set(fields)


def test_nonfinite_trajectory_point_omits_points_and_landing_keeps_carry():
    flight = _flight()
    bad_point = dataclasses.replace(flight.trajectory.points[-1], x=float("nan"))
    points = list(flight.trajectory.points)
    points[-1] = bad_point
    trajectory = dataclasses.replace(flight.trajectory, points=points)
    flight = dataclasses.replace(flight, trajectory=trajectory)
    resolved = _resolved(flight)

    raw = OpenFlightRangeCodec().build_shot(resolved)
    msg = _strict_json_loads(raw)
    assert "points" not in msg
    assert "landing" not in msg
    assert msg["result"]["carry_yd"] == pytest.approx(resolved.carry_yards, abs=0.01)


def test_nonfinite_ball_speed_becomes_null_and_line_is_strict_json():
    resolved = _resolved(_flight(), ball_speed_mph=float("inf"))
    raw = OpenFlightRangeCodec().build_shot(resolved)
    msg = _strict_json_loads(raw)
    assert msg["ball"]["speed_mph"] is None


def test_nonfinite_flight_spin_becomes_null_and_line_is_strict_json():
    conditions = LaunchConditions(
        ball_speed_mph=120.0,
        launch_angle_v=18.0,
        launch_angle_h=0.0,
        spin_rpm=float("nan"),
        spin_axis_deg=0.0,
        spin_source="measured",
    )
    real_flight = _flight()
    flight = CommittedFlight(conditions=conditions, trajectory=real_flight.trajectory)
    resolved = _resolved(flight)
    raw = OpenFlightRangeCodec().build_shot(resolved)
    msg = _strict_json_loads(raw)
    assert msg["ball"]["spin_rpm"] is None


def test_sanitizing_nonfinite_values_logs_a_warning_naming_the_shot(caplog):
    resolved = _resolved(_flight(), ball_speed_mph=float("inf"))
    with caplog.at_level("WARNING"):
        OpenFlightRangeCodec().build_shot(resolved)
    assert any(
        "[sim]" in r.message and str(resolved.shot_number) in r.message for r in caplog.records
    )


def test_finite_shot_produces_strict_json():
    msg = _strict_json_loads(OpenFlightRangeCodec().build_shot(_resolved(_flight())))
    assert msg["type"] == "shot"

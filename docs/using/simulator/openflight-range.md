# OpenFlight Range

OpenFlight Range is a desktop driving-range visualizer: a PC or Mac mini driving
a projector or large screen draws each shot. Unlike the OpenConnect simulators,
it does not run its own ball flight. The `openflight-range` connector sends
OpenFlight's own committed flight path with every shot, so the range draws
exactly the carry the kiosk shows. Repository link to follow.

See the connector architecture in [Simulator Connectors](index.md). This page
covers setup specific to OpenFlight Range.

## Requirements

- **OpenFlight Range running on the PC.** It listens on TCP **3200** and shows
  its address on screen ("Waiting for OpenFlight on `ip`:3200").
- **Network reachability.** The machine running OpenFlight (e.g. the Raspberry
  Pi) and the range PC must be on the same LAN, and 3200 must be reachable
  (mind the host firewall).
- No account/credentials are sent by OpenFlight. The protocol has no auth.

## Setup

1. **Start OpenFlight Range on the PC.** Note the address it shows (e.g.
   `192.168.1.60:3200`).
2. **Configure OpenFlight.** Copy the example config if you haven't already:
   ```bash
   cp config/sim.example.json config/sim.json
   ```
   Enable the OpenFlight Range connector with the PC's IP:
   ```jsonc
   {
     "connectors": [
       { "type": "openflight-range", "enabled": true, "host": "192.168.1.60", "port": 3200 }
     ]
   }
   ```
   `units` (`Yards` or `Meters`) sets the unit the range displays; it defaults
   to `Yards`. The wire values are always in the fixed units listed below.
3. **Start OpenFlight with simulator connectors on** (`--sim`, off by default):
   ```bash
   scripts/start-kiosk.sh --sim
   ```
   The header **OpenFlight Range** pill should turn **green**.
4. **Hit a shot.** The range draws the flight and shows the shot card. With
   debug mode on, the "Sent to OpenFlight Range" panel shows the values sent
   with measured/estimated badges.

## Protocol

Protocol v1 is newline-delimited JSON over TCP, UTF-8, one object per line.
OpenFlight connects to the range; the range never sends anything back. Every
object carries `type` and `v` (the protocol major version, the integer `1`).
Unknown fields are ignored.

| Message | When | Fields |
|---|---|---|
| `hello` | once, on connect | `v`, `app_version`, `units` (`"yards"`/`"meters"`), `device_id` |
| `shot` | per shot | `v`, `shot_number`, `session_uuid`, `club`, `profile` {id, name}, `ball` {speed_mph, launch_v_deg, launch_h_deg, spin_rpm, spin_axis_deg}, `club_data` {speed_mph, attack_angle_deg, path_deg, face_deg} (each nullable), `sources` {spin: measured/calculated/estimated, launch_v: ...}, `result` {carry_yd, apex_ft, offline_yd (positive = right), flight_time_s}, `points` [[t_s, x_m, y_m, z_m], ...], `landing` {speed_mps, angle_deg} |
| `heartbeat` | every 5 s | `v`, `ts` (Unix time, seconds) |

- **Shot identity.** `shot_number` + `session_uuid` identify a shot; the range
  drops repeats. OpenFlight picks a new `session_uuid` each time it starts.
  OpenFlight does not send `profile` yet, and adds `sources.carry`.
- **Carry.** `result.carry_yd` is always the carry OpenFlight committed for the
  shot (the same number the kiosk shows); the connector never recomputes it.
- **Flight path.** `points` is the committed trajectory, one point per
  simulator sample (currently 50 ms), in **metres** in the protocol frame:
  **x downrange, y up, z to the right**, origin at the ball. `ball.spin_rpm` is
  the spin that produced that flight. `sources.spin` is `measured` only when
  that spin was measured and the resolver's provenance for total spin is
  `measured`; otherwise it is `estimated`.
- **Shots without a flight.** When there is no ballistic flight (no vertical
  launch angle, or ballistics disabled), the shot carries the committed carry
  and no `points` (and no `landing`); `apex_ft`, `offline_yd` and
  `flight_time_s` are `null`.
- **Liveness.** OpenFlight sends a `heartbeat` every 5 s. The range drops a
  connection from which no bytes arrived for 15 s ("no data for 15 s") and the
  connector reconnects with backoff.

## Troubleshooting

- **Pill stays amber (connecting / reconnecting):** OpenFlight can't reach
  `host:port`. Check that OpenFlight Range is running, that `sim.json` has the
  IP the range shows, that 3200 isn't firewalled on the PC, and that you
  launched with `--sim`. OpenFlight retries automatically with backoff.
- **Ball drawn with "no flight path":** the shot had no vertical launch angle
  (or ballistics were off), so OpenFlight sent the committed carry from the
  per-club table without a path. Check the angle radar and the
  `sim_send` entries in the session log.

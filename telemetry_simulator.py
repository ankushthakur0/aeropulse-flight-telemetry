"""
AeroPulse - flight telemetry simulator.

Generates realistic, smoothly changing aircraft readings (not pure random
noise), occasionally injects faults, and stores everything in SQLite.

Run:
    python telemetry_simulator.py                 # 1 reading per second, forever
    python telemetry_simulator.py --interval 0.2  # faster
    python telemetry_simulator.py --ticks 600     # stop after 600 readings
Stop with Ctrl+C.
"""

import argparse
import random
import sqlite3
import time
import uuid
from datetime import datetime, timezone

DB_FILE = "aeropulse.db"

# --- Flight profile (in ticks, i.e. readings) --------------------------------
CLIMB_TICKS = 120
CRUISE_TICKS = 360
DESCENT_TICKS = 120
FLIGHT_TICKS = CLIMB_TICKS + CRUISE_TICKS + DESCENT_TICKS

CRUISE_ALT_FT = 35000
CRUISE_SPEED_KTS = 450
TEMP_ALERT_C = 110
VIBRATION_ALERT_G = 1.2


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id     TEXT NOT NULL,
            ts            TEXT NOT NULL,
            phase         TEXT NOT NULL,
            altitude_ft   REAL,
            airspeed_kts  REAL,
            heading_deg   REAL,
            engine_temp_c REAL,
            fuel_pct      REAL,
            vibration_g   REAL,
            alert         TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flight_ts ON telemetry(flight_id, ts)")
    conn.commit()


def smooth(current, target, rate, noise):
    """Move a value a fraction of the way toward a target, plus small noise.
    This is what makes the curves look real instead of jumping around."""
    return current + (target - current) * rate + random.gauss(0, noise)


class Flight:
    def __init__(self):
        self.flight_id = "AP-" + uuid.uuid4().hex[:6].upper()
        self.tick = 0
        self.altitude = 0.0
        self.airspeed = 0.0
        self.heading = random.uniform(0, 360)
        self.engine_temp = 80.0
        self.fuel = 100.0
        self.vibration = 0.3
        self.fault_ticks_left = 0

    def phase(self):
        if self.tick < CLIMB_TICKS:
            return "CLIMB"
        if self.tick < CLIMB_TICKS + CRUISE_TICKS:
            return "CRUISE"
        return "DESCENT"

    def step(self):
        phase = self.phase()

        # Targets depend on the phase of flight.
        if phase == "CLIMB":
            target_alt, target_speed, target_temp, burn = CRUISE_ALT_FT, 380, 98, 0.045
        elif phase == "CRUISE":
            target_alt, target_speed, target_temp, burn = CRUISE_ALT_FT, CRUISE_SPEED_KTS, 90, 0.028
        else:
            target_alt, target_speed, target_temp, burn = 0, 220, 82, 0.012

        self.altitude = max(0.0, smooth(self.altitude, target_alt, 0.02, 40))
        self.airspeed = max(0.0, smooth(self.airspeed, target_speed, 0.05, 2.5))
        self.heading = (self.heading + random.gauss(0, 0.6)) % 360
        self.engine_temp = smooth(self.engine_temp, target_temp, 0.08, 0.6)
        self.fuel = max(0.0, self.fuel - burn + random.gauss(0, 0.003))
        self.vibration = max(0.05, smooth(self.vibration, 0.3, 0.2, 0.03))

        # Fault injection: ~1% chance per tick to start a short overheat event.
        if self.fault_ticks_left == 0 and random.random() < 0.01:
            self.fault_ticks_left = random.randint(5, 15)
        if self.fault_ticks_left > 0:
            self.engine_temp += random.uniform(1.5, 3.5)
            self.vibration += random.uniform(0.05, 0.15)
            self.fault_ticks_left -= 1

        self.tick += 1
        return self.reading(phase)

    def reading(self, phase):
        alerts = []
        if self.engine_temp > TEMP_ALERT_C:
            alerts.append("HIGH_ENGINE_TEMP")
        if self.vibration > VIBRATION_ALERT_G:
            alerts.append("HIGH_VIBRATION")
        if self.fuel < 10:
            alerts.append("LOW_FUEL")
        return {
            "flight_id": self.flight_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "phase": phase,
            "altitude_ft": round(self.altitude, 1),
            "airspeed_kts": round(self.airspeed, 1),
            "heading_deg": round(self.heading, 1),
            "engine_temp_c": round(self.engine_temp, 1),
            "fuel_pct": round(self.fuel, 2),
            "vibration_g": round(self.vibration, 3),
            "alert": ",".join(alerts) if alerts else None,
        }

    @property
    def finished(self):
        return self.tick >= FLIGHT_TICKS


def main():
    parser = argparse.ArgumentParser(description="AeroPulse telemetry simulator")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between readings")
    parser.add_argument("--ticks", type=int, default=0, help="stop after N readings (0 = run forever)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    flight = Flight()
    print(f"AeroPulse telemetry simulator started | flight {flight.flight_id}")

    count = 0
    try:
        while True:
            r = flight.step()
            conn.execute(
                """INSERT INTO telemetry
                   (flight_id, ts, phase, altitude_ft, airspeed_kts, heading_deg,
                    engine_temp_c, fuel_pct, vibration_g, alert)
                   VALUES (:flight_id, :ts, :phase, :altitude_ft, :airspeed_kts,
                           :heading_deg, :engine_temp_c, :fuel_pct, :vibration_g, :alert)""",
                r,
            )
            conn.commit()
            count += 1

            print(
                f"[{r['phase']:<7}] Alt={r['altitude_ft']:>8.0f} ft | "
                f"Speed={r['airspeed_kts']:>5.0f} kts | Hdg={r['heading_deg']:>5.1f} | "
                f"Eng={r['engine_temp_c']:>5.1f}C | Fuel={r['fuel_pct']:>5.1f}% | "
                f"Vib={r['vibration_g']:.2f}g"
            )
            if r["alert"]:
                print(f"   WARNING: {r['alert']}")

            if flight.finished:
                print(f"Flight {flight.flight_id} complete. Starting a new flight.")
                flight = Flight()
            if args.ticks and count >= args.ticks:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        conn.close()
        print(f"Wrote {count} readings to {DB_FILE}")


if __name__ == "__main__":
    main()  

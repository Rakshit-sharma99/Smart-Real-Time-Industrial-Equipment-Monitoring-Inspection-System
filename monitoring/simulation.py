"""
Fake sensor values for the simulator.

Each machine has a sim_mode (set on the Settings page). The mode applies to the
machine's *primary* metric - the one most associated with that kind of machine
(pressure for a compressor, vibration for a CNC machine...). The other two
metrics always behave normally. This way one mode change produces one clear
alert in a demo.

  NORMAL         random walk between 40 % and 80 % of the warning limit
  DRIFT          climbs slowly from normal to just above critical, then resets
  FORCE_WARNING  between the warning and critical limits
  FORCE_CRITICAL 5-20 % above the critical limit
  OFFLINE        sends nothing
"""
import random
from dataclasses import dataclass, field
from decimal import Decimal

from equipment.metrics import METRICS, PRESSURE, TEMPERATURE, VIBRATION
from equipment.models import Equipment

Mode = Equipment.SimMode
T = Equipment.EquipmentType

PRIMARY_METRIC_BY_TYPE = {
    T.AIR_COMPRESSOR: PRESSURE,
    T.HYDRAULIC_PUMP: PRESSURE,
    T.CNC_MACHINE: VIBRATION,
    T.BOILER: TEMPERATURE,
    T.CONVEYOR_MOTOR: VIBRATION,
    T.COOLING_TOWER: TEMPERATURE,
    T.OTHER: TEMPERATURE,
}

DRIFT_TICKS = 30  # readings it takes DRIFT to climb from normal to critical


def primary_metric(equipment):
    return PRIMARY_METRIC_BY_TYPE.get(equipment.equipment_type, TEMPERATURE)


@dataclass
class MachineState:
    """Remembered between ticks so values move smoothly."""

    values: dict = field(default_factory=dict)  # metric key -> last value (float)
    drift_progress: float = 0.0  # 0.0 = normal, 1.0 = just above critical


class SensorSimulator:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.states = {}  # equipment pk -> MachineState

    def next_values(self, equipment, noise_pct=2.0, mode=None):
        """
        Values for one tick: {"temperature_c": Decimal, "vibration_mm_s": ..., "pressure_bar": ...}
        or None when the machine is in OFFLINE mode. `mode` overrides the
        machine's sim_mode (the seed command uses it to backfill normal history).
        """
        machine_mode = mode or equipment.sim_mode
        if machine_mode == Mode.OFFLINE:
            return None
        state = self.states.setdefault(equipment.pk, MachineState())
        primary = primary_metric(equipment)
        values = {}
        for metric in METRICS:
            mode = machine_mode if metric == primary else Mode.NORMAL
            warn, crit = (float(limit) for limit in equipment.thresholds(metric))
            value = self.next_value(mode, state, metric.key, warn, crit, float(noise_pct))
            values[metric.reading_field] = Decimal(f"{value:.2f}")
        return values

    def next_value(self, mode, state, key, warn, crit, noise_pct):
        if mode == Mode.FORCE_WARNING:
            value = warn + (crit - warn) * self.rng.uniform(0.25, 0.75)
        elif mode == Mode.FORCE_CRITICAL:
            value = crit * self.rng.uniform(1.05, 1.20)
        elif mode == Mode.DRIFT:
            start, end = 0.6 * warn, crit * 1.03
            value = self.add_noise(start + state.drift_progress * (end - start), noise_pct)
            state.drift_progress += 1 / DRIFT_TICKS
            if state.drift_progress > 1:
                state.drift_progress = 0.0  # reached critical: start over
        else:  # NORMAL: random walk inside the normal band
            low, high = 0.4 * warn, 0.8 * warn
            previous = state.values.get(key, (low + high) / 2)
            value = previous + self.rng.uniform(-1, 1) * 0.04 * warn
            value = min(max(self.add_noise(value, noise_pct), low), high)
        state.values[key] = value
        return value

    def add_noise(self, value, noise_pct):
        return value * (1 + self.rng.uniform(-noise_pct, noise_pct) / 100)

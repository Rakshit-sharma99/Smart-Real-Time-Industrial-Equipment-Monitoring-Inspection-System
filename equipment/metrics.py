"""
The three monitored metrics and the rule that classifies a value.

Simplification (documented): every metric has an UPPER limit only - a higher
value is always worse. Each machine stores its own warning and critical limit
per metric, with 0 < warning < critical.
"""
from dataclasses import dataclass
from decimal import Decimal

NORMAL = "NORMAL"
WARNING = "WARNING"
CRITICAL = "CRITICAL"

# Used to pick the "worst" of several statuses.
SEVERITY_RANK = {NORMAL: 0, WARNING: 1, CRITICAL: 2}


@dataclass(frozen=True)
class Metric:
    key: str  # stored in Alert.metric
    label: str
    unit: str
    reading_field: str  # column on SensorReading
    warn_field: str  # columns on Equipment
    crit_field: str
    default_warn: Decimal
    default_crit: Decimal


TEMPERATURE = Metric(
    "temperature", "Temperature", "°C", "temperature_c", "temp_warn", "temp_crit",
    Decimal("70.00"), Decimal("85.00"),
)
VIBRATION = Metric(
    "vibration", "Vibration", "mm/s", "vibration_mm_s", "vib_warn", "vib_crit",
    Decimal("7.10"), Decimal("11.00"),
)
PRESSURE = Metric(
    "pressure", "Pressure", "bar", "pressure_bar", "pres_warn", "pres_crit",
    Decimal("8.00"), Decimal("10.00"),
)

METRICS = (TEMPERATURE, VIBRATION, PRESSURE)
METRICS_BY_KEY = {metric.key: metric for metric in METRICS}
METRIC_CHOICES = [(metric.key, metric.label) for metric in METRICS]


def classify(value, warn, crit):
    """
    NORMAL   : value <  warning
    WARNING  : warning <= value < critical
    CRITICAL : value >= critical
    """
    if value >= crit:
        return CRITICAL
    if value >= warn:
        return WARNING
    return NORMAL


def worst(statuses):
    """The most severe status in the list (NORMAL if the list is empty)."""
    return max(statuses, key=SEVERITY_RANK.__getitem__, default=NORMAL)

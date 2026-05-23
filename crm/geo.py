"""Geospatial helpers for field visit proximity validation."""
import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union

FIELD_VISIT_MAX_DISTANCE_METERS = 10
# Extra meters from browser-reported GPS accuracy (GeolocationPosition.coords.accuracy).
GPS_ACCURACY_BUFFER_CAP_METERS = 25.0
# Lead pin and field visit use two separate GPS fixes; when accuracy is missing, allow this slack.
MIN_GPS_BUFFER_WHEN_NO_ACCURACY_METERS = 15.0
# Matches Client / ClientFieldVisit DecimalField(decimal_places=6)
COORDINATE_QUANTUM = Decimal("0.000001")

Number = Union[int, float, Decimal, str]


def quantize_coordinate(value: Number) -> Decimal:
    """
    Round WGS84 coordinate to 6 decimal places (~0.11 m) so values fit
    max_digits=9, decimal_places=6 and match browser geolocation payloads.
    """
    return Decimal(str(value)).quantize(COORDINATE_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_coordinate_optional(value: Optional[Number]) -> Optional[Decimal]:
    if value is None or (isinstance(value, str) and not str(value).strip()):
        return None
    return quantize_coordinate(value)


def haversine_distance_meters(
    lat1: Number, lon1: Number, lat2: Number, lon2: Number
) -> float:
    """Great-circle distance between two WGS84 points in meters."""
    r = 6371000.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def field_visit_max_allowed_distance_meters(
    employee_accuracy_meters: Optional[float] = None,
) -> float:
    """
    Required proximity is 10 m, plus up to 25 m from the device accuracy radius
    (two separate GPS fixes at create-lead vs field-visit are rarely identical).
    """
    if employee_accuracy_meters is None or employee_accuracy_meters <= 0:
        return FIELD_VISIT_MAX_DISTANCE_METERS + MIN_GPS_BUFFER_WHEN_NO_ACCURACY_METERS
    buffer = min(float(employee_accuracy_meters), GPS_ACCURACY_BUFFER_CAP_METERS)
    return FIELD_VISIT_MAX_DISTANCE_METERS + buffer

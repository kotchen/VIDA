import math


def validate_temperature(value, default: float = 0.1) -> float:
    if value is None or value == "":
        value = default

    try:
        temperature = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Temperature must be a number between 0 and 2") from exc

    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise ValueError("Temperature must be a finite number between 0 and 2")

    return temperature

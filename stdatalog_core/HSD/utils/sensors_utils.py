"""Sensor-related utility helpers for STDatalog.

Minimal module docstring to satisfy linters; functions/classes documented inline.
"""

# ******************************************************************************
# * @attention
# *
# * Copyright (c) 2022 STMicroelectronics.
# * All rights reserved.
# *
# * This software is licensed under terms that can be found in the LICENSE file
# * in the root directory of this software component.
# * If no LICENSE file comes with this software, it is provided AS-IS.
# *
# *
# ******************************************************************************
#
# Module purpose:
#   - Provide utilities for sensor type management and conversion.
# Design notes:
#   - Maintain a list of recognized sensor short codes.
#   - Provide conversion functions between short codes and extended names.


class SensorUtils:
    """
    Utility class for sensor type management.
    """

    # List of recognized sensor short codes used across the SDK and tools.
    # Keep this list in sync with SensorTypeConversion mappings below.
    sensor_types = [
        "acc",
        "mag",
        "gyro",
        "temp",
        "hum",
        "press",
        "mic",
        "mlc",
        "ispu",
        "qvar",
        "voc",
        "pow",
        "als",
        "tmos",
        "tof",
    ]


class SensorTypeConversion:
    """
    Conversion helpers between short sensor codes and extended names.

    Methods:
        - get_type_extended(short): short code -> extended name
        - get_type_short(name): extended/alias string -> short code

    Conventions:
        - Short codes (e.g., 'acc') are lower-case and typically 3–4 letters.
        - Extended names are title-cased human-readable strings.
        - For unknown inputs, get_type_extended returns "error".
        - For unknown descriptive inputs, get_type_short returns the first three
            letters of the first token (basic fallback).
    """

    @staticmethod
    def get_type_extended(check_type):
        """
        Convert a sensor type short form to its extended description.

        Input:
            check_type: str
                Short code identifying the sensor family (e.g., "acc", "gyro").

        Returns:
            str:
                Human-readable extended name (e.g., "Accelerometer").
                Returns "error" if the short code is not recognized.

        Examples:
            get_type_extended("acc") -> "Accelerometer"
            get_type_extended("tmos") -> "Infrared Sensor"
        """
        switcher = {
            "acc": "Accelerometer",
            "mag": "Magnetometer",
            "gyro": "Gyroscope",
            "temp": "Temperature",
            "hum": "Humidity",
            "press": "Pressure",
            "mic": "Microphone",
            "mlc": "Machine Learning Core",
            "ispu": "Intelligent Sensor Processing Unit",
            "qvar": "Electrostatic Sensor",
            "voc": "Volatile Organic Compound",
            "pow": "Power Meter",
            "als": "Ambient Light Sensor",
            "tmos": "Infrared Sensor",
            "tof": "Time of Flight Sensor",
        }
        return switcher.get(check_type, "error")

    @staticmethod
    def get_type_short(check_type):
        """
        Convert a sensor type description (extended name or alias) to its short code.

        Reduces branching and early returns by:
            - Normalizing input once
            - Using a single pattern map with tuples of keywords per sensor
            - Looping until first match, then returning
            - Falling back to a compact token when no match is found

        Input:
            check_type: str
                Human-readable description or alias (e.g., "Accelerometer", "ALS", "gyro").

        Returns:
            str:
                Lower-case short code (e.g., "acc", "als"). Empty string for empty input.

        Complexity:
            - Single return at the end (except early empty-input).
            - Fewer branches than multiple if-chains.
        """
        if not check_type:
            return ""

        stype = check_type.strip().lower()

        # Map of short codes to a tuple of keywords/aliases that indicate that type.
        # Order matters only for readability; first matching entry is returned.
        patterns = {
            "acc": ("accelerometer", "acc"),
            "mag": ("magnetometer", "mag"),
            "gyro": ("gyroscope", "gyro"),
            "temp": ("temperature", "temp"),
            "hum": ("humidity", "hum"),
            "press": ("pressure", "press"),
            "mic": ("microphone", "mic"),
            "mlc": ("machine learning core", "mlc"),
            "ispu": ("intelligent sensor processing unit", "ispu"),
            "qvar": ("electrostatic sensor", "qvar"),
            "voc": ("volatile organic compound", "voc"),
            "pow": ("power meter", "pow"),
            "als": ("ambient light sensor", "als"),
            "tmos": ("infrared sensor", "tmos"),
            "tof": ("time of flight sensor", "tof"),
        }

        # Try to find the first matching short code by keyword containment
        matched = None
        for short, keywords in patterns.items():
            # If any keyword is contained in the normalized input, we have a match
            if any(k in stype for k in keywords):
                matched = short
                break

        # Fallback: first three letters of the first word (keeps ID compact)
        if matched is None:
            tok = stype.split()[0][:3] if stype else ""
            matched = tok

        return matched

    @staticmethod
    def get_type_info_from_category(category, fallback_type=None):
        """
        Resolve sensor short and extended type names from a category-like identifier.

        This is useful for schema-driven flows that expose broader sensor categories
        such as ``ambient-light`` or ``power-monitor`` instead of HSD short codes.

        Input:
            category: str
                Category-like value coming from a schema or higher-level model.
            fallback_type: str | None
                Optional sensor type/name to resolve when the category is unknown.

        Returns:
            tuple[str, str]:
                ``(short_type, extended_type)``. Either element can be empty when no
                reliable mapping is available.
        """
        normalized = str(category or "").strip().lower().replace("_", "-").replace(" ", "-")

        category_map = {
            "accelerometer": ("acc", "Accelerometer"),
            "gyroscope": ("gyro", "Gyroscope"),
            "magnetometer": ("mag", "Magnetometer"),
            "pressure": ("press", "Pressure"),
            "temperature": ("temp", "Temperature"),
            "humidity": ("hum", "Humidity"),
            "microphone": ("mic", "Microphone"),
            "tof": ("tof", "Time of Flight Sensor"),
            "ambient-light": ("als", "Ambient Light Sensor"),
            "presence": ("", "Presence"),
            "power-monitor": ("pow", "Power Meter"),
        }

        if normalized in category_map:
            return category_map[normalized]

        raw_type = fallback_type if fallback_type is not None else category
        normalized_type = str(raw_type or "").strip().replace("_", " ").replace("-", " ")
        if not normalized_type:
            return "", ""

        short_type = SensorTypeConversion.get_type_short(normalized_type)
        if not short_type:
            return "", normalized_type.title()

        extended_type = SensorTypeConversion.get_type_extended(short_type)
        if extended_type == "error":
            return "", normalized_type.title()

        return short_type, extended_type

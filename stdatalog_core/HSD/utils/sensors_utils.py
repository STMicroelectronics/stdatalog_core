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

class SensorUtils:
    sensor_types = ["acc", "mag", "gyro", "temp", "hum", "press", "mic", "mlc", "ispu", "qvar", "voc", "pow", "als", "tmos", "tof"]

class SensorTypeConversion:

    @staticmethod
    def get_type_extended(check_type):
        switcher = {
            'acc': 'Accelerometer',
            'mag': 'Magnetometer',
            'gyro': 'Gyroscope',
            'temp': 'Temperature',
            'hum': 'Humidity',
            'press': 'Pressure',
            'mic': 'Microphone',
            'mlc': 'Machine Learning Core',
            'ispu': 'Intelligent Sensor Processing Unit',
            'qvar': 'Electrostatic Sensor',
            'voc': 'Volatile Organic Compound',
            'pow': 'Power Meter',
            'als': 'Ambient Light Sensor',
            'tmos': 'Infrared Sensor',
            'tof': 'Time of Flight Sensor'
        }
        return switcher.get(check_type, "error")
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.utils import PrintSession, SubscriptionUpdate

TZ = ZoneInfo("Europe/Oslo")
import anyio


class DataCaptureManager:
    def __init__(self):
        self.sensor_objects = {
            "temperature_sensor": None,  # replace?
            "temperature_fan": None,  # replace?
            "extruder": [],  # replace?
            "heater_bed": [],  # replace?
            "fan": ["speed"],
            "gcode_move": ["speed", "speed_factor"],
        }
        self._sensor_recordings = defaultdict(list)
        self._current_print_sesssion = None  # unused
        self.current_fan_speed = None
        self.current_move_speed = None
        self.current_speed_factor = None

    def reset(self, session: PrintSession):
        logger.debug("New data capture session started")
        self._sensor_recordings = defaultdict(list)
        self._current_print_session = session  # unused
        self.current_fan_speed = None
        self.current_move_speed = None
        self.current_speed_factor = None

    @property
    def current_nozzle_speed(self):
        if self.current_move_speed is None or self.current_speed_factor is None:
            return None
        return self.current_move_speed * self.current_speed_factor

    def get_frame_metadata(self):
        return {"fan_speed": self.current_fan_speed, "nozzle_speed": self.current_nozzle_speed}

    async def dump_data(self, file_path: Path):
        async with await anyio.open_file(file_path, "w", encoding="utf-8") as fp:
            await fp.write(
                json.dumps(self._sensor_recordings, ensure_ascii=False, indent=4)
            )

    def push_data(self, msg: SubscriptionUpdate, print_uuid):
        for sensor_name, fields in msg.changed_objects.items():
            if sensor_name == "fan" and "speed" in fields:
                self.current_fan_speed = fields["speed"]
            elif sensor_name == "gcode_move":
                if "speed" in fields:
                    self.current_move_speed = fields["speed"]
                if "speed_factor" in fields:
                    self.current_speed_factor = fields["speed_factor"]
            for obj in self.sensor_objects:
                if obj in sensor_name:
                    recording = {
                        "temperature": fields.get("temperature"),
                        "target": fields.get("target"),
                        "power": fields.get("power"),
                        "speed": fields.get("speed"),
                        "speed_factor": fields.get("speed_factor"),
                        "moonraker_time": msg.moonraker_time,
                        "id": str(print_uuid),
                        "time": datetime.now(TZ).strftime("%Y%m%d-%H%M%S"),
                    }
                    self._sensor_recordings[sensor_name].append(recording)

        return []

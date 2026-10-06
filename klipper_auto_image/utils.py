from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np


@dataclass
class Message:
    pass


@dataclass
class StateUpdate(Message):
    pass


@dataclass
class UnknownMessage(Message):
    pass


@dataclass
class QueryResponse(Message):
    changed_objects: dict


@dataclass
class SubscriptionUpdate(Message):
    changed_objects: dict
    moonraker_time: float

    def __str__(self):
        return f"Changed objects: {self.changed_objects}. Moonraker time: {self.moonraker_time}"


@dataclass
class PrinterStateUpdate(StateUpdate):
    klippy_state: str
    printjob_state: str

    def __str__(self):
        return (
            f"Klippy state: {self.klippy_state}. Printjob state: {self.printjob_state}"
        )


@dataclass
class PrintJobStateUpdate(StateUpdate):
    printjob_state: str

    def __str__(self):
        return f"Printjob state: {self.printjob_state}"


@dataclass
class KlippyStateUpdate(StateUpdate):
    klippy_state: str

    def __str__(self):
        return f"Klippy state: {self.klippy_state}"


@dataclass
class ServerInfo(Message):
    klippy_connected: bool
    klippy_state: str

    def __str__(self):
        return f"Klippy connected: {self.klippy_connected}. Klippy state: {self.klippy_state}"

    @property
    def ready(self):
        if self.klippy_connected and (self.klippy_state == "ready"):
            return True


@dataclass
class MoonrakerConnected(Message):
    connection_up: bool

    def __str__(self):
        return f"Moonraker connected: {self.connection_up}"


@dataclass(frozen=True)
class Action:
    pass


@dataclass(frozen=True)
class StartPrintSession(Action):
    session_id: UUID
    started_at: datetime


@dataclass(frozen=True)
class StartPostPrintSession(Action):
    started_at: datetime


@dataclass
class PrintSession:
    started_at: datetime
    current_layer: int
    post_printing_started_at: datetime
    moonraker_time: float
    print_session_id: UUID
    metadata_saved: bool
    data_out_dir: Path


@dataclass
class Frame:
    frame_id: UUID
    frame_timestamp: datetime
    image: np.ndarray
    path: Path


@dataclass
class FrameDetection:
    """Detection result"""

    # frame_id: str
    # detection_id: str
    # frame_timestamp: float
    # detections_timestamp: float
    # detection_image: np.ndarray
    # path: Path
    name: str
    confidence: float
    box: Box

    @classmethod
    def from_tuple_list(
        cls, detections: list[tuple[str, float, tuple[float, float, float, float]]]
    ) -> list[FrameDetection]:
        return [FrameDetection.from_tuple(d) for d in detections]

    @classmethod
    def from_tuple(
        cls, detection: tuple[str, float, tuple[float, float, float, float]]
    ) -> FrameDetection:
        box = Box.from_tuple(detection[2])
        return FrameDetection(detection[0], float(detection[1]), box)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrameDetection:
        return FrameDetection(data["name"], data["confidence"], Box(**data["box"]))


@dataclass
class Box:
    """Detection rect"""

    xc: float
    yc: float
    w: float
    h: float

    @classmethod
    def from_tuple(cls, box: tuple[float, float, float, float]) -> Box:
        return Box(xc=float(box[0]), yc=float(box[1]), w=float(box[2]), h=float(box[3]))

    def left(self) -> float:
        return self.xc - self.w * 0.5

    def right(self) -> float:
        return self.xc + self.w * 0.5

    def top(self) -> float:
        return self.yc - self.h * 0.5

    def bottom(self) -> float:
        return self.yc + self.h * 0.5

    def calc_iou(self, other: Box) -> float:
        """Calculates intersection over union ration which can be used to compare boxes"""
        al = self.left()
        ar = self.right()
        at = self.top()
        ab = self.bottom()

        bl = other.left()
        br = other.right()
        bt = other.top()
        bb = other.bottom()

        i_l = max(al, bl)
        i_r = min(ar, br)
        i_t = max(at, bt)
        i_b = min(ab, bb)

        o_l = min(al, bl)
        o_r = max(ar, br)
        o_t = min(at, bt)
        o_b = max(ab, bb)

        i_w = i_r - i_l
        i_h = i_b - i_t
        o_w = o_r - o_l
        o_h = o_b - o_t

        o_a = o_w * o_h
        if o_a <= 0.0:
            return 0.0
        return i_w * i_h / o_a

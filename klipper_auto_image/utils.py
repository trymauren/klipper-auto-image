import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# from typing import Any
from uuid import UUID

import numpy as np
import onnxruntime


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
    cam_name: str
    path: Path

    def get_opencv_image(self):
        return np.array(self.image)[:, :, ::-1].copy()


@dataclass
class Detection:
    pass


@dataclass
class FrameDetection(Detection):
    frame_id: UUID
    detection_id: UUID
    frame_timestamp: datetime
    detections_timestamp: datetime
    detection_image: np.ndarray
    cam_name: str
    path: Path


@dataclass
class EmptyFrameDetection(Detection):
    pass


class DefectDetector(ABC):
    @abstractmethod
    async def run(self):
        pass

    @abstractmethod
    async def next_result(self) -> Detection:
        pass


class ImageDefectDetector(DefectDetector):
    def __init__(self, session: onnxruntime.InferenceSession):
        super().__init__()
        self._inputs: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10_000)
        self._results: asyncio.Queue[FrameDetection] = asyncio.Queue(maxsize=10_000)
        self._session = session
        self._most_recent_result = None

    async def push_frame(self, frame: Frame):
        await self._inputs.put(frame)

    async def next_result(self):
        detection = await self._results.get()
        return detection

    def get_latest_result(self):
        return self._most_recent_result

    @abstractmethod
    def _defect_detection(self, frame: Frame) -> Detection:
        pass


class Model:
    pass


class ObicoModel(Model):
    pass

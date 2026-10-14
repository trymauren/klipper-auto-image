import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import cv2 as opencv

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.detector import Detector
from klipper_auto_image.utils import (
    Detection,
    EmptyFrameDetection,
    Frame,
    FrameDetection,
    ImageDefectDetector,
)

TZ = ZoneInfo("Europe/Oslo")
COLORS = {"spaghetti": (0, 0, 255), "stringing": (0, 165, 255), "detached": (0, 220, 0)}  # BGR


@dataclass
class YoloFrameDetection(FrameDetection):
    """FrameDetection that also carries the detections themselves"""

    detections: list[dict]


class YoloDetector(ImageDefectDetector):
    """
    Runs a YOLO model exported to ONNX (see the print-failure-detection repo) on camera frames.
    A failure is reported when a trigger class is seen in consecutive_frames frames in a row
    from the same camera. Report only: it never controls the printer.
    """

    def __init__(self, model_path, conf_threshold=0.5, trigger_classes=("spaghetti", "detached"),
                 consecutive_frames=3, cams=None, ignore_classes=()):
        self._detector = Detector(model_path, conf_threshold=conf_threshold, ignore_classes=ignore_classes)
        super().__init__(self._detector.session)
        self.trigger_classes = set(trigger_classes)
        self.consecutive_frames = consecutive_frames
        self.cams = set(cams) if cams else None  # None: all cameras
        self._hits = defaultdict(int)
        unknown = self.trigger_classes - set(self._detector.names.values())
        if unknown:
            logger.warning("Trigger classes %s are not in the model (%s)", unknown,
                           list(self._detector.names.values()))

    async def run(self):
        # Same loop as ImageDefectDetector.run, but inference runs in a worker thread so it
        # does not block the event loop (and the Moonraker connection) on the Pi
        while True:
            frame = await self._inputs.get()
            result = await asyncio.to_thread(self._defect_detection, frame)
            if isinstance(result, FrameDetection):
                self._most_recent_result = result.detection_frame
                await self._results.put(result)
            else:
                self._most_recent_result = frame

    def _defect_detection(self, frame: Frame) -> Detection:
        if self.cams is not None and frame.cam_name not in self.cams:
            return EmptyFrameDetection()
        try:
            detections = self._detector.detect(frame.to_pil())
        except Exception as exc:
            logger.error("Detection failed on camera %s: %s", frame.cam_name, exc)
            return EmptyFrameDetection()

        triggered = [d for d in detections if d["class"] in self.trigger_classes]
        self._hits[frame.cam_name] = self._hits[frame.cam_name] + 1 if triggered else 0
        if self._hits[frame.cam_name] == self.consecutive_frames:  # once per failure streak
            logger.warning(
                "Print failure detected on camera %s in %d frames in a row: %s",
                frame.cam_name,
                self.consecutive_frames,
                ", ".join(f"{d['class']} {d['confidence']:.2f}" for d in triggered),
            )

        if not detections:
            return EmptyFrameDetection()

        image = frame.frame.copy()
        for d in detections:
            x1, y1, x2, y2 = (int(v) for v in d["box"])
            color = COLORS.get(d["class"], (255, 255, 255))
            opencv.rectangle(image, (x1, y1), (x2, y2), color, 2)
            opencv.putText(image, f"{d['class']} {d['confidence']:.2f}", (x1, max(15, y1 - 5)),
                           opencv.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        return YoloFrameDetection(
            frame.frame_id,
            uuid4(),
            frame.frame_timestamp,
            datetime.now(TZ),
            image,
            frame.cam_name,
            frame.path,
            detections,
        )

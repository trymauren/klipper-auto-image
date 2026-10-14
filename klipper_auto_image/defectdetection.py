import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import cv2 as opencv
import numpy as np
import onnxruntime

# from klipper_auto_image.custom_logger import logger
from klipper_auto_image.utils import (
    Detection,
    EmptyFrameDetection,
    Frame,
    FrameDetection,
    ImageDefectDetector,
)

TZ = ZoneInfo("Europe/Oslo")


@dataclass
class ObicoDetection:
    """ObicoDetection result"""

    name: str
    confidence: float
    box: Box

    @classmethod
    def from_tuple_list(
        cls, detections: list[tuple[str, float, tuple[float, float, float, float]]]
    ) -> list[ObicoDetection]:
        return [ObicoDetection.from_tuple(d) for d in detections]

    @classmethod
    def from_tuple(
        cls, detection: tuple[str, float, tuple[float, float, float, float]]
    ) -> ObicoDetection:
        box = Box.from_tuple(detection[2])
        return ObicoDetection(detection[0], float(detection[1]), box)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObicoDetection:
        return ObicoDetection(data["name"], data["confidence"], Box(**data["box"]))


@dataclass
class Box:
    """ObicoDetection rect"""

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


class ObicoDetector(ImageDefectDetector):
    def __init__(
        self, session: onnxruntime.InferenceSession, thresh: float, nms: float
    ):
        self._inputs = asyncio.Queue(maxsize=10_000)
        self._results = asyncio.Queue(maxsize=10_000)
        self._session = session
        self.thresh = thresh
        self.nms = nms
        self._most_recent_result = None

    def _defect_detection(self, frame: Frame) -> Detection:
        image = frame.frame
        width = image.shape[1]
        height = image.shape[0]

        input_h = self._session.get_inputs()[0].shape[2]
        input_w = self._session.get_inputs()[0].shape[3]
        resized = opencv.resize(
            image, (input_w, input_h), interpolation=opencv.INTER_LINEAR
        )
        img_in = opencv.cvtColor(resized, opencv.COLOR_BGR2RGB)
        img_in = np.transpose(img_in, (2, 0, 1)).astype(np.float32)
        img_in = np.expand_dims(img_in, axis=0)
        img_in /= 255.0

        input_name = self._session.get_inputs()[0].name
        outputs = self._session.run(None, {input_name: img_in})
        detection_batch = self._post_processing(
            outputs, width, height, self.thresh, self.nms, "g"
        )
        detections = ObicoDetection.from_tuple_list(detection_batch[0])
        if len(detections) == 0:
            return EmptyFrameDetection()

        for d in detections:
            opencv.rectangle(
                image,
                (int(d.box.left()), int(d.box.top())),
                (int(d.box.right()), int(d.box.bottom())),
                (0, 255, 0),
                2,
            )

        frame_with_detections = FrameDetection(
            frame.frame_id,
            uuid4(),
            frame.frame_timestamp,
            datetime.now(TZ),
            image,
            frame.cam_name,
            frame.path,
        )
        return frame_with_detections

    def _post_processing(self, output, width, height, conf_thresh, nms_thresh, names):
        box_array = output[0]
        confs = output[1]

        if type(box_array).__name__ != "ndarray":
            box_array = box_array.cpu().detach().numpy()
            confs = confs.cpu().detach().numpy()

        num_classes = confs.shape[2]

        # [batch, num, 4]
        box_array = box_array[:, :, 0]

        # [batch, num, num_classes] --> [batch, num]
        max_conf = np.max(confs, axis=2)
        max_id = np.argmax(confs, axis=2)

        box_x1x1x2y2_to_xcycwh_scaled = lambda b: (
            float(0.5 * width * (b[0] + b[2])),
            float(0.5 * height * (b[1] + b[3])),
            float(width * (b[2] - b[0])),
            float(width * (b[3] - b[1])),
        )
        dets_batch = []
        for i in range(box_array.shape[0]):
            argwhere = max_conf[i] > conf_thresh
            l_box_array = box_array[i, argwhere, :]
            l_max_conf = max_conf[i, argwhere]
            l_max_id = max_id[i, argwhere]

            bboxes = []
            # nms for each class
            for j in range(num_classes):
                cls_argwhere = l_max_id == j
                ll_box_array = l_box_array[cls_argwhere, :]
                ll_max_conf = l_max_conf[cls_argwhere]
                ll_max_id = l_max_id[cls_argwhere]

                keep = self._nms_cpu(ll_box_array, ll_max_conf, nms_thresh)

                if keep.size > 0:
                    ll_box_array = ll_box_array[keep, :]
                    ll_max_conf = ll_max_conf[keep]
                    ll_max_id = ll_max_id[keep]

                    for k in range(ll_box_array.shape[0]):
                        bboxes.append(
                            [
                                ll_box_array[k, 0],
                                ll_box_array[k, 1],
                                ll_box_array[k, 2],
                                ll_box_array[k, 3],
                                ll_max_conf[k],
                                ll_max_conf[k],
                                ll_max_id[k],
                            ]
                        )

            detections = [
                (
                    names[b[6]],
                    float(b[4]),
                    box_x1x1x2y2_to_xcycwh_scaled((b[0], b[1], b[2], b[3])),
                )
                for b in bboxes
            ]
            dets_batch.append(detections)

        return dets_batch

    @staticmethod
    def _nms_cpu(boxes, confs, nms_thresh=0.5, min_mode=False):
        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]

        areas = (x2 - x1) * (y2 - y1)
        order = confs.argsort()[::-1]

        keep = []
        while order.size > 0:
            idx_self = order[0]
            idx_other = order[1:]

            keep.append(idx_self)

            xx1 = np.maximum(x1[idx_self], x1[idx_other])
            yy1 = np.maximum(y1[idx_self], y1[idx_other])
            xx2 = np.minimum(x2[idx_self], x2[idx_other])
            yy2 = np.minimum(y2[idx_self], y2[idx_other])

            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h

            if min_mode:
                over = inter / np.minimum(areas[order[0]], areas[order[1:]])
            else:
                over = inter / (areas[order[0]] + areas[order[1:]] - inter)

            inds = np.where(over <= nms_thresh)[0]
            order = order[inds + 1]

        return np.array(keep)

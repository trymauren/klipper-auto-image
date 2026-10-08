import asyncio
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import cv2 as opencv
import numpy as np
import onnxruntime
from PIL import Image

from klipper_auto_image.custom_logger import logger
from klipper_auto_image.utils import Detection, Frame, FrameDetection

TZ = ZoneInfo("Europe/Oslo")


class DefectDetector:
    def __init__(self):
        self._frames: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10_000)
        self._detections: asyncio.Queue[FrameDetection] = asyncio.Queue(maxsize=10_000)
        self._cfg = {
            "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
            "model_weights_path": "/home/pi/klipper-auto-image/weights/model-weights-5a6b1be1fa.onnx",
            "thresh": 0.4,
            "nms": 0.3,
        }
        self._prepare_model()
        self._most_recent_image = None

    async def run(self):
        while True:
            frame = await self._frames.get()
            frame_with_detections = self._defect_detection(frame)
            if frame_with_detections is not None:
                self._most_recent_image = Image.fromarray(
                    frame_with_detections.detection_image
                )
                await self._detections.put(frame_with_detections)
            else:
                self._most_recent_image = frame.image

    async def push_frame(self, frame: Frame):
        await self._frames.put(frame)

    async def next_detection(self):
        detection = await self._detections.get()
        return detection

    def get_latest_image(self):
        return self._most_recent_image

    def _prepare_model(self):
        providers = self._cfg["providers"]
        model_weights = self._cfg["model_weights_path"]
        self._onnx_session = onnxruntime.InferenceSession(
            model_weights, providers=providers
        )

    def _defect_detection(self, frame: Frame):
        image = frame.get_opencv_image()
        width = image.shape[1]
        height = image.shape[0]

        input_h = self._onnx_session.get_inputs()[0].shape[2]
        input_w = self._onnx_session.get_inputs()[0].shape[3]
        resized = opencv.resize(
            image, (input_w, input_h), interpolation=opencv.INTER_LINEAR
        )
        img_in = opencv.cvtColor(resized, opencv.COLOR_BGR2RGB)
        img_in = np.transpose(img_in, (2, 0, 1)).astype(np.float32)
        img_in = np.expand_dims(img_in, axis=0)
        img_in /= 255.0

        input_name = self._onnx_session.get_inputs()[0].name
        outputs = self._onnx_session.run(None, {input_name: img_in})
        detection_batch = self._post_processing(
            outputs, width, height, self._cfg["thresh"], self._cfg["nms"], "g"
        )
        detections = Detection.from_tuple_list(detection_batch[0])
        if len(detections) == 0:
            # logger.debug("No defects detected!")
            return None

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

    def _nms_cpu(self, boxes, confs, nms_thresh=0.5, min_mode=False):
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

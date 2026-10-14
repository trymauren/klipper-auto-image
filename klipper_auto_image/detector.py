"""
Runs a YOLO detection model exported to ONNX (see the print-failure-detection repo) on camera
snapshots. Needs the optional dependencies: uv sync --extra detect
"""
import ast

import numpy as np
import onnxruntime as ort
from PIL import Image


def nms(boxes, scores, iou_threshold):
    """Greedy non-maximum suppression. boxes are x1, y1, x2, y2. Returns indices to keep."""
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i, rest = order[0], order[1:]
        keep.append(i)
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


class Detector:
    # Defaults match Ultralytics' predict() so results are comparable to the training repo
    def __init__(self, model_path, conf_threshold=0.25, iou_threshold=0.7, ignore_classes=(),
                 num_threads=2, max_detections=300):
        options = ort.SessionOptions()
        options.intra_op_num_threads = num_threads  # leave CPU for Klipper and the cameras
        self.session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        model_input = self.session.get_inputs()[0]
        self.input_name = model_input.name
        self.input_h, self.input_w = model_input.shape[2], model_input.shape[3]
        # Ultralytics stores class names in the ONNX metadata, e.g. "{0: 'spaghetti', 1: 'stringing'}"
        self.names = ast.literal_eval(self.session.get_modelmeta().custom_metadata_map["names"])
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.max_detections = max_detections
        self.ignore_ids = [i for i, name in self.names.items() if name in ignore_classes]

    def preprocess(self, img):
        # Plain stretch resize rather than letterboxing, because the training images
        # were stretched to the model input size (Roboflow "Resize to 640x640 (Stretch)")
        resized = img.convert("RGB").resize((self.input_w, self.input_h), Image.BILINEAR)
        x = np.asarray(resized, dtype=np.float32) / 255.0
        return x.transpose(2, 0, 1)[np.newaxis]  # HWC -> NCHW

    def detect(self, img):
        """Returns a list of {"class", "confidence", "box": [x1, y1, x2, y2]} in image pixels."""
        output = self.session.run(None, {self.input_name: self.preprocess(img)})[0]
        preds = output[0].T  # (num_candidates, 4 + num_classes), boxes as cx, cy, w, h
        scores = preds[:, 4:]
        class_ids = scores.argmax(axis=1)
        confs = scores[np.arange(len(scores)), class_ids]

        keep = confs >= self.conf_threshold
        if self.ignore_ids:
            keep &= ~np.isin(class_ids, self.ignore_ids)
        cx, cy, w, h = preds[keep, :4].T
        confs, class_ids = confs[keep], class_ids[keep]

        sx, sy = img.width / self.input_w, img.height / self.input_h
        boxes = np.stack([(cx - w / 2) * sx, (cy - h / 2) * sy, (cx + w / 2) * sx, (cy + h / 2) * sy], axis=1)

        # Per-class NMS: offset each class into its own coordinate range so boxes of
        # different classes never suppress each other
        offsets = class_ids[:, None] * float(max(img.width, img.height) + 1)
        kept = nms(boxes + offsets, confs, self.iou_threshold)[: self.max_detections]

        return [
            {
                "class": self.names[int(class_ids[i])],
                "confidence": round(float(confs[i]), 3),
                "box": [round(float(v), 1) for v in boxes[i]],
            }
            for i in kept
        ]

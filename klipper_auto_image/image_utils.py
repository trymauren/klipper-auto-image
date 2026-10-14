import cv2 as opencv
import numpy as np


class ImageEncodeError(ValueError):
    """Raised when an image cannot be encoded."""


class ImageDecodeError(ValueError):
    """Raised when an image cannot be encoded."""


def decode_bytes_to_bgr_array(data: bytes) -> np.ndarray:
    if not data:
        raise ImageDecodeError("empty image data")
    try:
        img = opencv.imdecode(np.frombuffer(data, np.uint8), opencv.IMREAD_COLOR)
    except opencv.error as e:
        raise ImageDecodeError("jpeg encode failed") from e
    if img is None:
        raise ImageDecodeError("not a decodable image")
    return np.asarray(img)


def encode_bgr_array_to_jpeg_bytes(img: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = opencv.imencode(".jpg", img, [opencv.IMWRITE_JPEG_QUALITY, quality])
    try:
        ok, buf = opencv.imencode(".jpg", img, [opencv.IMWRITE_JPEG_QUALITY, quality])
    except opencv.error as e:
        raise ImageEncodeError("jpeg encode failed") from e
    if not ok:
        raise ImageEncodeError("jpeg encode failed")
    return buf.tobytes()

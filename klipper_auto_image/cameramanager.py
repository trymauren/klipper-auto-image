import asyncio
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.utils import Frame, PrintSession

TZ = ZoneInfo("Europe/Oslo")
from collections.abc import Callable


class CameraManager:
    def __init__(self):
        self._cams = []
        self._cam_names = []
        self._frame_index = 0
        self._fps = 1
        self._frame_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10)

    def configure(self, print_session: PrintSession, cams: list[dict], fps: int):
        self._cams = []
        self._cam_names = []
        self._frame_index = 0
        self._uuid = print_session.print_session_id
        self._fps = fps
        self._data_out_dir = print_session.data_out_dir
        self._register_cameras(cams)
        self._setup_directories()
        self._reset_frame_queue()

    async def run(self, ready: Callable[..., bool]):
        next_shot = time.monotonic()
        while True:
            next_shot += 1 / self._fps
            await asyncio.sleep(max(0.0, next_shot - time.monotonic()))
            self._frame_index += 1

            if ready():
                # t = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
                # logger.info("New image should be captured now: %s", t)
                images = await asyncio.to_thread(self._capture_images)
                for cam_name, image in images.items():
                    img = Frame(
                        self._uuid,
                        datetime.now(TZ),
                        image,
                        cam_name,
                        Path("dummypath"),
                    )
                    await self._frame_queue.put(img)

    async def next_frame(self):
        frame = await self._frame_queue.get()
        return frame

    def _register_cameras(self, cams: list[dict]):
        logger.info("Will capture images from the following cameras:")
        for requested_cam in cams:
            self._cams.append(requested_cam["uri"])
            self._cam_names.append(requested_cam["name"])
            logger.info("%s (%s)", requested_cam["uri"], requested_cam["name"])

    def _setup_directories(self):
        for name in self._cam_names:
            cam_dir = self._data_out_dir / name
            cam_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("Created directory %s", cam_dir)

    def _reset_frame_queue(self):
        while True:
            try:
                self._frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _capture_images(self):
        images = {}
        for cam, name in zip(self._cams, self._cam_names):
            t = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
            path = (
                self._data_out_dir
                / name
                / f"frame_{self._frame_index}_id_{self._uuid}_time_{t}.jpg"
            )
            try:
                response = requests.get(cam, timeout=5)
                response.raise_for_status()
                data = response.content
                with Image.open(BytesIO(data)) as img:
                    img.load()
                    img.save(path)
                    images[name] = img

                logger.debug("Captured %s", path)

            # except OSError:
            #     logger.debug("Pillow failed when capturing from %s", cam)

            except requests.exceptions.ConnectionError:
                logger.debug(
                    "Camera %s is unavailable at %s",
                    name,
                    cam,
                )

            except requests.exceptions.Timeout:
                logger.debug(
                    "Timed out getting snapshot from camera %s (%s)",
                    name,
                    cam,
                )

            except requests.exceptions.HTTPError as exc:
                logger.debug(
                    "Camera %s (%s) returned HTTP %s",
                    name,
                    cam,
                    exc.response.status_code if exc.response is not None else "unknown",
                )
        return images

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import requests

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.utils import Frame, PrintSession

TZ = ZoneInfo("Europe/Oslo")


@dataclass(frozen=True)
class SnapshotArgs:
    uuid: UUID
    cams: tuple[str]
    cam_names: tuple[str]
    data_out_dir: Path
    frame_index: int


from klipper_auto_image.image_utils import decode_bytes_to_bgr_array


class CameraManager:
    def __init__(self):
        self._cams = []
        self._cam_names = []
        self._frame_index = 0
        self._fps = 1
        self._configured = False

    def configure(self, print_session: PrintSession, cams: list[dict], fps: int):
        self._cams = []
        self._cam_names = []
        self._frame_index = 0
        self._uuid = print_session.print_session_id
        self._fps = fps
        self._data_out_dir = print_session.data_out_dir
        self._register_cameras(cams)
        self._setup_directories()
        self._configured = True

    async def run(self, ready: Callable[..., bool], metadata_provider: Callable[..., dict] = dict):
        next_shot = time.monotonic()
        while True:
            next_shot += 1 / self._fps
            await asyncio.sleep(max(0.0, next_shot - time.monotonic()))

            if ready() and self._configured:
                # t = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
                # logger.info("New image should be captured now: %s", t)
                self._frame_index += 1

                # In case self._capture_images() is still running in thread while
                # configure() is called
                snapshot_args = SnapshotArgs(
                    self._uuid,
                    tuple(self._cams),
                    tuple(self._cam_names),
                    self._data_out_dir,
                    self._frame_index,
                )
                await asyncio.to_thread(self._capture_images, snapshot_args, metadata_provider())

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

    def _capture_images(self, snapshot_args: SnapshotArgs, metadata: dict):
        uuid = snapshot_args.uuid
        cams = snapshot_args.cams
        cam_names = snapshot_args.cam_names
        frame_index = snapshot_args.frame_index
        data_out_dir = snapshot_args.data_out_dir

        for cam, name in zip(cams, cam_names):
            t = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
            path = data_out_dir / name / f"frame_{frame_index}_id_{uuid}_time_{t}.jpg"
            try:
                response = requests.get(cam, timeout=5)
                response.raise_for_status()
                data = response.content
                path.write_bytes(data)

                sidecar = {
                    "frame_index": frame_index,
                    "camera": name,
                    "print_id": str(uuid),
                    "time": t,
                    **metadata,
                }
                path.with_suffix(".json").write_text(
                    json.dumps(sidecar, ensure_ascii=False, indent=4), encoding="utf-8"
                )

                logger.debug("Captured %s", path)

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

            except OSError:
                logger.debug("Pillow failed when capturing from %s", cam)


class CameraMonitor(CameraManager):
    def __init__(self, maxsize=10):
        super().__init__()
        self._frame_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=maxsize)

    def configure(self, print_session: PrintSession, cams: list[dict], fps: int):
        super().configure(print_session, cams, fps)
        self._reset_frame_queue()

    async def run(self, ready: Callable[..., bool], metadata_provider: Callable[..., dict] = dict):
        next_shot = time.monotonic()
        while True:
            next_shot += 1 / self._fps
            await asyncio.sleep(max(0.0, next_shot - time.monotonic()))

            if ready() and self._configured:
                # t = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
                # logger.info("New image should be captured now: %s", t)
                self._frame_index += 1

                # In case self._capture_images() is still running in thread while
                # configure() is called
                snapshot_args = SnapshotArgs(
                    self._uuid,
                    tuple(self._cams),
                    tuple(self._cam_names),
                    self._data_out_dir,
                    self._frame_index,
                )
                images = await asyncio.to_thread(
                    self._capture_and_queue_images, snapshot_args, metadata_provider()
                )
                for cam_name, image in images.items():
                    frame = Frame(
                        self._uuid,
                        datetime.now(TZ),
                        decode_bytes_to_bgr_array(image),
                        cam_name,
                        Path("dummypath"),
                    )
                    await self._frame_queue.put(frame)

    async def next_frame(self):
        frame = await self._frame_queue.get()
        return frame

    def _reset_frame_queue(self):
        while True:
            try:
                self._frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _capture_and_queue_images(self, snapshot_args: SnapshotArgs, metadata: dict):
        uuid = snapshot_args.uuid
        cams = snapshot_args.cams
        cam_names = snapshot_args.cam_names
        frame_index = snapshot_args.frame_index
        data_out_dir = snapshot_args.data_out_dir
        images = {}
        for cam, name in zip(cams, cam_names):
            t = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
            path = data_out_dir / name / f"frame_{frame_index}_id_{uuid}_time_{t}.jpg"
            try:
                response = requests.get(cam, timeout=5)
                response.raise_for_status()
                data = response.content
                images[name] = data
                path.write_bytes(data)

                sidecar = {
                    "frame_index": frame_index,
                    "camera": name,
                    "print_id": str(uuid),
                    "time": t,
                    **metadata,
                }
                path.with_suffix(".json").write_text(
                    json.dumps(sidecar, ensure_ascii=False, indent=4), encoding="utf-8"
                )

                logger.debug("Captured %s", path)

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

            except OSError:
                logger.debug("Pillow failed when capturing from %s", cam)

        return images

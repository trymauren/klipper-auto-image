from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.cameramanager import CameraManager
from klipper_auto_image.controller import Controller
from klipper_auto_image.datacapturemanager import DataCaptureManager
from klipper_auto_image.defectdetection import DefectDetector
from klipper_auto_image.moonrakerconnector import MoonrakerConnector
from klipper_auto_image.parsing_utils import get_config
from klipper_auto_image.statemachine import PrinterStateMachine

# @dataclass
# class LatestFrame:
#     jpeg_bytes: bytes
#     processed_at: float
#     sequence: int


# class FrameStore:
#     def __init__(self):
#         self._frames: dict[str, LatestFrame] = {}
#         self._lock = asyncio.Lock()
#
#     async def set_frame(self, camera_id, image) -> None:
#         async with self._lock:
#             self._frames[camera_id] = image
#
#     async def get_frame(self, camera_id: str):
#         async with self._lock:
#             return self._frames.get(camera_id)
#
#
# store = FrameStore()


def run():
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = app.state.cfg
        printer_state_machine = PrinterStateMachine(cfg)
        data_capture_manager = DataCaptureManager()
        moonraker_connector = MoonrakerConnector(cfg)
        camera_manager = CameraManager()
        defect_detector = DefectDetector()
        controller = Controller(
            cfg=cfg,
            moonraker_connector=moonraker_connector,
            printer_state_machine=printer_state_machine,
            data_capture_manager=data_capture_manager,
            camera_manager=camera_manager,
            defect_detector=defect_detector,
        )
        app.state.detections = defect_detector

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(moonraker_connector.run()),
                tg.create_task(controller.run()),
                tg.create_task(camera_manager.run(controller.should_gather_data)),
                tg.create_task(defect_detector.run()),
            ]
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()

    cfg = get_config()
    logger.setup_logging(log_level=cfg.loglevel)
    app = FastAPI(lifespan=lifespan)
    app.state.cfg = cfg

    @app.get("/monitor/frame")
    async def live_image():
        frame = app.state.detections.get_latest_image()
        buffer = BytesIO()
        frame.save(buffer, format="JPEG", quality=95)
        logger.info(frame)
        if frame is None:
            raise HTTPException(status_code=404, detail="No frame yet")

        return Response(
            content=buffer.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8009,
        # log_config=None,  # TODO!
    )
    # @app.get("/live/{camera_id}/status")
    # async def live_status(camera_id: str):
    #     frame = await store.get_frame(camera_id)
    #     if frame is None:
    #         return JSONResponse(
    #             {"camera_id": camera_id, "state": "starting"},
    #             headers={"Cache-Control": "no-store"},
    #         )
    #
    #     age_seconds = time.time() - frame.processed_at
    #     state = "live" if age_seconds < 2.0 else "stale"
    #
    #     return JSONResponse(
    #         {
    #             "camera_id": camera_id,
    #             "state": state,
    #             "age_seconds": round(age_seconds, 3),
    #             "sequence": frame.sequence,
    #             "processed_at": frame.processed_at,
    #         },
    #         headers={"Cache-Control": "no-store"},
    #     )

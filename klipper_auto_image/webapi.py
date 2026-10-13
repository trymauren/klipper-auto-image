from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import onnxruntime
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.cameramanager import CameraMonitor
from klipper_auto_image.controller import Controller
from klipper_auto_image.datacapturemanager import DataCaptureManager
from klipper_auto_image.image_utils import ImageEncodeError
from klipper_auto_image.moonrakerconnector import MoonrakerConnector
from klipper_auto_image.parsing_utils import get_config
from klipper_auto_image.statemachine import PrinterStateMachine

from .defectdetection import ObicoDetector


def run():
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = app.state.cfg
        printer_state_machine = PrinterStateMachine(cfg)
        data_capture_manager = DataCaptureManager()
        moonraker_connector = MoonrakerConnector(cfg)
        camera_manager = CameraMonitor()

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        weights = "/home/pi/klipper-auto-image/weights/model-weights-5a6b1be1fa.onnx"
        onnx_session = onnxruntime.InferenceSession(weights, providers=providers)

        defect_detector = ObicoDetector(session=onnx_session, thresh=0.4, nms=0.3)
        controller = Controller(
            cfg=cfg,
            moonraker_connector=moonraker_connector,
            printer_state_machine=printer_state_machine,
            data_capture_manager=data_capture_manager,
            camera_manager=camera_manager,
            defect_detectors=[defect_detector],
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
        frame = app.state.detections.get_latest_result()
        if frame is None:
            raise HTTPException(status_code=404, detail="No frame yet")
        try:
            bytes_ = frame.to_jpeg()
        except ImageEncodeError:
            raise HTTPException(status_code=500, detail="JPEG encode failed")

        return Response(
            content=bytes_,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8009,
        # log_config=None,  # TODO!
    )

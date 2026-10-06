#!/usr/bin/env python
import asyncio

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.cameramanager import CameraManager
from klipper_auto_image.controller import Controller
from klipper_auto_image.datacapturemanager import DataCaptureManager
from klipper_auto_image.defectdetection import DefectDetector
from klipper_auto_image.moonrakerconnector import MoonrakerConnector
from klipper_auto_image.parsing_utils import get_config
from klipper_auto_image.statemachine import PrinterStateMachine


async def _run(cfg) -> None:
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

    async with asyncio.TaskGroup() as tg:
        tg.create_task(moonraker_connector.run())
        tg.create_task(controller.run())
        tg.create_task(camera_manager.run(controller.should_gather_data))
        tg.create_task(defect_detector.run())


def run() -> None:
    cfg = get_config()
    logger.setup_logging(log_level=cfg.loglevel)
    asyncio.run(_run(cfg))


if __name__ == "__main__":
    run()

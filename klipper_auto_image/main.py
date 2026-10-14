#!/usr/bin/env python
import asyncio

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.cameramanager import CameraManager, CameraMonitor
from klipper_auto_image.controller import Controller
from klipper_auto_image.datacapturemanager import DataCaptureManager
from klipper_auto_image.moonrakerconnector import MoonrakerConnector
from klipper_auto_image.parsing_utils import get_config
from klipper_auto_image.statemachine import PrinterStateMachine


def build_defect_detectors(cfg) -> list:
    """YOLO failure detection, enabled by a [detect] section with a model in the config"""
    detect_cfg = getattr(cfg, "detect", None)
    if not detect_cfg or not detect_cfg.get("model"):
        return []
    from klipper_auto_image.yolodetector import YoloDetector

    try:
        detector = YoloDetector(
            detect_cfg["model"],
            conf_threshold=detect_cfg.get("conf_threshold", 0.5),
            trigger_classes=detect_cfg.get("trigger_classes", ["spaghetti", "detached"]),
            consecutive_frames=detect_cfg.get("consecutive_frames", 3),
            cams=detect_cfg.get("cams"),
            ignore_classes=detect_cfg.get("ignore_classes", []),
        )
    except Exception as exc:  # capture keeps working without detection
        logger.error("Could not load detection model %s: %s", detect_cfg["model"], exc)
        return []
    logger.info("Running failure detection with %s", detect_cfg["model"])
    return [detector]


async def _run(cfg) -> None:
    printer_state_machine = PrinterStateMachine(cfg)
    data_capture_manager = DataCaptureManager()
    moonraker_connector = MoonrakerConnector(cfg)
    defect_detectors = build_defect_detectors(cfg)
    # CameraMonitor also queues the captured frames for the detectors
    camera_manager = CameraMonitor() if defect_detectors else CameraManager()
    controller = Controller(
        cfg=cfg,
        moonraker_connector=moonraker_connector,
        printer_state_machine=printer_state_machine,
        data_capture_manager=data_capture_manager,
        camera_manager=camera_manager,
        defect_detectors=defect_detectors,
    )

    async with asyncio.TaskGroup() as tg:
        tg.create_task(moonraker_connector.run())
        tg.create_task(controller.run())
        tg.create_task(camera_manager.run(controller.should_gather_data))
        for defect_detector in defect_detectors:
            tg.create_task(defect_detector.run())


def run() -> None:
    cfg = get_config()
    logger.setup_logging(log_level=cfg.loglevel)
    asyncio.run(_run(cfg))


if __name__ == "__main__":
    run()

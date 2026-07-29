import logging
import logging.handlers
import os
import sys
from functools import partial
from pathlib import Path

from rich.logging import RichHandler

logger = logging.getLogger("klipper-auto-image")
picamera2_logger = logging.getLogger("picamera2")


def setup_logging(log_level=logging.INFO):
    log_path = Path(os.path.expanduser("~/printer_data/logs"))
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / "log"
    log_file_formatter = logging.Formatter(
        "%(levelname)s %(name)s [%(asctime)s] %(message)s"
    )
    stream_formatter = logging.Formatter("%(name)s: %(message)s")

    # File logging
    filehandler = logging.handlers.RotatingFileHandler(
        log_file,
        mode="a",
        encoding="utf-8",
        maxBytes=1_048_576,  # MiB
        backupCount=3,  # 3 log files of maximum maxBytes are kept
    )
    filehandler.setFormatter(log_file_formatter)

    # Stdout logging
    # streamhandler = logging.StreamHandler(sys.stdout)
    streamhandler = RichHandler()
    streamhandler.setFormatter(stream_formatter)

    logger.addHandler(streamhandler)
    logger.addHandler(filehandler)
    logger.setLevel(log_level)

    picamera2_logger.addHandler(streamhandler)
    picamera2_logger.addHandler(filehandler)
    picamera2_logger.setLevel(log_level)

    log_startup()


def log_startup():
    logger.info("### klipper-auto-image ###")
    logger.info("Startup ...")


info = partial(logger.info)
debug = partial(logger.debug)
warning = partial(logger.warning)
error = partial(logger.error)
critical = partial(logger.critical)

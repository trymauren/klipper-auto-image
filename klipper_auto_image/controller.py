# import asyncio
import json

# from collections import defaultdict
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import anyio

# from pathlib import Path # anyio also has path
from klipper_auto_image import custom_logger as logger
from klipper_auto_image.cameramanager import CameraManager
from klipper_auto_image.datacapturemanager import DataCaptureManager
from klipper_auto_image.moonrakerconnector import MoonrakerConnector
from klipper_auto_image.statemachine import PrinterStateMachine
from klipper_auto_image.utils import (
    MoonrakerConnected,
    # PrinterStateUpdate,
    PrintJobStateUpdate,
    PrintSession,
    ServerInfo,
    StartPostPrintSession,
    StartPrintSession,
    StateUpdate,
    SubscriptionUpdate,
)

TZ = ZoneInfo("Europe/Oslo")


class Controller:
    def __init__(
        self,
        printer_state_machine: PrinterStateMachine,
        data_capture_manager: DataCaptureManager,
        moonraker_connector: MoonrakerConnector,
        camera_manager: CameraManager,
        cfg,
    ):
        self._data_capture_manager = data_capture_manager
        self._printer_state_machine = printer_state_machine
        self._moonraker_connector = moonraker_connector
        self._camera_manager = camera_manager
        self._cfg = cfg
        # self._print_session: PrintSession
        self._print_session = PrintSession(
            started_at=datetime.now(TZ),
            current_layer=99999999,
            post_printing_started_at=datetime.now(TZ),
            moonraker_time=99999999,
            print_session_id=99999999,
            metadata_saved=False,
            data_out_dir=self._cfg.output_dir,
        )

    @property
    def print_uuid(self):
        return self._print_session.print_session_id

    async def run(self):
        while True:
            await self._moonraker_connector.wait_until_connected()
            try:
                await self._moonraker_connector.subscribe(
                    self._data_capture_manager.sensor_objects
                )
                logger.debug("Controller has subscribed to moonraker")
            except ConnectionError:
                logger.debug("Failed to subscribe. Connection not ready.")
                continue

            while True:
                msg = await self._moonraker_connector.next_msg()
                if isinstance(msg, StateUpdate):
                    logger.debug("New status update: %s", msg)
                    actions = self._printer_state_machine.apply(msg)
                    await self._handle_actions(actions)
                elif isinstance(msg, ServerInfo):
                    if not msg.ready:
                        logger.debug("msg not ready: %s", msg)
                        break
                    logger.debug("New ServerInfo msg: %s", msg)

                elif isinstance(msg, MoonrakerConnected):
                    if not msg.connection_up:
                        logger.debug("msg connection down: %s", msg)
                        break
                    logger.debug("New MoonrakerConnected msg: %s", msg)

                elif isinstance(msg, SubscriptionUpdate):
                    actions = await self._handle_subscription_msg(msg)
                    await self._handle_actions(actions)

    async def _handle_subscription_msg(self, msg: SubscriptionUpdate):
        # logger.debug("New subscription msg: %s", msg)
        actions = []
        stats = msg.changed_objects.get("print_stats", {})
        if "info" in stats:
            self._print_session.current_layer = stats["info"]["current_layer"] or 0

        if not self.should_gather_data():
            return actions

        post_printing = self._printer_state_machine.post_printing
        if post_printing:
            start = self._print_session.post_printing_started_at
            elapsed_time = (datetime.now(TZ) - start).total_seconds()
            post_printing_done = elapsed_time >= self._cfg.post_printing_time
            if post_printing_done:
                logger.debug("Post printing data capture finished")
                update = PrintJobStateUpdate(printjob_state="data_capture_finished")
                actions = self._printer_state_machine.apply(update)
                await self._end_print_session()

        actions = self._data_capture_manager.push_data(msg, self.print_uuid)
        return actions

    async def _handle_actions(self, actions):
        for action in actions:
            if isinstance(action, StartPrintSession):
                await self._start_print_session(action.session_id, action.started_at)
            elif isinstance(action, StartPostPrintSession):
                self._start_post_print_session(action.started_at)

    async def _start_print_session(self, session_id: UUID, started_at: datetime):
        logger.debug("Preparing new print session")
        output_dir = self._cfg.output_dir / datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        self._print_session = PrintSession(
            started_at=datetime.now(TZ),
            current_layer=0,
            post_printing_started_at=started_at,
            moonraker_time=99999999,
            print_session_id=session_id,
            metadata_saved=False,
            data_out_dir=output_dir,
        )
        self._data_capture_manager.reset(self._print_session)
        self._camera_manager.configure(
            self._print_session, self._cfg.cam, self._cfg.fps
        )
        await self.update_metadata()

    def _start_post_print_session(self, started_at: datetime):
        logger.debug("Started post printing data capture")
        self._print_session.post_printing_started_at = started_at

    async def _end_print_session(self):
        logger.debug("Ending print session")
        file_path = self._print_session.data_out_dir / "sensor_data.json"
        await self._data_capture_manager.dump_data(file_path)

    async def update_metadata(self):
        method = "server.history.list"
        params = {"limit": 1, "order": "desc"}
        msg = await self._moonraker_connector.query(method, params)
        jobs = msg.get("result", {}).get("jobs", [])
        metadata = jobs[0] if jobs else None
        if metadata is not None:
            logger.debug("Metadata: %s", metadata)
            file_dir = self._print_session.data_out_dir
            file_dir.mkdir(parents=True, exist_ok=True)
            file_path = file_dir / "metadata.json"
            async with await anyio.open_file(file_path, "w", encoding="utf-8") as fp:
                await fp.write(json.dumps(metadata, ensure_ascii=False, indent=4))
            self._print_session.metadata_saved = True
            logger.debug("Saved metadata to %s", file_path)

    def should_gather_data(self):
        return bool(
            self._printer_state_machine.ready() and self._print_session.current_layer
        )

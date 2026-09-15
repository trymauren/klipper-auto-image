#!/usr/bin/env python
import asyncio
import json
import logging
import time
from pathlib import Path
import uuid
import websockets
from collections import defaultdict
from klipper_auto_image import custom_logger as logger
from klipper_auto_image import parsing_utils
from datetime import datetime

import requests
from PIL import Image
from io import BytesIO

CHECK_MOONRAKER_LOG = "Moonraker might not be running. If this error persists, check the Moonraker logs."
UNKNOWN_MSG = "Unknown message in the websocket response!"
SUCCESSFUL_SUBSCRIPTION = "Successfully subscribed to printer state event"
RE_SUBSCRIBE_MOONRAKER = "Resubscribing to Moonraker printer status caused by an unregistered Moonraker restart"


class AutoImager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.printjob_state = "websocket_disconnected" # not a Moonraker response value
        self.klippy_state = "klippy_disconnected" # not a Moonraker response value
        self.websocket_disconnect_count = 0
        self.post_printing_count = 0
        self.current_layer = 0
        self.total_layer = 0
        self._frame_index = 0 
        self.moonraker_time = 999999999
        self.print_session_id = uuid.uuid4()
        self.cams = []
        self.cam_names = []
        self.current_temperatures = defaultdict(list)
        self.metadata_saved = True
        self.out_dir = self.cfg.output_dir
        self.register_cameras()

    
    @property
    def uuid_str(self):
        return str(self.print_session_id)

    @property
    def time_stamp(self):
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    def register_cameras(self):
        logger.info("Will capture images from the following cameras:")
        for requested_cam in self.cfg.cam:
            self.cams.append(requested_cam["uri"])
            self.cam_names.append(requested_cam["name"])
            logger.info("%s (%s)", requested_cam["uri"], requested_cam["name"])
    
    def new_print_session(self):
        logger.info("Preparing new print session")
        self.out_dir = self.cfg.output_dir / self.time_stamp 
        for name in self.cam_names:
            cam_dir = self.out_dir / name
            cam_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("Created directory %s", cam_dir)
        self._frame_index = 0
        self.print_session_id = uuid.uuid4()
        self.metadata_saved = False
        self.post_printing_count = 0
        self.current_temperatures = defaultdict(list)

    def cleanup_after_print_session(self):
        logger.info("Cleaning up after print session")
        self.post_printing_count = 0
        self.current_layer = 0
        self.total_layer = 0
        if self.current_temperatures:
            file_path = self.out_dir / "sensor_data.json"
            with open(file_path, 'w', encoding='utf-8') as fp:
                json.dump(self.current_temperatures, fp, ensure_ascii=False, indent=4)
            logger.info("Wrote temperature data to %s", file_path)

    def update_printjob_state(self, new_state):
        if new_state == self.printjob_state:
            return

        old_state = self.printjob_state
        
        if new_state == "printing" and old_state != "printing":
            self.new_print_session()
 
        elif new_state in ["cancelled", "error", "complete"] and old_state == "printing":
            self.post_printing_count = 1
        
        self.printjob_state = new_state

    def record_temperatures(self, changed_stats, time_):
        if not self.should_shoot():
            return
        objects = ["temperature_sensor", "temperature_fan", "extruder", "heater_bed"]
        for sensor_name, fields in changed_stats.items():
            for obj in objects:
                if obj in sensor_name:
                    temp = fields.get("temperature")
                    target = fields.get("target")
                    power = fields.get("power")
                    recording = {"temperature": temp, "target": target, "power": power, 
                                 "moonraker_time": time_, "id": self.uuid_str,
                                 "time": self.time_stamp}
                    # logger.error("%s, %s", sensor_name, recording)
                    self.current_temperatures[sensor_name].append(recording)

    def should_shoot(self):
        """
        Currently returns True if the printer is printing layer [1, ... n] or
        if the printer has just finished printing
        """
        
        if self.klippy_state == "ready":
            if (self.printjob_state == "printing") and (self.current_layer > 0):
                return True

        elif self.klippy_state in ["error", "shutdown", "startup", "disconnected"]:
            return False

        elif self.klippy_state == "klippy_disconnected":
            logger.info("Session not yet established. Waiting for connection.")
            return False

        if self.post_printing_count:
            if self.post_printing_count > 5:
                self.cleanup_after_print_session()
                return False
            return True

        return False

    def _capture_images(self):
        for cam, name in zip(self.cams, self.cam_names):
            path = self.out_dir / name / f"frame_{self._frame_index:06d}_moonraker_time_{self.moonraker_time}_id_{self.uuid_str}_time_{self.time_stamp}.jpg"
            response = requests.get(cam, timeout=10)
            img = Image.open(BytesIO(response.content))
            img.save(path)
            logger.debug("Captured %s", path)

    async def capture_loop(self):
        """
        Calls the image capturing function according to specified frequency.
        The call is offloaded to a thread to avoid blocking.
        """
        next_shot = time.monotonic()

        while True:

            next_shot += 1 / self.cfg.fps
            await asyncio.sleep(max(0.0, next_shot - time.monotonic()))
            self._frame_index += 1

            if not self.should_shoot():
                disconnected = int(self.printjob_state == "websocket_disconnected")
                self.websocket_disconnect_count += disconnected
                continue
            
            await asyncio.to_thread(self._capture_images)
            self.websocket_disconnect_count = 0

            if self.post_printing_count:
                # Now in post-printing mode, capturing some frames also after printer is finished/stopped
                self.post_printing_count += 1

    async def subscribe_and_monitor(self, ws):
        """
        Subscribes to Moonraker printer status updates and updates self.printjob_state
        """

        # MOVE THE AVAILABLE OBJECTS QUERY TO AFTER KLIPPER (KLIPPY?) IS READY
        # https://moonraker.readthedocs.io/en/latest/printer_objects/

        if not self.klippy_state == "ready":
            logger.error("Monitor function was invoked before klippy state is ready")
            return
 
        payload = {"jsonrpc": "2.0", "method": "printer.objects.list", "id": 1454}
        available_objects = []
        await ws.send(json.dumps(payload)) 
        while True:
            msg = json.loads(await ws.recv()) 
            if (msg.get("id") == 1454) and (msg.get("result") is not None):
                # Check which objects are available
                available_objects = msg["result"]["objects"]
                break
        logger.info("Available printer objects: %s", available_objects)        

        temperature_objects = {}
        for obj in available_objects:
            if "temperature_sensor" in obj:
                temperature_objects[obj] = None
            if "temperature_fan" in obj:
                temperature_objects[obj] = None
            if "extruder" in obj:
                temperature_objects[obj] = None
            if "heater_bed" in obj:
                temperature_objects[obj] = None

        # temperature_objects = {obj: None for obj in available_objects if "temperature_sensor" in obj}

        logger.info("Using the following objects %s", temperature_objects.keys())

        msg_id = 7000
        payload = {
            "jsonrpc": "2.0",
            "method": "printer.objects.subscribe",
            "params": {"objects": {"print_stats": ["state", "info"], **temperature_objects}},
            "id": msg_id,
        }
        while True:
            await ws.send(json.dumps(payload))
            await self.monitor_printer(ws, msg_id)

    
    async def check_klippy_ready(self, ws):
        """
        Check if Moonraker is connected to Klippy and that
        Klippy is ready
        """
        msg_id = 9546
        payload = {
            "jsonrpc": "2.0",
            "method": "server.info",
            "id": msg_id,
        }
        while True:
            await ws.send(json.dumps(payload))
            while True:
                msg = json.loads(await ws.recv()) 
                if msg.get("id") == msg_id:
                    result = msg["result"]
                    klippy_state = result["klippy_state"]
                    klippy_connected = result["klippy_connected"]
                    if klippy_state == "ready" and klippy_connected:
                        self.klippy_state = "ready"
                        return 

    async def request_metadata(self, ws):
        payload = {
            "jsonrpc": "2.0",
            "method": "server.history.list",
            "params":{
                "limit": 1,
                "order": "desc"
            },
            "id": 5656
        }
        await ws.send(json.dumps(payload))
        msg = json.loads(await ws.recv())
        # logger.info("METADATA REQUEST MIGHT FAIL, MSG CONTENT: %s", msg)
        metadata = msg.get('result').get('jobs')[0]
        logger.debug("Metadata: %s", metadata) 
        file_path = self.out_dir / f"metadata.json"
        with open(file_path, 'w', encoding='utf-8') as fp:
            json.dump(metadata, fp, ensure_ascii=False, indent=4)
        self.metadata_saved = True
        logger.info("Saved metadata to %s", file_path)
 
    def update_layer_stats(self, stats):
        self.current_layer = stats["info"]["current_layer"] or 0
        self.total_layer = stats["info"]["total_layer"]

    async def monitor_printer(self, ws, subscription_msg_id):

        while self.websocket_disconnect_count < 10:
            msg = json.loads(await ws.recv())
        
            printjob_state = self.printjob_state
            
            if not self.metadata_saved: 
                await self.request_metadata(ws)

            if msg.get("id") == subscription_msg_id:
                result = msg.get("result") 
                if result is not None:
                    printjob_state = result["status"]["print_stats"]["state"]
                    logger.debug("Reply: %s", printjob_state)
                    logger.info(SUCCESSFUL_SUBSCRIPTION)
                elif msg.get("error") is not None:
                    logger.warning(CHECK_MOONRAKER_LOG)
                    continue
                else:
                    logger.error(UNKNOWN_MSG)

            elif msg.get("method") == "notify_status_update":
                changed = msg["params"][0]  # first in params are the changed fields
                time_ = msg["params"][1] # the time is relative to the monotonic clock used by Klipper
                self.moonraker_time = time_
                self.record_temperatures(changed, time_)
                stats = changed.get("print_stats", {})
                if "state" in stats:
                    printjob_state = stats["state"]
                if "info" in stats:
                    self.update_layer_stats(stats)
            
            elif msg.get("method") == "notify_klippy_shutdown":
                self.klippy_state = "shutdown"
                printjob_state = "shutdown"
                logger.info("Klippy state: %s", self.klippy_state)

            elif msg.get("method") == "notify_klippy_disconnected":
                self.klippy_state = "disconnected"
                printjob_state = "error"
                logger.info("Klippy state: %s", self.klippy_state)

            elif msg.get("method") == "notify_klippy_ready":
                self.klippy_state = "ready"
                printjob_state = "standby"
                logger.info("Klippy state: %s", self.klippy_state)

            else:
                logger.debug("Other: %s", msg.get("method"))
            

            self.update_printjob_state(printjob_state)

            logger.debug("Printjob state: %s", self.printjob_state)

        logger.info(RE_SUBSCRIBE_MOONRAKER)
        self.websocket_disconnect_count = 0    

    async def connect_with_backoff(self, uri):
        """
        Connects to Moonraker using a websocket, see
        https://websocket.org/guides/languages/python/
        """
        delay = 1
        while True:
            try:
                async with websockets.connect(uri) as ws:
                    delay = 1
                    await self.check_klippy_ready(ws)
                    await self.subscribe_and_monitor(ws)

            except (websockets.ConnectionClosed, OSError) as e:
                self.printjob_state = "websocket_disconnected"
                wait = min(delay, 30)
                logger.info(f"Disconnected ({e}), retry in {wait:.1f}s")
                await asyncio.sleep(wait)
                delay = min(delay * 2, 30)


async def _run(cfg):
    cfg = parsing_utils.get_config()
    ai = AutoImager(cfg)
    async with asyncio.TaskGroup() as tg:
        # connects to websocket and subscribes to printer state
        tg.create_task(
            ai.connect_with_backoff(cfg.ws_uri)
        )
        # captures images
        tg.create_task(ai.capture_loop())


def run():
    cfg = parsing_utils.get_config()
    logger.setup_logging(log_level=cfg.loglevel)
    asyncio.run(_run(cfg))


if __name__ == "__main__":
    run()

#!/usr/bin/env python
import asyncio
import json
import logging
import time
from pathlib import Path

import websockets
from picamera2 import Picamera2

from klipper_auto_image import custom_logger as logger
from klipper_auto_image import parsing_utils


class AutoImager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.printer_state = "websocket_disconnected" # not a Moonraker response value
        self.klippy_state = "klippy_disconnected" # not a Moonraker response value
        self.websocket_disconnect_count = 0
        self.print_finished_count = 0
        self.current_layer = 0
        self.total_layer = 0
        self._frame_index = 0 
        self.metadata_saved = True
        self.out_dir = self.cfg.output_dir
        # logger.info("Available cameras: %s", Picamera2.global_camera_info())
        self.picam2 = Picamera2()

        logger.debug("Configured controls %s", cfg.controls)
        config = self.picam2.create_still_configuration(
            controls=cfg.controls
        )
        self.picam2.configure(config)  # before-start configuration
        self.picam2.start()
    
    def new_print_session(self):
        self.out_dir = self.cfg.output_dir / time.strftime("%Y%m%d-%H%M%S")
        logger.debug("Created directory %s", self.out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._frame_index = 0
        self.metadata_saved = False

    def update_printer_state(self, new_state):
        if new_state == self.printer_state:
            return

        old_state = self.printer_state
        
        if new_state == "printing" and old_state != "printing":
            self.new_print_session()
        
        elif new_state in ["cancelled", "error", "complete"] and old_state == "printing":
            new_state = "after_printing" 
 
        self.printer_state = new_state

    def should_shoot(self):
        """
        Currently returns True only if the printer is actually printing
        """
        if self.klippy_state == "ready":
            if self.printer_state == "printing" and self.current_layer > 0:
                return True
            elif self.printer_state == "after_printing":
                if self.print_finished_count < 5:
                    self.print_finished_count += 1
                    return True
                else:
                    self.update_printer_state("finished_capturing_after")
                    self.print_finished_count = 0
                    self.current_layer = 0
                    self.total_layer = 0
            return False

        elif self.printer_state in [
            "standby",
            "paused",
            "error",
            "cancelled",
            "complete",
            "websocket_disconnected",
        ] or self.klippy_state in ["error", "shutdown", "startup", "disconnected"]:
            return False
        else:
            logger.warning("Printer status is unknown")
            return False

    def _capture_image(self, path):
        """
        Non async function that captures the image
        """
        request = self.picam2.capture_request()
        try:
            request.save("main", str(path))
            logger.debug("Captured %s", path.name)
        finally:
            request.release()
            logger.debug("Released cam")

    async def capture_loop(self):
        """
        Calls the image capturing function according to specified frequency.
        The call is offloaded to a thread to avoid blocking.
        """
        next_shot = time.monotonic()

        while True:

            next_shot += 1 / self.cfg.fps

            await asyncio.sleep(max(0.0, next_shot - time.monotonic()))

            if not self.should_shoot():
                self.websocket_disconnect_count += int(self.printer_state == "websocket_disconnected")                          
                # printer is not printing or similar
                continue
            self._frame_index += 1
            path = self.out_dir / f"frame_{self._frame_index:06d}.jpg"
            # Since picamera is blocking, it must be offloaded
            await asyncio.to_thread(self._capture_image, path)
            self.websocket_disconnect_count = 0

    async def subscribe(self, ws):
        """
        Subscribes to Moonraker printer status updates and updates self.printer_state
        """
        msg_id = 7000
        payload = {
            "jsonrpc": "2.0",
            "method": "printer.objects.subscribe",
            "params": {"objects": {"print_stats": ["state", "info"]}},
            "id": msg_id,
        }
        while True:
            await ws.send(json.dumps(payload))
            await self.monitor_printer(ws, msg_id)
 
    
    async def monitor_printer(self, ws, msg_id):

        while self.websocket_disconnect_count < 10:
            msg = json.loads(await ws.recv())

            printer_state = self.printer_state
            
            if not self.metadata_saved: 
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
                metadata = msg.get('result').get('jobs')[0]
                logger.debug("Metadata: %s", metadata) 
                file_path = self.out_dir / f"metadata.json"
                with open(file_path, 'w') as fp:
                    json.dump(metadata, fp)
                self.metadata_saved = True

            if msg.get("id") == msg_id:
                if msg.get("result") is not None:
                    printer_state = msg["result"]["status"]["print_stats"]["state"]
                    logger.debug("Reply: %s", printer_state)
                    logger.info("Successfully subscribed to printer state event")
                else:
                    if msg.get("error") is not None:
                        logger.warning("Moonraker might not be running. If this error persists, check the Moonraker logs.")
                        continue
                    else:
                        logger.error("Unknown message in the websocket response!")

            elif msg.get("method") == "notify_status_update":
                changed = msg["params"][0]  # first in params are the changed fields
                logger.info("Update: %s", changed)
                stats = changed.get("print_stats", {})
                if "state" in stats:
                    printer_state = stats["state"]
                if "info" in stats:
                    self.current_layer = stats["info"]["current_layer"]
                    self.total_layer = stats["info"]["total_layer"]

            # elif msg.get("method") == "notify_proc_stat_update":
            #     logger.debug(msg)
            #
            # elif msg.get("method") == "notify_gcode_response":
            #     logger.debug(msg)

            else:
                logger.debug("Other: %s", msg.get("method"))
            
            self.update_printer_state(printer_state)

            while True:
                if await self.klippy_ready(ws):
                    break
                await asyncio.sleep(1)

            logger.debug("Printer state: %s", self.printer_state)
        logger.info("Resubscribing to Moonraker printer status caused by an unregistered Moonraker restart")
        self.websocket_disconnect_count = 0

    async def klippy_ready(self, ws):
        msg_id = 9000
        payload = {
            "jsonrpc": "2.0",
            "method": "server.info",
            "id": msg_id,
        }
        await ws.send(json.dumps(payload))
        while True:
            msg = json.loads(await ws.recv()) 
            if msg.get("id") == msg_id:
                klippy_state = msg["result"]["klippy_state"]

                if klippy_state == "ready":
                    ready = True
                    if self.klippy_state != klippy_state:
                        logger.info("Klippy state: %s", klippy_state)
                    else:
                        logger.debug("Klippy state: %s", klippy_state)

                elif klippy_state in ["error", "shutdown", "disconnected", "startup"]:
                    ready = False
                    if self.klippy_state != klippy_state:
                        logger.warning("Klippy state: %s", klippy_state)
                    else:
                        logger.debug("Klippy state: %s", klippy_state)

                else:
                    logger.error("Unknown Klippy state: %s", klippy_state)
                
                self.klippy_state = klippy_state
                return ready
    

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
                    await self.klippy_ready(ws)
                    await self.subscribe(ws)

            except (websockets.ConnectionClosed, OSError) as e:
                self.printer_state = "websocket_disconnected"
                wait = min(delay, 30)
                logger.info(f"Disconnected ({e}), retry in {wait:.1f}s")
                await asyncio.sleep(wait)
                delay = min(delay * 2, 30)


async def _run(cfg):
    cfg = parsing_utils.get_config()
    ai = AutoImager(cfg)
    try:
        async with asyncio.TaskGroup() as tg:
            # connects to websocket and subscribes to printer state
            tg.create_task(
                ai.connect_with_backoff(cfg.ws_uri)
            )
            # captures images
            tg.create_task(ai.capture_loop())
    finally:
        ai.picam2.stop()


def run():
    cfg = parsing_utils.get_config()
    logger.setup_logging(log_level=cfg.loglevel)
    asyncio.run(_run(cfg))


if __name__ == "__main__":
    run()

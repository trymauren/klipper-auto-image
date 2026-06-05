#!/usr/bin/env python
import argparse
import logging
import json
import asyncio
import websockets
import time
from picamera2 import Picamera2
from pathlib import Path

ID = 5664 # some number
FREQUENCY = 1 # images per second
logger = logging.getLogger(__name__)
OUTPUT_ROOT = Path("captured_images_test")

class SpaghettiMonitor:

    def __init__(self):
        self.status = "standby"
        self.picam2 = Picamera2()

        some_controls = {
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": 10000,        # microseconds (1/100 s)
            # "AnalogueGain": 4.0,
            # "ColourGains": (1.8, 1.6),   # (red, blue)
            }

        config = self.picam2.create_still_configuration(
            main={"format": "RGB888"},
            # buffer_count=4,
            controls=some_controls,
        )
        
        self.picam2.configure(config) # before-start configuration
        self.picam2.start()
    
        self.run_dir = OUTPUT_ROOT / time.strftime("%Y%m%d-%H%M%S")
        logger.debug("Created directory %s", self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._frame_index = 0


    def should_shoot(self):
        # add counter which captures some images also after stopped?
        # currently only returns true if the printer is printing
        if self.status == "printing":
            return True
        elif self.status in ["standby", "paused", "error", "cancelled", "complete"]:
            return False
        else:
            logger.warn("Something is not right, printer status is unknown!")
            return False


    def _capture_image(self, path):
        request = self.picam2.capture_request()
        try:
            request.save("main", str(path))
            logger.debug("Captured %s", path.name)
        finally:
            request.release()
            logger.debug("Released cam")


    async def capture_loop(self):
        next_shot = time.monotonic()
        while True:
            next_shot += FREQUENCY

            await asyncio.sleep(max(0.0, next_shot - time.monotonic()))
    
            logger.debug("Recorded printer status: %s", self.status)
            if not self.should_shoot():
                # printer is not printing or similar
                continue
 
            self._frame_index += 1
            path = self.run_dir / f"frame_{self._frame_index:06d}.jpg"
            # Since picamera is blocking, it must be offloaded
            await asyncio.to_thread(self._capture_image, path)


    async def subscribe(self, ws):
        payload = {
            "jsonrpc": "2.0",
            "method": "printer.objects.subscribe",
            "params": {"objects": {"print_stats": "state"}},
            "id": ID,
        }
        await ws.send(json.dumps(payload))

        while True:
            msg = json.loads(await ws.recv())

            if msg.get("id") == ID:
                self.status = status = msg["result"]["status"]["print_stats"]["state"]
                logger.debug("REPLY: %s", status)
                logger.info("Successfully subscribed to printer state event")
                
            elif msg.get("method") == "notify_status_update":
                changed = msg["params"][0] # first in params are the changed fields
                logger.debug("UPDATE: %s", changed)
                stats = changed.get("print_stats", {})
                if "state" in stats:
                    self.status = status = stats["state"]

            else:
                logger.debug("OTHER: %s", msg.get("method"))


    async def connect_with_backoff(self, uri):
        delay = 1
        while True:
            try:
                async with websockets.connect(uri) as ws:
                    await self.subscribe(ws)

            except (websockets.ConnectionClosed, OSError) as e:
                wait = min(delay, 30)
                logger.info(f"Disconnected ({e}), retry in {wait:.1f}s")
                await asyncio.sleep(wait)
                delay = min(delay * 2, 30)


async def main(uri):
    sm = SpaghettiMonitor()
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(sm.connect_with_backoff(uri)) # connects to websocket and subscribes to printer state
            tg.create_task(sm.capture_loop()) # captures images
    finally:
        sm.picam2.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-d', '--debug',
        help="Print lots of debugging statements",
        action="store_const", dest="loglevel", const=logging.DEBUG,
        default=logging.WARNING,
    )
    parser.add_argument(
        '-v', '--verbose',
        help="Be verbose",
        action="store_const", dest="loglevel", const=logging.INFO,
    )
    args = parser.parse_args()    
    logging.basicConfig(level=args.loglevel)
    asyncio.run(main("ws://ratrig.labnet:7125/websocket"))

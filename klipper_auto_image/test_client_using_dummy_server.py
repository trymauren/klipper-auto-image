#!/usr/bin/env python
import asyncio
import json
import logging
import signal
import sys

import websockets

from klipper_auto_image import exceptions, parsing_utils

ID = 5664  # some number
logger = logging.getLogger(__name__)


CLIENTS = set()


async def handler(websocket):
    CLIENTS.add(websocket)
    state_to_test = "printing"
    try:
        async for _ in websocket:
            data = json.dumps(
                {
                    "method": "notify_status_update",
                    "params": [{"print_stats": {"state": state_to_test}}],
                }
            )
            websockets.broadcast(CLIENTS, data)
    except websockets.ConnectionClosed:
        pass
    finally:
        CLIENTS.discard(websocket)


async def ws_server(hostname, port):
    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)

    async with websockets.serve(handler, hostname, port):
        logger.info(f"Server running on {hostname}:{port}")
        await stop  # Run until SIGTERM


async def subscribe(ws):
    payload = {
        "jsonrpc": "2.0",
        "method": "printer.objects.subscribe",
        "params": {"objects": {"print_stats": "state"}},
        "id": ID,
    }
    await ws.send(json.dumps(payload))

    while True:
        msg = json.loads(await ws.recv())

        if msg.get("method") == "notify_status_update":
            changed = msg["params"][0]  # first in params are the changed fields
            logger.debug("UPDATE: %s", changed)
            stats = changed.get("print_stats", {})
            if "state" in stats:
                logger.info("printer state: %s", stats["state"])


async def ws_client(uri):
    delay = 1
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await subscribe(ws)

        except (websockets.ConnectionClosed, OSError) as e:
            wait = min(delay, 30)
            logger.info(f"Disconnected ({e}), retry in {wait:.1f}s")
            await asyncio.sleep(wait)
            delay = min(delay * 2, 30)


async def test():
    cfg = parsing_utils.get_config()
    logging.basicConfig(level=cfg.loglevel)
    try:
        hostname, port = parsing_utils.get_hostname_port(cfg.ws_uri)
    except exceptions.InvalidConfigError as e:
        logger.error("Config error: %s", e)
        sys.exit(1)

    try:
        # Testing the klipper-auto-imager client with the dummy-server. This try will
        # fail if picamera2 import in main fails
        from klipper_auto_image import main

    except ImportError:
        # Testing the dummy-server with a dummy client since picamera2 is not available
        logger.warning(
            "!!Using dummy client since we are probably not on a raspberry pi with picamera2 available!!"
        )
        client = ws_client(cfg.ws_uri)
    else:
        # It seems like keeping the client creation out of the try block is nice
        logger.info("Using picamera2 client")
        client = main._run(cfg)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(client)
        tg.create_task(ws_server(hostname, port))


if __name__ == "__main__":
    asyncio.run(test())

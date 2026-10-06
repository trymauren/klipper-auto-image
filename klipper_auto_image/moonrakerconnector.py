import asyncio
import json

import websockets
from websockets.asyncio.client import ClientConnection

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.utils import (
    KlippyStateUpdate,
    # Action,
    Message,
    MoonrakerConnected,
    PrinterStateUpdate,
    PrintJobStateUpdate,
    # QueryResponse,
    ServerInfo,
    # PrintSession,
    # StartPostPrintSession,
    # StartPrintSession,
    SubscriptionUpdate,
    UnknownMessage,
)

JSON_RPC_VERSION = "2.0"

CHECK_MOONRAKER_LOG = (
    "Moonraker might not be running. If this error persists, check the Moonraker logs."
)
UNKNOWN_MSG = "Unknown message in the websocket response!"
SUCCESSFUL_SUBSCRIPTION = "Successfully subscribed to printer state event"
RE_SUBSCRIBE_MOONRAKER = "Resubscribing to Moonraker printer status caused by an unregistered Moonraker restart"


class MoonrakerConnector:
    def __init__(self, cfg):
        self._notifications: asyncio.Queue[Message] = asyncio.Queue(maxsize=20)
        self._pending_requests: dict[int, asyncio.Future[dict]] = {}
        self._next_id = 0
        self._ws: ClientConnection | None = None
        self._loop = asyncio.get_running_loop()
        self._cfg = cfg
        self._connected = asyncio.Event()

    def _set_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("MoonrakerClient used from multiple event loops")

    def _fail_all_pending(self, exc: Exception):
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(exc)
        self._pending_requests.clear()

    def _allocate_id(self):
        self._next_id += 1
        return self._next_id

    def ws_active(self):
        return self._ws is not None and self._ws.state is websockets.State.OPEN

    async def run(self):
        """
        connects to moonraker using a websocket, see
        https://websocket.org/guides/languages/python/
        """
        delay = 1
        while True:
            try:
                async with websockets.connect(self._cfg.ws_uri) as ws:
                    delay = 1
                    self._ws = ws
                    self._set_loop()
                    self._connected.set()
                    msg = MoonrakerConnected(True)
                    await self._notifications.put(msg)
                    logger.info(
                        f"Client is connected to Moonraker server at {self._cfg.ws_uri}"
                    )
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(self._ws_reader())
                        tg.create_task(self._server_info_monitor())

            except (websockets.ConnectionClosed, OSError) as e:
                msg = MoonrakerConnected(False)
                await self._notifications.put(msg)
                self._ws = None
                self._fail_all_pending(e)
                self._connected.clear()
                wait = min(delay, 30)
                logger.info(f"Disconnected ({e}), retry in {wait:.1f}s")
                await asyncio.sleep(wait)
                delay = min(delay * 2, 30)

    async def wait_until_connected(self):
        await self._connected.wait()

    async def _ws_reader(self):
        while True:
            msg = json.loads(await self._ws.recv())

            msg_id = msg.get("id")
            if msg_id is not None:
                future = self._pending_requests.pop(msg["id"], None)
                if future is not None and not future.done():
                    future.set_result(msg)
                continue

            method = msg.get("method")
            if method is not None:
                normalised_msgs = self._handle_incoming_message(msg)
                for normalised_msg in normalised_msgs:
                    await self._notifications.put(normalised_msg)
                continue

    async def _send_json(self, payload):
        if not self.ws_active():
            raise RuntimeError("MoonrakerConnector is not connected")
        await self._ws.send(json.dumps(payload))

    async def _rpc_call(self, method, params=None):
        if not self.ws_active():
            raise RuntimeError("MoonrakerConnector is not connected")
        if self._loop is None:
            raise RuntimeError("MoonrakerConnector loop is not initialized")
        msg_id = self._allocate_id()
        future = self._loop.create_future()
        self._pending_requests[msg_id] = future
        await self._send_json(
            {
                "jsonrpc": JSON_RPC_VERSION,
                "method": method,
                "params": params or {},
                "id": msg_id,
            }
        )
        return await future

    async def next_msg(self):
        return await self._notifications.get()

    async def subscribe(self, objects: dict[str, list | None]):
        # need to also handle errors in messages! msg["error"] in the event of no msg["result"]
        if not self._connected.is_set():
            raise ConnectionError
        msg = await self._rpc_call("printer.objects.list")
        available_objects = []
        if msg is not None:
            result = msg.get("result")
            if result is not None:
                available_objects = msg["result"]["objects"]
            elif msg.get("error") is not None:
                logger.warning(CHECK_MOONRAKER_LOG)
            else:
                logger.error(UNKNOWN_MSG)
        # logger.debug("Available printer objects: %s", available_objects)
        sensor_objects = {}
        for available_obj in available_objects:
            for desired_obj, params in objects.items():
                if desired_obj in available_obj:
                    sensor_objects[available_obj] = params
        # logger.debug("Using the following objects %s", sensor_objects.keys())
        sensor_params = {
            "objects": {"print_stats": ["state", "info"], **sensor_objects}
        }
        msg = await self._rpc_call("printer.objects.subscribe", params=sensor_params)
        if msg is not None:
            result = msg.get("result")
            if result is not None:
                printjob_state = result["status"]["print_stats"]["state"]
                notification = PrintJobStateUpdate(printjob_state=printjob_state)
                await self._notifications.put(notification)
                logger.debug(
                    "Printjob state obtained from successful subscription: %s",
                    printjob_state,
                )
                logger.info(SUCCESSFUL_SUBSCRIPTION)
            elif msg.get("error") is not None:
                logger.warning(CHECK_MOONRAKER_LOG)
            else:
                logger.error(UNKNOWN_MSG)

    async def query(self, method: str, params: dict):
        if not self._connected.is_set():
            raise ConnectionError
        return await self._rpc_call(method, params)

    async def _server_info_monitor(self):
        delay = 10
        while True:
            msg = await self._rpc_call("server.info")
            result = msg.get("result")
            klippy_state = "unknown"
            if result is not None:
                klippy_state = result.get("klippy_state")
                klippy_connected = result.get("klippy_connected")
                msg = ServerInfo(klippy_connected, klippy_state)
                await self._notifications.put(msg)
                msg = KlippyStateUpdate(klippy_state=klippy_state)
                await self._notifications.put(msg)

            elif msg.get("error") is not None:
                logger.warning(CHECK_MOONRAKER_LOG)
            else:
                logger.error(UNKNOWN_MSG)

            await asyncio.sleep(delay)

    # SEE notify_history_changed

    def _handle_incoming_message(self, msg):
        method = msg.get("method")
        if method == "notify_status_update":
            return self._handle_status_update(msg)
        elif method == "notify_klippy_ready":
            return self._handle_klippy_ready()
        elif method == "notify_klippy_shutdown":
            return self._handle_klippy_shutdown()
        elif method == "notify_klippy_disconnected":
            return self._handle_klippy_disconnected()
        return [UnknownMessage()]

    def _handle_status_update(self, msg):
        updates = []
        changed_objects, moonraker_time = msg["params"]
        stats = changed_objects.get("print_stats", {})
        if "state" in stats:
            printjob_state = stats["state"]
            updates.append(PrintJobStateUpdate(printjob_state=printjob_state))
        updates.append(SubscriptionUpdate(changed_objects, moonraker_time))
        return updates

    def _handle_klippy_ready(self):
        update = PrinterStateUpdate(klippy_state="ready", printjob_state="standby")
        return [update]

    def _handle_klippy_shutdown(self):
        update = PrinterStateUpdate(klippy_state="shutdown", printjob_state="shutdown")
        return [update]

    def _handle_klippy_disconnected(self):
        update = PrinterStateUpdate(klippy_state="disconnected", printjob_state="error")
        return [update]

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from klipper_auto_image import custom_logger as logger
from klipper_auto_image.utils import (
    Action,
    KlippyStateUpdate,
    PrinterStateUpdate,
    PrintJobStateUpdate,
    StartPostPrintSession,
    StartPrintSession,
    StateUpdate,
)

TZ = ZoneInfo("Europe/Oslo")


class PrinterStateMachine:
    def __init__(self, cfg):
        self.printjob_state = None
        self.klippy_state = None
        self._cfg = cfg

    @property
    def klippy_ready(self):
        return self.klippy_state == "ready"

    @property
    def printing(self):
        return self.printjob_state in ["printing", "post_printing"]

    @property
    def post_printing(self):
        return self.printjob_state == "post_printing"

    def time_now(self):
        return datetime.now(TZ)

    def ready(self):
        score = 0
        if self.printing:
            score += 1
        if self.klippy_ready:
            score += 1
        return score == 2

    def apply(self, update: StateUpdate) -> list[Action]:
        actions = []
        logger.debug("Applying the following to printer state machine: %s", update)
        if isinstance(update, PrinterStateUpdate):
            actions.extend(self._update_klippy_state(update.klippy_state))
            actions.extend(self._update_printjob_state(update.printjob_state))
        elif isinstance(update, PrintJobStateUpdate):
            actions.extend(self._update_printjob_state(update.printjob_state))
        elif isinstance(update, KlippyStateUpdate):
            actions.extend(self._update_klippy_state(update.klippy_state))
        return actions

    def _update_printjob_state(self, new_state):
        actions = []

        if new_state == self.printjob_state:
            return actions

        old_state = self.printjob_state

        if new_state == "printing" and old_state != "printing":
            action = StartPrintSession(session_id=uuid4(), started_at=datetime.now(TZ))
            actions.append(action)

        elif (
            new_state in ["cancelled", "error", "complete"] and old_state == "printing"
        ):
            new_state = "post_printing"
            action = StartPostPrintSession(datetime.now(TZ))
            actions.append(action)

        self.printjob_state = new_state

        return actions

    def _update_klippy_state(self, new_state):
        actions = []
        self.klippy_state = new_state
        return actions

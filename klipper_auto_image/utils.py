from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID


@dataclass
class Message:
    pass

@dataclass
class StateUpdate(Message):
    pass


@dataclass
class UnknownMessage(Message):
    pass


@dataclass
class QueryResponse(Message):
    changed_objects: dict


@dataclass
class SubscriptionUpdate(Message):
    changed_objects: dict
    moonraker_time: float

    def __str__(self):
        return f"Changed objects: {self.changed_objects}. Moonraker time: {self.moonraker_time}"


@dataclass
class PrinterStateUpdate(StateUpdate):
    klippy_state: str
    printjob_state: str

    def __str__(self):
        return (
            f"Klippy state: {self.klippy_state}. Printjob state: {self.printjob_state}"
        )


@dataclass
class PrintJobStateUpdate(StateUpdate):
    printjob_state: str

    def __str__(self):
        return (
            f"Printjob state: {self.printjob_state}"
        )


@dataclass
class KlippyStateUpdate(StateUpdate):
    klippy_state: str

    def __str__(self):
        return (
            f"Klippy state: {self.klippy_state}"
        )


@dataclass
class ServerInfo(Message):
    klippy_connected: bool
    klippy_state: str

    def __str__(self):
        return f"Klippy connected: {self.klippy_connected}. Klippy state: {self.klippy_state}"

    @property
    def ready(self):
        if self.klippy_connected and (self.klippy_state == "ready"):
            return True


@dataclass
class MoonrakerConnected(Message):
    connection_up: bool

    def __str__(self):
        return f"Moonraker connected: {self.connection_up}"


@dataclass(frozen=True)
class Action:
    pass


@dataclass(frozen=True)
class StartPrintSession(Action):
    session_id: UUID
    started_at: datetime


@dataclass(frozen=True)
class StartPostPrintSession(Action):
    started_at: datetime


@dataclass()
class PrintSession:
    started_at: datetime
    current_layer: int
    post_printing_started_at: datetime
    moonraker_time: float
    print_session_id: UUID
    metadata_saved: bool
    data_out_dir: Path

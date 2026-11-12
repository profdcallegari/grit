import logging
import threading
import time
from typing import Optional

INTERVAL_SECONDS = 5.0


class Progress:
    def __init__(self, logger: logging.Logger, label: str, unit: str = "",
                 total: Optional[int] = None, run_id: Optional[object] = None,
                 interval: Optional[float] = None):
        self.logger = logger
        self.label = label
        self.unit = unit
        self.total = total
        self.run_id = run_id
        self.interval = INTERVAL_SECONDS if interval is None else interval
        self.count = 0
        self.started = 0.0
        self._last_update = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def _emit(self, message: str):
        self.logger.info("%s", message, extra={
            "grit_progress_id": self.run_id,
            "grit_progress_text": message,
        })

    def _description(self) -> str:
        if not self.unit:
            return ""
        with self._lock:
            count = self.count
        amount = f"{count}/{self.total}" if self.total is not None else str(count)
        return f"{amount} {self.unit}"

    def _heartbeat(self):
        while not self._stop.wait(self.interval):
            now = time.monotonic()
            elapsed = int(now - self.started)
            detail = self._description()
            with self._lock:
                stagnant = int(now - self._last_update)
            waiting = f"; sem novos {self.unit} há {stagnant} s" if self.unit and stagnant >= 5 else ""
            self._emit(f"{self.label}: {detail + '; ' if detail else ''}{elapsed} s decorridos{waiting}.")

    def __enter__(self):
        self.started = time.monotonic()
        self._last_update = self.started
        self._emit(f"{self.label}...")
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return self

    def update(self, count: int):
        with self._lock:
            if count != self.count:
                self.count = count
                self._last_update = time.monotonic()

    def __exit__(self, exc_type, exc_value, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if exc_type is None:
            elapsed = time.monotonic() - self.started
            detail = self._description()
            self._emit(f"{self.label} concluída: {detail + ' em ' if detail else ''}{elapsed:.1f} s.")

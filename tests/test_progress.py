import logging
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from grit.core.git_client import GitClient, _nul_tokens
from grit.core.progress import Progress


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []
        self.heartbeat = threading.Event()

    def emit(self, record):
        self.messages.append(record.getMessage())
        if "decorridos" in record.getMessage():
            self.heartbeat.set()


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.Logger("test.progress", logging.INFO)
        self.handler = CapturingHandler()
        self.logger.addHandler(self.handler)

    def test_periodic_count_and_completion_without_late_messages(self):
        with Progress(self.logger, "Etapa", "arquivos processados", total=3, interval=0.01) as progress:
            progress.update(1)
            self.assertTrue(self.handler.heartbeat.wait(2))
        self.assertTrue(any("1/3 arquivos processados" in msg for msg in self.handler.messages))
        self.assertIn("concluída", self.handler.messages[-1])
        self.assertFalse(progress._thread.is_alive())

    def test_failure_does_not_report_completion(self):
        with self.assertRaisesRegex(ValueError, "erro"):
            with Progress(self.logger, "Etapa", interval=0.01):
                raise ValueError("erro")
        self.assertFalse(any("concluída" in msg for msg in self.handler.messages))

    def test_default_interval_reports_stalled_count_after_five_seconds(self):
        started = time.monotonic()
        with Progress(self.logger, "Histórico", "commits processados"):
            self.assertTrue(self.handler.heartbeat.wait(7))
            self.assertGreaterEqual(time.monotonic() - started, 5)
        self.assertTrue(any("sem novos commits processados há" in msg for msg in self.handler.messages))

    def test_history_reports_while_waiting_for_git_output(self):
        release = threading.Event()
        output = (
            b"\0GRIT_RECORD\0abc\0Ana\0ana@example.com\0"
            b"2026-01-01T10:00:00+00:00\0\0initial\0"
        )

        class BlockingStream:
            def __init__(self):
                self.data = output

            def read(self, size):
                if not release.wait(5):
                    raise TimeoutError("A leitura não foi liberada pelo teste.")
                data, self.data = self.data[:size], self.data[size:]
                return data

        class FakeProcess:
            def __init__(self):
                self.stdout = BlockingStream()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def wait(self):
                return 0

        client = GitClient.__new__(GitClient)
        client.repo_dir = Path(".")
        client.progress_id = object()
        result = []
        errors = []

        def consume():
            try:
                result.extend(client.stream_commits())
            except Exception as exc:
                errors.append(exc)

        with patch("grit.core.git_client.subprocess.Popen", return_value=FakeProcess()), \
                patch("grit.core.git_client.logger", self.logger), \
                patch("grit.core.progress.INTERVAL_SECONDS", 0.01):
            worker = threading.Thread(target=consume)
            worker.start()
            try:
                self.assertTrue(self.handler.heartbeat.wait(2))
                self.assertTrue(any("0 commits processados" in msg for msg in self.handler.messages))
                self.assertFalse(any("concluída" in msg for msg in self.handler.messages))
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(result), 1)
        self.assertTrue(any("1 commits processados" in msg and "concluída" in msg
                            for msg in self.handler.messages))

    def test_tokens_are_delivered_before_pipe_buffer_fills(self):
        script = "import sys; sys.stdout.buffer.write(b'first\\0'); sys.stdout.buffer.flush(); sys.stdin.read(1)"
        with subprocess.Popen([sys.executable, "-u", "-c", script],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as proc:
            received = threading.Event()
            tokens = []

            def read_tokens():
                for token in _nul_tokens(proc.stdout):
                    tokens.append(token)
                    received.set()

            reader = threading.Thread(target=read_tokens)
            reader.start()
            try:
                self.assertTrue(received.wait(3))
                self.assertEqual(tokens, ["first"])
            finally:
                proc.stdin.write(b"x")
                proc.stdin.close()
                reader.join(5)
            self.assertFalse(reader.is_alive())
            self.assertEqual(proc.wait(), 0)


if __name__ == "__main__":
    unittest.main()

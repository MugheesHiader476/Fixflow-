"""Killable processing boundary for untrusted decoders; database writes stay in the parent transaction."""

import logging
import multiprocessing
import os
import signal
import sys
import threading
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path

from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.runner import PipelineError, configure_logging, run_pipeline
from backend.schemas.pipeline import PipelineResult

logger = logging.getLogger(__name__)


def _child(
    connection: Connection,
    path: str,
    source_id: str,
    config: PipelineConfig,
    previous: PipelineResult | None,
    force: bool,
    strict_okf: bool,
) -> None:
    if os.name == "posix":
        os.setsid()
    if sys.platform == "linux":
        import resource  # noqa: PLC0415

        maximum = config.process_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))
    try:
        configure_logging()
        result = run_pipeline(Path(path), source_id, config, previous=previous, force=force, strict_okf=strict_okf)
        payload = result.model_dump_json().encode()
        if len(payload) > config.max_result_bytes:
            raise PipelineError("Pipeline output exceeds configured artifact size limit")
        connection.send_bytes(b"1" + payload)
    except PipelineError as error:
        connection.send_bytes(b"0" + str(error).encode())
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        logger.error("Pipeline child failed (%s)", type(error).__name__)
        connection.send_bytes(b"0Pipeline execution failed")
    finally:
        connection.close()


def _stop(process: BaseProcess) -> None:
    try:
        if os.name == "posix" and process.pid and os.getpgid(process.pid) == process.pid:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except (ProcessLookupError, ValueError):
        pass


def execute_pipeline(
    path: Path,
    source_id: str,
    config: PipelineConfig | None = None,
    *,
    previous: PipelineResult | None = None,
    force: bool = False,
    strict_okf: bool = False,
) -> PipelineResult:
    config = config or PipelineConfig()
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_child, args=(sender, str(path), source_id, config, previous, force, strict_okf), daemon=True
    )
    process.start()
    sender.close()
    deadline = threading.Timer(config.processing_timeout_seconds, _stop, args=(process,))
    deadline.daemon = True
    deadline.start()
    try:
        if not receiver.poll(config.processing_timeout_seconds):
            raise PipelineError("Pipeline exceeded configured processing time limit")
        payload = receiver.recv_bytes(config.max_result_bytes + 1)
        if payload[:1] != b"1":
            raise PipelineError(payload[1:].decode())
        return PipelineResult.model_validate_json(payload[1:])
    except (EOFError, OSError) as error:
        raise PipelineError("Pipeline decoder stopped or exceeded its processing limit") from error
    finally:
        deadline.cancel()
        receiver.close()
        process.join(timeout=1)
        if process.is_alive():
            _stop(process)
            process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
        process.close()

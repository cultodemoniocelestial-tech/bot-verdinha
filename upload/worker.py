#!/usr/bin/env python3
"""Worker separado para consumir a fila de upload (SQLite).

Uso:
  python3 upload/worker.py

O start/stop é controlado pela API via runtime flag 'upload_running'.
Este processo pode ser encerrado com Ctrl+C (graceful shutdown).
"""

from pathlib import Path
import sys
import os
import signal
import threading
import uuid

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from upload.app import fila_watcher, QUEUE_STORE  # reutiliza a lógica existente

def main():
    # garante que a flag exista
    if QUEUE_STORE.get_runtime('upload_running', None) is None:
        QUEUE_STORE.set_runtime('upload_running', False)

    stop_event = threading.Event()
    worker_id = f"upload-worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def _handle(sig, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    fila_watcher(stop_event=stop_event, worker_id=worker_id)

if __name__ == '__main__':
    main()

"""
Compressed Rotating File Handler — rotates log files at 1MB and gzips old ones.

When the log file reaches maxBytes (1MB), it is:
  1. Closed
  2. Compressed to edge_server_YYYYMMDD_HHMMSS.log.gz
  3. A new empty log file is created

Old compressed files are cleaned up after backupCount is reached.
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class CompressedRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that gzips old log files with timestamped names."""

    def __init__(
        self,
        filename: str,
        maxBytes: int = 1_048_576,  # 1 MB
        backupCount: int = 10,
        encoding: str = "utf-8",
    ):
        self.namer = self._namer
        self.rotator = self._rotator
        super().__init__(
            filename,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
        )

    @staticmethod
    def _namer(default_name: str) -> str:
        """Generate a timestamped .gz filename for the rotated log."""
        base_dir = os.path.dirname(default_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.splitext(os.path.basename(default_name.split(".")[0]))[0]
        # Use the original base name (without rotation number)
        original_base = os.path.basename(default_name).split(".")[0]
        return os.path.join(base_dir, f"{original_base}_{timestamp}.log.gz")

    @staticmethod
    def _rotator(source: str, dest: str) -> None:
        """Compress the source log file to a .gz destination."""
        try:
            with open(source, "rb") as f_in:
                with gzip.open(dest, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.remove(source)
            logging.getLogger(__name__).info(
                f"Log rotated: {os.path.basename(source)} → {os.path.basename(dest)}"
            )
        except Exception as e:
            logging.getLogger(__name__).error(f"Log rotation failed: {e}")

    def doRollover(self) -> None:
        """Perform a rollover, compressing the old log file."""
        if self.stream:
            self.stream.close()
            self.stream = None

        # Generate timestamped gz filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = os.path.dirname(self.baseFilename)
        base_name = os.path.splitext(os.path.basename(self.baseFilename))[0]
        gz_path = os.path.join(log_dir, f"{base_name}_{timestamp}.log.gz")

        # Compress current log
        if os.path.exists(self.baseFilename):
            try:
                with open(self.baseFilename, "rb") as f_in:
                    with gzip.open(gz_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
            except Exception as e:
                logging.getLogger(__name__).error(f"Log compression failed: {e}")

        # Cleanup old gz files if over backupCount
        self._cleanup_old_gz(log_dir, base_name)

        # Truncate the current log file (start fresh)
        if os.path.exists(self.baseFilename):
            with open(self.baseFilename, "w"):
                pass

        if not self.delay:
            self.stream = self._open()

    def _cleanup_old_gz(self, log_dir: str, base_name: str) -> None:
        """Remove oldest .gz files if count exceeds backupCount."""
        try:
            gz_files = sorted(
                [
                    f for f in os.listdir(log_dir)
                    if f.startswith(base_name) and f.endswith(".log.gz")
                ],
                key=lambda f: os.path.getmtime(os.path.join(log_dir, f)),
            )
            # Keep only the newest backupCount files
            while len(gz_files) > self.backupCount:
                oldest = gz_files.pop(0)
                os.remove(os.path.join(log_dir, oldest))
                logging.getLogger(__name__).info(f"Removed old log archive: {oldest}")
        except Exception as e:
            logging.getLogger(__name__).error(f"Log cleanup failed: {e}")

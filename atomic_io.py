"""Atomic file I/O operations with UTF-8 enforcement and backup capabilities.

Designed for Windows 11 / PowerShell compatibility and resilient state persistence.
"""

import os
import shutil
import tempfile
import hashlib
from pathlib import Path
from typing import Optional, Union


def normalize_newlines(text: str) -> str:
    """Normalize CRLF / CR to standard LF for hash consistency."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def compute_sha256(data: Union[str, bytes, Path], normalize_text: bool = True) -> str:
    """Compute SHA-256 hex digest. If path is provided, reads as text/bytes."""
    if isinstance(data, Path) or (isinstance(data, str) and os.path.exists(data)):
        path = Path(data)
        if path.is_file():
            raw_bytes = path.read_bytes()
            if normalize_text:
                try:
                    text = raw_bytes.decode("utf-8")
                    normalized = normalize_newlines(text)
                    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                except UnicodeDecodeError:
                    return hashlib.sha256(raw_bytes).hexdigest()
            return hashlib.sha256(raw_bytes).hexdigest()

    if isinstance(data, str):
        if normalize_text:
            data = normalize_newlines(data)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
    elif isinstance(data, bytes):
        return hashlib.sha256(data).hexdigest()
    else:
        raise ValueError(f"Unsupported data type for sha256 computation: {type(data)}")


def safe_read_text(filepath: Union[str, Path], encoding: str = "utf-8") -> str:
    """Read file content with UTF-8 default and resilient error handling."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        # Fallback to UTF-8 with replace or utf-8-sig
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")


def create_backup(filepath: Union[str, Path], suffix: str = ".bak") -> Optional[Path]:
    """Create a backup copy of existing file before modification."""
    path = Path(filepath)
    if not path.exists():
        return None
    backup_path = path.with_name(path.name + suffix)
    shutil.copy2(path, backup_path)
    return backup_path


def atomic_write_text(
    filepath: Union[str, Path],
    content: str,
    encoding: str = "utf-8",
    backup: bool = False
) -> Path:
    """Atomically write text to filepath using temporary file and atomic replace.

    Guarantees that readers never see half-written content.
    Ensures parent directories exist.
    """
    path = Path(filepath).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    if backup and path.exists():
        create_backup(path)

    # Write to a temporary file in the same directory to allow atomic os.replace across filesystem boundaries
    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".tmp_{path.name}_",
        text=False
    )
    temp_path = Path(temp_path_str)

    try:
        with os.fdopen(temp_fd, "w", encoding=encoding, newline="\n") as f:
            f.write(content)
        
        # On Windows, os.replace is atomic on same volume since Python 3.3
        os.replace(temp_path, path)
        return path
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise IOError(f"Failed to atomically write to {path}: {e}") from e

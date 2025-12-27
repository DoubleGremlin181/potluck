"""Archive extraction utilities for data ingestion."""

import shutil
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from potluck.core.exceptions import ExtractionError, UnsupportedArchiveError
from potluck.core.logging import get_logger

logger = get_logger(__name__)


# Safety limits for archive extraction (zip bomb protection)
MAX_FILES = 100_000  # Maximum number of files to extract
MAX_TOTAL_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB total extracted size
MAX_COMPRESSION_RATIO = 100  # Suspicious if compression ratio exceeds this


@dataclass
class ExtractedArchive:
    """Context for an extracted archive.

    Manages the temporary directory containing extracted files.
    """

    source_path: Path
    """Original archive path."""

    extract_path: Path
    """Path to extracted contents."""

    is_temporary: bool
    """Whether extract_path is a temporary directory that should be cleaned up."""

    def cleanup(self) -> None:
        """Remove the temporary extraction directory if applicable."""
        if self.is_temporary and self.extract_path.exists():
            logger.debug(f"Cleaning up temporary directory: {self.extract_path}")
            shutil.rmtree(self.extract_path)


def is_archive(path: Path) -> bool:
    """Check if a path is a supported archive file.

    Args:
        path: Path to check.

    Returns:
        True if the path is a supported archive format.
    """
    if not path.is_file():
        return False

    suffix = path.suffix.lower()
    name = path.name.lower()

    # Check common archive extensions
    archive_extensions = {".zip", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2"}
    if suffix in archive_extensions:
        return True

    # Check for .tar.* patterns
    return ".tar." in name


def get_archive_type(path: Path) -> str | None:
    """Determine the archive type from the file path.

    Args:
        path: Path to the archive file.

    Returns:
        Archive type string ('zip', 'tar', 'tgz', etc.) or None if not an archive.
    """
    suffix = path.suffix.lower()
    name = path.name.lower()

    if suffix == ".zip":
        return "zip"
    elif suffix == ".tar":
        return "tar"
    elif suffix == ".tgz" or name.endswith(".tar.gz"):
        return "tgz"
    elif suffix == ".tbz2" or name.endswith(".tar.bz2"):
        return "tbz2"
    elif ".tar." in name:
        # Handle other .tar.* formats
        return "tar"

    return None


def _is_safe_path(dest_path: Path, member_name: str) -> bool:
    """Check if an archive member path is safe (doesn't escape destination).

    Uses path resolution to detect path traversal attacks including encoded variants.

    Args:
        dest_path: The destination directory for extraction.
        member_name: The member name from the archive.

    Returns:
        True if the path is safe, False if it could escape the destination.
    """
    try:
        # Resolve the absolute path of what would be extracted
        full_path = (dest_path / member_name).resolve()
        dest_resolved = dest_path.resolve()
        # Check that the resolved path is within the destination
        return full_path.is_relative_to(dest_resolved)
    except (ValueError, OSError):
        return False


def extract_archive(
    archive_path: Path,
    dest_path: Path | None = None,
    extract_nested: bool = True,
) -> ExtractedArchive:
    """Extract an archive to a destination directory.

    Args:
        archive_path: Path to the archive file.
        dest_path: Optional destination path. If None, a temporary directory is used.
        extract_nested: If True, recursively extract nested archives.

    Returns:
        ExtractedArchive with paths and cleanup info.

    Raises:
        UnsupportedArchiveError: If the archive format is not supported.
        ExtractionError: If extraction fails.
    """
    archive_type = get_archive_type(archive_path)
    if archive_type is None:
        raise UnsupportedArchiveError(f"Unsupported archive format: {archive_path}")

    is_temporary = dest_path is None
    final_dest: Path | None = dest_path

    try:
        # Create temp directory inside try block to handle mkdtemp failures
        if final_dest is None:
            final_dest = Path(tempfile.mkdtemp(prefix="potluck_extract_"))

        logger.info(f"Extracting {archive_path} to {final_dest}")

        if archive_type == "zip":
            _extract_zip(archive_path, final_dest)
        else:
            _extract_tar(archive_path, final_dest, archive_type)

        # Handle nested archives if requested
        if extract_nested:
            _extract_nested_archives(final_dest)

        return ExtractedArchive(
            source_path=archive_path,
            extract_path=final_dest,
            is_temporary=is_temporary,
        )

    except Exception as e:
        # Clean up on failure if we created a temp directory
        if is_temporary and final_dest is not None and final_dest.exists():
            shutil.rmtree(final_dest)
        raise ExtractionError(f"Failed to extract {archive_path}: {e}") from e


def _extract_zip(
    archive_path: Path,
    dest_path: Path,
    max_files: int = MAX_FILES,
    max_total_size: int = MAX_TOTAL_SIZE,
) -> None:
    """Extract a ZIP archive with security checks.

    Args:
        archive_path: Path to the ZIP file.
        dest_path: Destination directory.
        max_files: Maximum number of files to extract.
        max_total_size: Maximum total uncompressed size.

    Raises:
        ExtractionError: If security checks fail or extraction fails.
    """
    with zipfile.ZipFile(archive_path, "r") as zf:
        infos = zf.infolist()

        # Check file count (zip bomb protection)
        if len(infos) > max_files:
            raise ExtractionError(f"Archive contains too many files: {len(infos)} > {max_files}")

        # Check total uncompressed size (zip bomb protection)
        total_size = sum(info.file_size for info in infos)
        if total_size > max_total_size:
            raise ExtractionError(
                f"Archive uncompressed size too large: {total_size} > {max_total_size}"
            )

        # Check compression ratio (zip bomb detection)
        archive_size = archive_path.stat().st_size
        if archive_size > 0 and total_size / archive_size > MAX_COMPRESSION_RATIO:
            raise ExtractionError(
                f"Suspicious compression ratio: {total_size / archive_size:.0f}x "
                f"exceeds limit of {MAX_COMPRESSION_RATIO}x"
            )

        # Security check: prevent path traversal using proper resolution
        for name in zf.namelist():
            if not _is_safe_path(dest_path, name):
                raise ExtractionError(f"Path traversal detected in archive: {name}")

        zf.extractall(dest_path)


def _extract_tar(
    archive_path: Path,
    dest_path: Path,
    archive_type: str,
    max_files: int = MAX_FILES,
    max_total_size: int = MAX_TOTAL_SIZE,
) -> None:
    """Extract a TAR archive (optionally compressed) with security checks.

    Args:
        archive_path: Path to the TAR file.
        dest_path: Destination directory.
        archive_type: Type of compression ('tar', 'tgz', 'tbz2').
        max_files: Maximum number of files to extract.
        max_total_size: Maximum total uncompressed size.

    Raises:
        ExtractionError: If security checks fail or extraction fails.
    """
    mode = "r:"
    if archive_type == "tgz":
        mode = "r:gz"
    elif archive_type == "tbz2":
        mode = "r:bz2"

    with tarfile.open(str(archive_path), mode) as tf:  # type: ignore[call-overload]
        members = tf.getmembers()

        # Check file count (zip bomb protection)
        if len(members) > max_files:
            raise ExtractionError(f"Archive contains too many files: {len(members)} > {max_files}")

        # Check total uncompressed size (zip bomb protection)
        total_size = sum(m.size for m in members if m.isfile())
        if total_size > max_total_size:
            raise ExtractionError(
                f"Archive uncompressed size too large: {total_size} > {max_total_size}"
            )

        # Check compression ratio (zip bomb detection)
        archive_size = archive_path.stat().st_size
        if archive_size > 0 and total_size / archive_size > MAX_COMPRESSION_RATIO:
            raise ExtractionError(
                f"Suspicious compression ratio: {total_size / archive_size:.0f}x "
                f"exceeds limit of {MAX_COMPRESSION_RATIO}x"
            )

        # Security check: prevent path traversal using proper resolution
        for member in members:
            if not _is_safe_path(dest_path, member.name):
                raise ExtractionError(f"Path traversal detected in archive: {member.name}")

        tf.extractall(dest_path, filter="data")


def _extract_nested_archives(base_path: Path, max_depth: int = 2) -> None:
    """Recursively extract nested archives.

    Args:
        base_path: Base directory to scan for nested archives.
        max_depth: Maximum nesting depth to extract.
    """
    if max_depth <= 0:
        return

    for file_path in list(base_path.rglob("*")):
        if file_path.is_file() and is_archive(file_path):
            # Extract nested archive in place
            nested_dest = file_path.with_suffix("")
            if file_path.name.endswith(".tar.gz"):
                nested_dest = Path(str(file_path)[:-7])  # Remove .tar.gz
            elif file_path.name.endswith(".tar.bz2"):
                nested_dest = Path(str(file_path)[:-8])  # Remove .tar.bz2

            if not nested_dest.exists():
                nested_dest.mkdir(parents=True)

            logger.debug(f"Extracting nested archive: {file_path}")
            try:
                archive_type = get_archive_type(file_path)
                if archive_type == "zip":
                    _extract_zip(file_path, nested_dest)
                else:
                    _extract_tar(file_path, nested_dest, archive_type or "tar")

                # Remove the archive after extraction
                file_path.unlink()

                # Recurse into the extracted directory
                _extract_nested_archives(nested_dest, max_depth - 1)

            except Exception as e:
                logger.warning(f"Failed to extract nested archive {file_path}: {e}")


@contextmanager
def extracted(path: Path, extract_nested: bool = True) -> Iterator[Path]:
    """Context manager for extracting and cleaning up archives.

    If the path is an archive, extracts it to a temporary directory and
    yields the extraction path. Cleans up the temporary directory on exit.

    If the path is not an archive (a directory), yields the path unchanged.

    Usage:
        with extracted(some_path) as content_path:
            # Work with content_path
            ...
        # Cleanup happens automatically

    Args:
        path: Path to an archive file or directory.
        extract_nested: If True, recursively extract nested archives.

    Yields:
        Path to the extracted contents (or the original path if not an archive).
    """
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    if path.is_dir():
        # Not an archive, yield as-is
        yield path
    elif is_archive(path):
        # Extract and clean up
        extracted_archive = extract_archive(path, extract_nested=extract_nested)
        try:
            yield extracted_archive.extract_path
        finally:
            extracted_archive.cleanup()
    else:
        # Regular file, yield as-is
        yield path

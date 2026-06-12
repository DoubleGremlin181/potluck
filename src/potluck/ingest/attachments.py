"""Content-addressed attachment blob store: ``<root>/<sha256[:2]>/<sha256>``.

The path is derived from the content hash, so cross-message dedup is a free
existence check — the second message carrying the same file writes nothing.
Blobs never enter the database (metadata lives in the files table).
"""

from pathlib import Path


class AttachmentStore:
    """Writes attachment payloads under a managed root, keyed by sha256."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def save(self, sha256: str, payload: bytes) -> Path:
        """Persist *payload* at its content address; no-op if already stored."""
        path = self.path_for(sha256)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(payload)
            tmp.replace(path)  # atomic: readers never see partial blobs
        return path

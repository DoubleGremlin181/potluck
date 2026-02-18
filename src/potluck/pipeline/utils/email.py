"""Shared email ingestion utilities used across email ingesters."""

SNIPPET_MAX_LENGTH = 200


def generate_snippet(body_text: str | None, max_length: int = SNIPPET_MAX_LENGTH) -> str | None:
    """Generate a preview snippet from email body text.

    Args:
        body_text: Plain text email body.
        max_length: Maximum snippet length.

    Returns:
        Truncated, single-line snippet or None if no body text.
    """
    if not body_text:
        return None
    return body_text[:max_length].replace("\n", " ").strip() or None


def compute_email_size(body_text: str | None, body_html: str | None) -> int | None:
    """Compute total email size in bytes from body text and HTML.

    Args:
        body_text: Plain text body.
        body_html: HTML body.

    Returns:
        Total size in bytes, or None if both bodies are empty.
    """
    size = 0
    if body_text:
        size += len(body_text.encode("utf-8"))
    if body_html:
        size += len(body_html.encode("utf-8"))
    return size if size > 0 else None

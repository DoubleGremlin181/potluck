"""Download utilities for ArcFace pretrained weights.

The pretrained models are from InsightFace's model zoo and are for
non-commercial research purposes only.
"""

import hashlib
from pathlib import Path

import httpx

from potluck.core.logging import get_logger

logger = get_logger(__name__)

# Model weight configurations
# Source: https://huggingface.co/JustinLeee/FaceMind_ArcFace_iResNet50_CASIA_FaceV5
WEIGHTS_CONFIG: dict[str, dict[str, str]] = {
    "arcface_r50": {
        # ArcFace IResNet50 trained on CASIA-FaceV5 - hosted on HuggingFace
        "url": "https://huggingface.co/JustinLeee/FaceMind_ArcFace_iResNet50_CASIA_FaceV5/resolve/main/ArcFace_iResNet50_CASIA_FaceV5.pth?download=true",
        "sha256": "",  # Will validate after first download
        "filename": "arcface_iresnet50.pth",
    },
}

# Default model to use
DEFAULT_MODEL = "arcface_r50"


def get_cache_dir() -> Path:
    """Get the cache directory for model weights."""
    cache_dir = Path.home() / ".cache" / "potluck" / "models" / "arcface"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_weights_path(model_name: str = DEFAULT_MODEL) -> Path:
    """Get the path where weights should be stored.

    Args:
        model_name: Name of the model weights (e.g., 'w600k_r50').

    Returns:
        Path to the weights file.
    """
    if model_name not in WEIGHTS_CONFIG:
        raise ValueError(f"Unknown model: {model_name}. Available: {list(WEIGHTS_CONFIG.keys())}")

    config = WEIGHTS_CONFIG[model_name]
    return get_cache_dir() / config["filename"]


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def download_weights(model_name: str = DEFAULT_MODEL, force: bool = False) -> Path:
    """Download pretrained ArcFace weights.

    Args:
        model_name: Name of the model weights to download.
        force: Force re-download even if file exists.

    Returns:
        Path to the downloaded weights file.

    Raises:
        ValueError: If model_name is unknown.
        RuntimeError: If download fails.
    """
    if model_name not in WEIGHTS_CONFIG:
        raise ValueError(f"Unknown model: {model_name}. Available: {list(WEIGHTS_CONFIG.keys())}")

    config = WEIGHTS_CONFIG[model_name]
    weights_path = get_weights_path(model_name)

    # Check if already downloaded
    if weights_path.exists() and not force:
        logger.info(f"Using cached weights: {weights_path}")
        return weights_path

    logger.info(f"Downloading ArcFace weights ({model_name})...")
    logger.info(f"URL: {config['url']}")

    # Download with progress
    try:
        with httpx.stream("GET", config["url"], follow_redirects=True, timeout=300.0) as response:
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            # Write to temp file first
            temp_path = weights_path.with_suffix(".tmp")
            with open(temp_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        if downloaded % (1024 * 1024) < 8192:  # Log every ~1MB
                            logger.info(f"Download progress: {progress:.1f}%")

            # Move to final location
            temp_path.rename(weights_path)
            logger.info(f"Downloaded weights to: {weights_path}")

            # Log hash for future verification
            file_hash = _compute_sha256(weights_path)
            logger.debug(f"SHA256: {file_hash}")

            return weights_path

    except httpx.HTTPError as e:
        raise RuntimeError(f"Failed to download weights: {e}") from e


def ensure_weights(model_name: str = DEFAULT_MODEL) -> Path:
    """Ensure weights are available, downloading if necessary.

    Args:
        model_name: Name of the model weights.

    Returns:
        Path to the weights file.
    """
    weights_path = get_weights_path(model_name)
    if not weights_path.exists():
        return download_weights(model_name)
    return weights_path

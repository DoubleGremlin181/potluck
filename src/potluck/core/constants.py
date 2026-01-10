"""Shared constants used across Potluck modules.

This module contains constants that are shared between models and processing
to avoid circular imports and ensure consistency.
"""

# Embedding dimensions for various ML models
TEXT_EMBEDDING_DIM = 384  # e5-small-v2 text embeddings
MULTIMODAL_EMBEDDING_DIM = 768  # SigLIP cross-modal embeddings
FACE_EMBEDDING_DIM = 512  # ArcFace face embeddings

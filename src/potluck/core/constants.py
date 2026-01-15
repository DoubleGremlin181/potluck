"""Embedding constants for ML models."""

# Embedding dimensions
TEXT_EMBEDDING_DIM = 384  # e5-small-v2 text embeddings
MULTIMODAL_EMBEDDING_DIM = 768  # SigLIP cross-modal embeddings
FACE_EMBEDDING_DIM = 512  # ArcFace face embeddings

# Default model identifiers
DEFAULT_TEXT_EMBEDDING_MODEL = "intfloat/e5-small-v2"
DEFAULT_MULTIMODAL_MODEL = "google/siglip-base-patch16-224"
DEFAULT_CAPTIONING_MODEL = "Salesforce/blip2-opt-2.7b"

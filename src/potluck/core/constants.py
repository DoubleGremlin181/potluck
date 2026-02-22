"""ML model configuration constants.

Defines embedding dimensions and default model identifiers used across
the processing pipeline. Dimensions must match the models specified here;
changing a model requires updating the corresponding dimension constant
and re-running embeddings.
"""

# Embedding dimensions
TEXT_EMBEDDING_DIM = 384  # e5-small-v2 text embeddings
MULTIMODAL_EMBEDDING_DIM = 768  # SigLIP2 cross-modal embeddings
FACE_EMBEDDING_DIM = 512  # ArcFace face embeddings

# Default model identifiers
DEFAULT_TEXT_EMBEDDING_MODEL = "intfloat/e5-small-v2"
DEFAULT_MULTIMODAL_MODEL = "google/siglip2-base-patch16-224"
DEFAULT_CAPTIONING_MODEL = "microsoft/Florence-2-base-ft"

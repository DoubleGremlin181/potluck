"""Processor implementations for entity processing.

This module contains all processor implementations:
- HashingProcessor: File and perceptual hashing
- MetadataProcessor: EXIF metadata extraction
- OCRProcessor: Text extraction using EasyOCR
- FaceProcessor: Face detection using MTCNN + ArcFace
- CaptioningProcessor: Image captioning using BLIP-2
- TextEmbeddingProcessor: Text embedding for text-to-text semantic search
- MultimodalTextEmbeddingProcessor: Text embedding for cross-modal search
- MediaEmbeddingProcessor: Visual/text embeddings for media
"""

# Processors are auto-discovered by the parent __init__.py
# This file enables the processors/ directory to be a proper package

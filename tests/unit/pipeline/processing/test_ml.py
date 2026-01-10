"""Unit tests for ML model loading utilities."""

import os
from unittest.mock import patch

import pytest

# Skip entire module if ML dependencies not installed
pytest.importorskip("torch")
pytest.importorskip("sentence_transformers")

import torch

from potluck.pipeline.processing.ml import MLModels, get_device


class TestGetDevice:
    """Tests for get_device function."""

    def test_explicit_device(self) -> None:
        """get_device should return explicit device when specified."""
        device = get_device("cpu")
        assert device == torch.device("cpu")

    def test_explicit_cuda_device(self) -> None:
        """get_device should return CUDA device when explicitly specified."""
        device = get_device("cuda:0")
        assert device == torch.device("cuda:0")

    @patch.dict(os.environ, {"GPU": "false"}, clear=False)
    def test_gpu_disabled_returns_cpu(self) -> None:
        """get_device should return CPU when GPU env var is false."""
        device = get_device()
        assert device == torch.device("cpu")

    @patch.dict(os.environ, {"GPU": "true"}, clear=False)
    @patch("torch.cuda.is_available", return_value=False)
    def test_gpu_enabled_but_unavailable_returns_cpu(self, mock_cuda: object) -> None:
        """get_device should return CPU when GPU enabled but CUDA unavailable."""
        device = get_device()
        assert device == torch.device("cpu")

    @patch.dict(os.environ, {}, clear=False)
    def test_no_gpu_env_defaults_to_cpu(self) -> None:
        """get_device should default to CPU when GPU env var not set."""
        # Remove GPU env var if present
        with patch.dict(os.environ, {"GPU": ""}, clear=False):
            os.environ.pop("GPU", None)
            device = get_device()
            assert device == torch.device("cpu")


class TestMLModels:
    """Tests for MLModels class."""

    def setup_method(self) -> None:
        """Clear model cache before each test."""
        MLModels.clear_cache()

    def test_init_with_default_device(self) -> None:
        """MLModels should initialize with auto-detected device."""
        models = MLModels()
        assert models.device in [torch.device("cpu"), torch.device("cuda")]

    def test_init_with_explicit_device(self) -> None:
        """MLModels should accept explicit device."""
        models = MLModels(device="cpu")
        assert models.device == torch.device("cpu")

    def test_clear_cache(self) -> None:
        """clear_cache should empty the model cache."""
        MLModels._cache["test_key"] = "test_value"
        MLModels.clear_cache()
        assert "test_key" not in MLModels._cache

    def test_model_cache_shared_across_instances(self) -> None:
        """Model cache should be shared across MLModels instances."""
        models1 = MLModels(device="cpu")
        models2 = MLModels(device="cpu")

        # Add something to cache through first instance
        models1._cache["shared_key"] = "shared_value"

        # Should be visible from second instance
        assert models2._cache.get("shared_key") == "shared_value"

        MLModels.clear_cache()

    def test_text_embedding_model_cached(self) -> None:
        """Text embedding model should be cached after first load."""
        models = MLModels(device="cpu")

        # First call loads the model
        model1 = models.get_text_embedding_model()

        # Second call returns cached model
        model2 = models.get_text_embedding_model()

        assert model1 is model2

    def test_text_embedding_model_has_correct_dimension(self) -> None:
        """Text embedding model should produce 384-dimensional embeddings."""
        models = MLModels(device="cpu")
        model = models.get_text_embedding_model()

        # e5 model produces 384-d embeddings
        embedding = model.encode("query: test text")
        assert len(embedding) == 384

    def test_siglip_model_cached(self) -> None:
        """SigLIP model should be cached after first load."""
        models = MLModels(device="cpu")

        # First call loads the model
        model1, processor1 = models.get_siglip_model()

        # Second call returns cached model
        model2, processor2 = models.get_siglip_model()

        assert model1 is model2
        assert processor1 is processor2

    def test_encode_text_siglip_returns_768d(self) -> None:
        """SigLIP text encoding should return 768-dimensional vector."""
        models = MLModels(device="cpu")
        embedding = models.encode_text_siglip("test text")

        assert len(embedding) == 768

    def test_encode_text_siglip_normalized(self) -> None:
        """SigLIP text embedding should be L2-normalized by default."""
        import numpy as np

        models = MLModels(device="cpu")
        embedding = models.encode_text_siglip("test text")

        # L2 norm should be approximately 1.0
        norm = np.linalg.norm(embedding)
        assert abs(norm - 1.0) < 0.01

    def test_easyocr_reader_cached(self) -> None:
        """EasyOCR reader should be cached after first load."""
        models = MLModels(device="cpu")

        # First call loads the reader
        reader1 = models.get_easyocr_reader()

        # Second call returns cached reader
        reader2 = models.get_easyocr_reader()

        assert reader1 is reader2

    def test_easyocr_custom_languages(self) -> None:
        """EasyOCR reader should support custom languages."""
        models = MLModels(device="cpu")

        # Different languages should create different cache entries
        reader_en = models.get_easyocr_reader(["en"])
        reader_es = models.get_easyocr_reader(["es"])

        # Both should be cached but different instances
        assert reader_en is not reader_es

    def test_mtcnn_cached(self) -> None:
        """MTCNN detector should be cached after first load."""
        models = MLModels(device="cpu")

        # First call loads the detector
        mtcnn1 = models.get_mtcnn()

        # Second call returns cached detector
        mtcnn2 = models.get_mtcnn()

        assert mtcnn1 is mtcnn2

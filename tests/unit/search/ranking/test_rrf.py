"""Tests for RRF (Reciprocal Rank Fusion) ranker."""

from uuid import uuid4

from potluck.models.base import EntityType
from potluck.search.dtos import RankingConfig, RetrievalResult
from potluck.search.ranking.rrf import RRFRanker


class TestRRFRanker:
    """Tests for RRFRanker."""

    def test_empty_results(self) -> None:
        """Empty input produces empty output."""
        ranker = RRFRanker()
        config = RankingConfig()

        result = ranker.rank({}, config)
        assert result == []

    def test_fts_only_results(self) -> None:
        """FTS-only results are ranked correctly."""
        ranker = RRFRanker()
        config = RankingConfig(fts_weight=0.3, vector_weight=0.7, rrf_k=60)

        id1 = uuid4()
        id2 = uuid4()

        fts_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=id1,
                score=0.9,
                rank=1,
                snippet="result 1",
            ),
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=id2,
                score=0.7,
                rank=2,
                snippet="result 2",
            ),
        ]

        result = ranker.rank({"fts": fts_results}, config)

        assert len(result) == 2
        # First result should have higher score
        assert result[0].entity_id == id1
        assert result[1].entity_id == id2
        # FTS ranks should be preserved
        assert result[0].fts_rank == 1
        assert result[1].fts_rank == 2
        # Vector ranks should be None
        assert result[0].vector_rank is None
        # Snippets should be preserved
        assert result[0].snippet == "result 1"

    def test_vector_only_results(self) -> None:
        """Vector-only results are ranked correctly."""
        ranker = RRFRanker()
        config = RankingConfig(fts_weight=0.3, vector_weight=0.7, rrf_k=60)

        id1 = uuid4()
        id2 = uuid4()

        vector_results = [
            RetrievalResult(
                entity_type=EntityType.MEDIA,
                entity_id=id1,
                score=0.95,
                rank=1,
            ),
            RetrievalResult(
                entity_type=EntityType.MEDIA,
                entity_id=id2,
                score=0.85,
                rank=2,
            ),
        ]

        result = ranker.rank({"vector": vector_results}, config)

        assert len(result) == 2
        assert result[0].entity_id == id1
        # Vector ranks should be preserved
        assert result[0].vector_rank == 1
        assert result[1].vector_rank == 2
        # FTS ranks should be None
        assert result[0].fts_rank is None

    def test_hybrid_fusion_boosts_overlap(self) -> None:
        """Results appearing in both FTS and vector get boosted."""
        ranker = RRFRanker()
        config = RankingConfig(fts_weight=0.3, vector_weight=0.7, rrf_k=60)

        # Create IDs
        overlap_id = uuid4()  # Appears in both
        fts_only_id = uuid4()  # FTS only
        vector_only_id = uuid4()  # Vector only

        fts_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=overlap_id,
                score=0.8,
                rank=1,
            ),
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=fts_only_id,
                score=0.6,
                rank=2,
            ),
        ]

        vector_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=vector_only_id,
                score=0.9,
                rank=1,
            ),
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=overlap_id,
                score=0.85,
                rank=2,
            ),
        ]

        result = ranker.rank({"fts": fts_results, "vector": vector_results}, config)

        # Overlap result should be first (boosted by appearing in both)
        assert result[0].entity_id == overlap_id
        assert result[0].fts_rank == 1
        assert result[0].vector_rank == 2

        # Other results should follow
        ids = {r.entity_id for r in result}
        assert overlap_id in ids
        assert fts_only_id in ids
        assert vector_only_id in ids

    def test_rrf_score_calculation(self) -> None:
        """RRF score calculation is correct."""
        ranker = RRFRanker()
        config = RankingConfig(fts_weight=0.3, vector_weight=0.7, rrf_k=60)

        entity_id = uuid4()

        fts_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=entity_id,
                score=0.9,
                rank=1,
            ),
        ]

        vector_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=entity_id,
                score=0.8,
                rank=3,
            ),
        ]

        result = ranker.rank({"fts": fts_results, "vector": vector_results}, config)

        assert len(result) == 1

        # Expected score:
        # FTS: 0.3 / (60 + 1) = 0.3 / 61 ≈ 0.00492
        # Vector: 0.7 / (60 + 3) = 0.7 / 63 ≈ 0.01111
        # Total ≈ 0.01603
        expected_fts = 0.3 / (60 + 1)
        expected_vector = 0.7 / (60 + 3)
        expected_total = expected_fts + expected_vector

        assert abs(result[0].score - expected_total) < 0.0001

    def test_different_weights(self) -> None:
        """Different weights affect ranking."""
        ranker = RRFRanker()

        id1 = uuid4()  # FTS rank 1
        id2 = uuid4()  # Vector rank 1

        fts_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=id1,
                score=0.9,
                rank=1,
            ),
        ]

        vector_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=id2,
                score=0.9,
                rank=1,
            ),
        ]

        # With higher FTS weight, FTS result should win
        config_fts = RankingConfig(fts_weight=0.9, vector_weight=0.1)
        result_fts = ranker.rank({"fts": fts_results, "vector": vector_results}, config_fts)
        assert result_fts[0].entity_id == id1

        # With higher vector weight, vector result should win
        config_vec = RankingConfig(fts_weight=0.1, vector_weight=0.9)
        result_vec = ranker.rank({"fts": fts_results, "vector": vector_results}, config_vec)
        assert result_vec[0].entity_id == id2

    def test_k_parameter_effect(self) -> None:
        """Different k values affect score distribution."""
        ranker = RRFRanker()

        id1 = uuid4()
        id2 = uuid4()

        fts_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=id1,
                score=0.9,
                rank=1,
            ),
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=id2,
                score=0.5,
                rank=10,
            ),
        ]

        # With low k, rank difference has bigger impact
        config_low_k = RankingConfig(fts_weight=1.0, vector_weight=0.0, rrf_k=1)
        result_low = ranker.rank({"fts": fts_results}, config_low_k)

        # With high k, rank difference has smaller impact
        config_high_k = RankingConfig(fts_weight=1.0, vector_weight=0.0, rrf_k=1000)
        result_high = ranker.rank({"fts": fts_results}, config_high_k)

        # Score ratio should be more extreme with low k
        ratio_low = result_low[0].score / result_low[1].score
        ratio_high = result_high[0].score / result_high[1].score

        assert ratio_low > ratio_high

    def test_cross_entity_type_fusion(self) -> None:
        """Results from different entity types are fused correctly."""
        ranker = RRFRanker()
        config = RankingConfig()

        email_id = uuid4()
        media_id = uuid4()
        chat_id = uuid4()

        fts_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=email_id,
                score=0.9,
                rank=1,
            ),
            RetrievalResult(
                entity_type=EntityType.CHAT_MESSAGE,
                entity_id=chat_id,
                score=0.7,
                rank=2,
            ),
        ]

        vector_results = [
            RetrievalResult(
                entity_type=EntityType.MEDIA,
                entity_id=media_id,
                score=0.85,
                rank=1,
            ),
        ]

        result = ranker.rank({"fts": fts_results, "vector": vector_results}, config)

        # All entity types should be present
        entity_types = {r.entity_type for r in result}
        assert EntityType.EMAIL in entity_types
        assert EntityType.MEDIA in entity_types
        assert EntityType.CHAT_MESSAGE in entity_types

    def test_zero_weight_ignored(self) -> None:
        """Retrievers with zero weight contribute nothing."""
        ranker = RRFRanker()
        config = RankingConfig(fts_weight=0.0, vector_weight=1.0)

        fts_id = uuid4()
        vector_id = uuid4()

        fts_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=fts_id,
                score=0.9,
                rank=1,
            ),
        ]

        vector_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=vector_id,
                score=0.5,
                rank=1,
            ),
        ]

        result = ranker.rank({"fts": fts_results, "vector": vector_results}, config)

        # FTS-only results don't appear when FTS weight is 0
        result_ids = {r.entity_id for r in result}
        assert fts_id not in result_ids
        assert vector_id in result_ids

        # Vector result should have non-zero score
        vector_result = next(r for r in result if r.entity_id == vector_id)
        assert vector_result.score > 0.0

    def test_snippet_preserved_from_fts(self) -> None:
        """Snippets from FTS are preserved in fused results."""
        ranker = RRFRanker()
        config = RankingConfig()

        entity_id = uuid4()

        fts_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=entity_id,
                score=0.9,
                rank=1,
                snippet="...matching <<keyword>> in text...",
            ),
        ]

        vector_results = [
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=entity_id,
                score=0.85,
                rank=2,
                # Vector doesn't have snippets
            ),
        ]

        result = ranker.rank({"fts": fts_results, "vector": vector_results}, config)

        assert result[0].snippet == "...matching <<keyword>> in text..."

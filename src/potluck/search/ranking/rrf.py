"""Reciprocal Rank Fusion (RRF) ranker for hybrid search."""

from collections import defaultdict
from uuid import UUID

from potluck.models.base import EntityType
from potluck.search.dtos import RankingConfig, RetrievalResult, SearchResultItem
from potluck.search.ranking.base import Ranker


class RRFRanker(Ranker):
    """Reciprocal Rank Fusion ranker for combining multiple result sets.

    RRF is a simple but effective fusion algorithm that combines ranked lists
    without requiring score normalization. Each result's contribution is:

        contribution = weight / (k + rank)

    Where:
    - weight: Retriever weight (e.g., 0.3 for FTS, 0.7 for vector)
    - k: Constant that dampens high rankings (default 60)
    - rank: 1-indexed position in the retriever's result list

    Final score = sum of contributions across all retrievers.

    RRF advantages:
    - No score normalization needed (ranks are comparable)
    - Simple and fast
    - Works well in practice for hybrid search
    - Robust to outliers in any single retriever
    """

    def rank(
        self,
        results: dict[str, list[RetrievalResult]],
        config: RankingConfig,
    ) -> list[SearchResultItem]:
        """Combine results using Reciprocal Rank Fusion.

        Args:
            results: Dict with keys "fts" and/or "vector" mapping to results.
            config: Ranking configuration with weights and RRF k constant.

        Returns:
            Fused and ranked search results.
        """
        # Map retriever names to weights
        weights = {
            "fts": config.fts_weight,
            "vector": config.vector_weight,
        }
        k = config.rrf_k

        # Track scores and metadata per unique entity
        # Key: (entity_type, entity_id)
        scores: dict[tuple[EntityType, UUID], float] = defaultdict(float)
        fts_ranks: dict[tuple[EntityType, UUID], int] = {}
        vector_ranks: dict[tuple[EntityType, UUID], int] = {}
        snippets: dict[tuple[EntityType, UUID], str | None] = {}

        # Process each retriever's results
        for retriever_name, retriever_results in results.items():
            weight = weights.get(retriever_name, 0.0)
            if weight == 0.0:
                continue

            for result in retriever_results:
                entity_key = (result.entity_type, result.entity_id)

                # RRF contribution: weight / (k + rank)
                scores[entity_key] += weight / (k + result.rank)

                # Track ranks for debugging/transparency
                if retriever_name == "fts":
                    fts_ranks[entity_key] = result.rank
                    # FTS provides snippets, vector doesn't
                    if result.snippet and entity_key not in snippets:
                        snippets[entity_key] = result.snippet
                elif retriever_name == "vector":
                    vector_ranks[entity_key] = result.rank

        # Sort by combined score (descending)
        sorted_entities = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        # Build SearchResultItem list
        return [
            SearchResultItem(
                entity_type=entity_key[0],
                entity_id=entity_key[1],
                score=score,
                fts_rank=fts_ranks.get(entity_key),
                vector_rank=vector_ranks.get(entity_key),
                snippet=snippets.get(entity_key),
                # title, occurred_at, source_type populated by orchestrator
            )
            for entity_key, score in sorted_entities
        ]

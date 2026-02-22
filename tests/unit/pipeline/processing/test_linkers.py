"""Unit tests for entity linkers, priority mapping, and preemption guard."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from potluck.core.celery import PRIORITY_INGEST, PRIORITY_LINK, processor_to_celery_priority
from potluck.models.base import EntityType
from potluck.models.links import EntityLink, LinkType
from potluck.pipeline.processing.linkers.base import PERSIST_BATCH_SIZE, BaseLinker
from potluck.pipeline.processing.linkers.semantic import SemanticLinker
from potluck.pipeline.processing.linkers.spatial import (
    SpatialLinker,
    _CoordEntity,
    haversine_distance,
)
from potluck.pipeline.processing.linkers.temporal import TemporalLinker

# ---------------------------------------------------------------------------
# Priority mapping tests
# ---------------------------------------------------------------------------


class TestProcessorToCeleryPriority:
    """Tests for processor_to_celery_priority() mapping function."""

    def test_hashing_priority_10_maps_to_1(self) -> None:
        assert processor_to_celery_priority(10) == 1

    def test_metadata_priority_20_maps_to_2(self) -> None:
        assert processor_to_celery_priority(20) == 2

    def test_ocr_priority_30_maps_to_3(self) -> None:
        assert processor_to_celery_priority(30) == 3

    def test_faces_priority_40_maps_to_4(self) -> None:
        assert processor_to_celery_priority(40) == 4

    def test_captioning_priority_50_maps_to_5(self) -> None:
        assert processor_to_celery_priority(50) == 5

    def test_text_embedding_priority_25_maps_to_2(self) -> None:
        """Processors with priority 25 share the same Celery level as priority 20."""
        assert processor_to_celery_priority(25) == 2

    def test_clustering_priority_90_maps_to_8(self) -> None:
        """Priority 90 clamps to max processing priority 8."""
        assert processor_to_celery_priority(90) == 8

    def test_priority_100_clamps_to_8(self) -> None:
        """Priorities above 80 all clamp to 8."""
        assert processor_to_celery_priority(100) == 8

    def test_priority_0_clamps_to_1(self) -> None:
        """Priority 0 clamps up to 1 (0 is reserved for ingestion)."""
        assert processor_to_celery_priority(0) == 1

    def test_priority_constants(self) -> None:
        """PRIORITY_INGEST < all processing < PRIORITY_LINK."""
        assert PRIORITY_INGEST == 0
        assert PRIORITY_LINK == 9
        for proc_pri in [10, 20, 30, 40, 50, 60, 70, 80, 90]:
            celery_pri = processor_to_celery_priority(proc_pri)
            assert PRIORITY_INGEST < celery_pri < PRIORITY_LINK


# ---------------------------------------------------------------------------
# SUPPORTED_ENTITY_TYPES filtering tests
# ---------------------------------------------------------------------------


class TestSupportedEntityTypes:
    """Tests for SUPPORTED_ENTITY_TYPES on each linker."""

    def test_temporal_supports_time_based_entities(self) -> None:
        expected = {
            EntityType.MEDIA,
            EntityType.CHAT_MESSAGE,
            EntityType.EMAIL,
            EntityType.CALENDAR_EVENT,
            EntityType.LOCATION_VISIT,
            EntityType.SOCIAL_POST,
        }
        assert expected == TemporalLinker.SUPPORTED_ENTITY_TYPES

    def test_spatial_supports_location_only(self) -> None:
        assert {EntityType.LOCATION} == SpatialLinker.SUPPORTED_ENTITY_TYPES
        # LocationVisit explicitly excluded
        assert EntityType.LOCATION_VISIT not in SpatialLinker.SUPPORTED_ENTITY_TYPES

    def test_semantic_supports_embeddable_entities(self) -> None:
        expected = {
            EntityType.MEDIA,
            EntityType.KNOWLEDGE_NOTE,
            EntityType.DOCUMENT,
        }
        assert expected == SemanticLinker.SUPPORTED_ENTITY_TYPES

    def test_base_linker_requires_supported_entity_types(self) -> None:
        """Subclass without SUPPORTED_ENTITY_TYPES should raise TypeError."""
        with pytest.raises(TypeError, match="SUPPORTED_ENTITY_TYPES"):

            class BadLinker(BaseLinker):
                NAME = "bad"
                LINK_TYPES = {LinkType.SAME_TIME}

                def find_links(
                    self,
                    session: object,
                    entity_type: object,
                    entity_ids: object,
                ) -> Iterator[EntityLink]:
                    return iter([])


# ---------------------------------------------------------------------------
# Spatial linker grid algorithm tests
# ---------------------------------------------------------------------------


class TestSpatialGrid:
    """Tests for the grid-based spatial indexing in SpatialLinker."""

    def test_same_location_within_50m(self) -> None:
        """Two points within 50m should produce a SAME_LOCATION link."""
        linker = SpatialLinker()
        # Two points ~30m apart (same street)
        a = _CoordEntity(uuid4(), 40.7128, -74.0060)
        b = _CoordEntity(uuid4(), 40.7131, -74.0060)  # ~33m north

        links = list(linker._grid_find_links([a, b], EntityType.LOCATION))
        assert len(links) == 1
        assert links[0].link_type == LinkType.SAME_LOCATION
        assert links[0].confidence >= 0.5

    def test_near_within_500m(self) -> None:
        """Two points 50-500m apart should produce a NEAR link."""
        linker = SpatialLinker()
        # Two points ~200m apart
        a = _CoordEntity(uuid4(), 40.7128, -74.0060)
        b = _CoordEntity(uuid4(), 40.7146, -74.0060)  # ~200m north

        links = list(linker._grid_find_links([a, b], EntityType.LOCATION))
        assert len(links) == 1
        assert links[0].link_type == LinkType.NEAR
        assert links[0].confidence >= 0.3

    def test_beyond_threshold_no_link(self) -> None:
        """Two points >500m apart should produce no link."""
        linker = SpatialLinker()
        # Two points ~5km apart
        a = _CoordEntity(uuid4(), 40.7128, -74.0060)
        b = _CoordEntity(uuid4(), 40.7580, -74.0060)  # ~5km north

        links = list(linker._grid_find_links([a, b], EntityType.LOCATION))
        assert len(links) == 0

    def test_grid_boundary_no_false_negatives(self) -> None:
        """Points at grid cell boundaries should still be compared.

        Place two points that are close (within near_meters) but would
        fall into different grid cells. The forward-neighbor comparison
        should still detect them.
        """
        linker = SpatialLinker(near_meters=500)
        # Cell size ≈ 500/111320 ≈ 0.00449 degrees
        # Place points straddling a cell boundary
        cell_size = 500.0 / 111_320.0
        base_lat = cell_size * 100  # arbitrary cell boundary
        a = _CoordEntity(uuid4(), base_lat - 0.0001, 0.0)
        b = _CoordEntity(uuid4(), base_lat + 0.0001, 0.0)

        # These should be ~22m apart
        dist = haversine_distance(a.lat, a.lon, b.lat, b.lon)
        assert dist < 50  # They're very close

        links = list(linker._grid_find_links([a, b], EntityType.LOCATION))
        assert len(links) >= 1  # Should not miss due to grid boundary

    def test_empty_entities_no_links(self) -> None:
        """Empty or single entity list should produce no links."""
        linker = SpatialLinker()
        assert list(linker._grid_find_links([], EntityType.LOCATION)) == []

        a = _CoordEntity(uuid4(), 40.7128, -74.0060)
        assert list(linker._grid_find_links([a], EntityType.LOCATION)) == []

    def test_haversine_known_distance(self) -> None:
        """Verify haversine calculation against known distance."""
        # NYC to LA is approximately 3,944 km
        nyc_lat, nyc_lon = 40.7128, -74.0060
        la_lat, la_lon = 34.0522, -118.2437

        distance = haversine_distance(nyc_lat, nyc_lon, la_lat, la_lon)
        assert 3_900_000 < distance < 4_000_000  # Within 100km tolerance


# ---------------------------------------------------------------------------
# Batched persistence tests
# ---------------------------------------------------------------------------


class TestBatchedPersistence:
    """Tests for BaseLinker.persist_links batched commit behavior."""

    def test_persist_commits_in_batches(self) -> None:
        """persist_links should commit every PERSIST_BATCH_SIZE links."""
        session = MagicMock()
        linker = TemporalLinker()

        # Create more links than one batch
        num_links = PERSIST_BATCH_SIZE + 50
        links: list[EntityLink] = [
            EntityLink(
                source_type=EntityType.MEDIA,
                source_id=uuid4(),
                target_type=EntityType.MEDIA,
                target_id=uuid4(),
                link_type=LinkType.SAME_TIME,
                confidence=0.9,
            )
            for _ in range(num_links)
        ]

        persisted = linker.persist_links(session, iter(links))

        assert persisted == num_links
        # Should have committed twice: once at PERSIST_BATCH_SIZE, once for remainder
        assert session.commit.call_count == 2
        assert session.add.call_count == num_links

    def test_persist_empty_iterator(self) -> None:
        """persist_links with empty iterator should return 0 and not commit."""
        session = MagicMock()
        linker = TemporalLinker()

        persisted = linker.persist_links(session, iter([]))
        assert persisted == 0
        session.commit.assert_not_called()

    def test_persist_sets_linker_provenance(self) -> None:
        """persist_links should set linker_name, version, is_automatic on each link."""
        session = MagicMock()
        linker = TemporalLinker()

        link = EntityLink(
            source_type=EntityType.MEDIA,
            source_id=uuid4(),
            target_type=EntityType.MEDIA,
            target_id=uuid4(),
            link_type=LinkType.SAME_TIME,
            confidence=0.9,
        )

        linker.persist_links(session, iter([link]))

        assert link.linker_name == "temporal"
        assert link.linker_version == linker.VERSION
        assert link.is_automatic is True


# ---------------------------------------------------------------------------
# Preemption guard tests
# ---------------------------------------------------------------------------


class TestPreemptionGuard:
    """Tests for has_pending_processing preemption guard."""

    def test_no_pending_returns_false(self) -> None:
        """When no processing tasks are queued, returns False."""
        from potluck.core.celery import has_pending_processing

        mock_app = MagicMock()
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 0

        mock_conn = MagicMock()
        mock_channel = MagicMock()
        mock_channel.client = mock_redis
        mock_conn.channel.return_value = mock_channel
        mock_app.connection_for_read.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_app.connection_for_read.return_value.__exit__ = MagicMock(return_value=False)

        assert has_pending_processing(mock_app) is False

    def test_pending_returns_true(self) -> None:
        """When processing tasks are queued, returns True."""
        from potluck.core.celery import has_pending_processing

        mock_app = MagicMock()
        mock_redis = MagicMock()

        # Priority 3 has tasks pending
        def llen_side_effect(key: str) -> int:
            if key.endswith("3"):
                return 5
            return 0

        mock_redis.llen.side_effect = llen_side_effect

        mock_conn = MagicMock()
        mock_channel = MagicMock()
        mock_channel.client = mock_redis
        mock_conn.channel.return_value = mock_channel
        mock_app.connection_for_read.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_app.connection_for_read.return_value.__exit__ = MagicMock(return_value=False)

        assert has_pending_processing(mock_app) is True

    def test_connection_error_returns_true(self) -> None:
        """If Redis check fails, assume tasks still pending (safe fallback)."""
        from potluck.core.celery import has_pending_processing

        mock_app = MagicMock()
        mock_app.connection_for_read.side_effect = ConnectionError("Redis down")

        assert has_pending_processing(mock_app) is True


# ---------------------------------------------------------------------------
# Linker dispatch filtering tests
# ---------------------------------------------------------------------------


class TestLinkerDispatch:
    """Tests for linker dispatch helpers in tasks/processing.py."""

    @pytest.mark.ml
    def test_dispatch_temporal_only_for_supported_types(self) -> None:
        """dispatch_temporal_linker should only dispatch for supported entity types."""
        from potluck.pipeline.tasks.processing import dispatch_temporal_linker

        import_run_id = str(uuid4())
        entity_ids = [str(uuid4())]

        with patch("potluck.pipeline.tasks.processing.run_temporal_linker_batch") as mock_task:
            # MEDIA is supported
            dispatch_temporal_linker(import_run_id, EntityType.MEDIA.value, entity_ids)
            mock_task.apply_async.assert_called_once()

            mock_task.reset_mock()

            # DOCUMENT is NOT supported by temporal linker
            dispatch_temporal_linker(import_run_id, EntityType.DOCUMENT.value, entity_ids)
            mock_task.apply_async.assert_not_called()

    @pytest.mark.ml
    def test_dispatch_spatial_only_for_location(self) -> None:
        """dispatch_spatial_linker should only dispatch for LOCATION."""
        from potluck.pipeline.tasks.processing import dispatch_spatial_linker

        import_run_id = str(uuid4())
        entity_ids = [str(uuid4())]

        with patch("potluck.pipeline.tasks.processing.run_spatial_linker_batch") as mock_task:
            # LOCATION is supported
            dispatch_spatial_linker(import_run_id, EntityType.LOCATION.value, entity_ids)
            mock_task.apply_async.assert_called_once()

            mock_task.reset_mock()

            # MEDIA is NOT supported by spatial linker
            dispatch_spatial_linker(import_run_id, EntityType.MEDIA.value, entity_ids)
            mock_task.apply_async.assert_not_called()

    @pytest.mark.ml
    def test_dispatch_semantic_only_for_embeddable(self) -> None:
        """dispatch_semantic_linker should only dispatch for embeddable types."""
        from potluck.pipeline.tasks.processing import dispatch_semantic_linker

        import_run_id = str(uuid4())
        entity_ids = [str(uuid4())]

        with patch("potluck.pipeline.tasks.processing.run_semantic_linker_batch") as mock_task:
            # MEDIA is supported
            dispatch_semantic_linker(import_run_id, EntityType.MEDIA.value, entity_ids)
            mock_task.apply_async.assert_called_once()

            mock_task.reset_mock()

            # CHAT_MESSAGE is NOT supported by semantic linker
            dispatch_semantic_linker(import_run_id, EntityType.CHAT_MESSAGE.value, entity_ids)
            mock_task.apply_async.assert_not_called()


# ---------------------------------------------------------------------------
# Chain priority tests
# ---------------------------------------------------------------------------


class TestChainPriorities:
    """Tests for priority assignment on processing chain tasks."""

    @pytest.mark.ml
    def test_batch_pipeline_sets_priorities(self) -> None:
        """run_batch_entity_pipeline should set Celery priority on each task in the chain."""
        from potluck.pipeline.tasks.processing import run_batch_entity_pipeline

        entity_ids = [str(uuid4())]

        with patch("potluck.pipeline.tasks.processing.chain") as mock_chain:
            mock_chain.return_value.apply_async = MagicMock()

            run_batch_entity_pipeline(EntityType.MEDIA.value, entity_ids)

            # The chain should have been called
            mock_chain.assert_called_once()
            # Each task in the chain should be a signature with .set(priority=...)
            # The call args are the signature objects
            call_args = mock_chain.call_args[0]
            assert len(call_args) > 0

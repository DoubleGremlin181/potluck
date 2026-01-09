"""Entity linkers for creating relationships between entities.

Linkers analyze batches of entities after import completes to create
EntityLink records for related entities. Unlike processors which operate
on individual entities, linkers compare entities pairwise to find
temporal, spatial, and semantic relationships.

Available linkers:
- TemporalLinker: Creates SAME_TIME links for entities occurring close in time
- SpatialLinker: Creates SAME_LOCATION and NEAR links based on coordinates
- SemanticLinker: Creates SIMILAR links based on embedding similarity
"""

from potluck.pipeline.processing.linkers.base import BaseLinker
from potluck.pipeline.processing.linkers.semantic import SemanticLinker
from potluck.pipeline.processing.linkers.spatial import SpatialLinker
from potluck.pipeline.processing.linkers.temporal import TemporalLinker

__all__ = [
    "BaseLinker",
    "TemporalLinker",
    "SpatialLinker",
    "SemanticLinker",
]

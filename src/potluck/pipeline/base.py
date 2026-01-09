"""Abstract base class for all pipeline stages.

A Stage is a unit of work in the pipeline that transforms input to output.
Both ingestion and processing operations are modeled as stages, providing
a unified interface for pipeline orchestration.
"""

from abc import ABC, abstractmethod
from typing import ClassVar


class Stage[InputT, OutputT](ABC):
    """Abstract base class for all pipeline stages.

    A Stage is a unit of work that transforms input to output. Both ingestion
    stages (file path -> entities) and processing stages (media -> results)
    inherit from this base class.

    Subclasses must:
    - Define a NAME class attribute identifying the stage
    - Implement the execute() method

    Subclasses may optionally:
    - Override should_execute() to conditionally skip execution
    """

    NAME: ClassVar[str]
    """Unique identifier for this stage type."""

    @abstractmethod
    def execute(self, input_data: InputT) -> OutputT:
        """Execute the stage on input data.

        Args:
            input_data: Input for this stage.

        Returns:
            Result of stage execution.
        """
        ...

    def should_execute(self, input_data: InputT) -> bool:
        """Check if this stage should execute for the given input.

        Default returns True. Override to conditionally skip certain inputs
        (e.g., OCR stage skipping non-image media).

        Args:
            input_data: Input to check.

        Returns:
            True if the stage should execute, False to skip.
        """
        return True

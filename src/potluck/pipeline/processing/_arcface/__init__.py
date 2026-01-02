"""Vendored ArcFace components from InsightFace.

This module contains the IResNet backbone architecture from InsightFace's
arcface_torch implementation, adapted for PyTorch-native inference.

Source: https://github.com/deepinsight/insightface/tree/master/recognition/arcface_torch
License: Non-commercial research purposes only

Note: The pretrained models are for non-commercial research use only.
"""

from potluck.pipeline.processing._arcface.download import download_weights, get_weights_path
from potluck.pipeline.processing._arcface.iresnet import (
    IBasicBlock,
    IResNet,
    iresnet18,
    iresnet34,
    iresnet50,
    iresnet100,
    iresnet200,
)

__all__ = [
    "IBasicBlock",
    "IResNet",
    "iresnet18",
    "iresnet34",
    "iresnet50",
    "iresnet100",
    "iresnet200",
    "download_weights",
    "get_weights_path",
]

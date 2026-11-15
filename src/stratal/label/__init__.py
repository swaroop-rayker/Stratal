"""Labeling: captions, CLIP scores and the filter (LABEL-1.1 to LABEL-1.4).

``models`` defines what the stage needs from a model without importing PyTorch.
``hf`` is the only module that imports PyTorch and transformers. ``captioning``,
``scoring``, ``filtering`` and ``vram`` hold the owner's units (ADR-0012).
"""

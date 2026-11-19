"""The inference service: one model at a time, behind ``/predict`` (REG-1.4, ADR-0016).

It runs in its own container, built from the ``worker-gpu`` image, and the API
forwards ``POST /api/v1/models/{version_id}/predict`` to it. See
:mod:`stratal.inference.holder` for where a model runs and when the GPU is given
back, and :mod:`stratal.inference.app` for the HTTP side.
"""

#!/usr/bin/env python3
"""Unit tests for GPU worker modules."""

import pytest
from pathlib import Path

# Tests verify that each module compiles and basic imports work
MODULES = [
    "workers.gpu_worker.discovery",
    "workers.gpu_worker.embedder",
    "workers.gpu_worker.dedup",
    "workers.gpu_worker.clustering",
    "workers.gpu_worker.graphrag_engine",
    "workers.gpu_worker.consensus",
    "workers.gpu_worker.claims_conflicts",
    "workers.gpu_worker.markdown_compiler",
    "workers.gpu_worker.chunker",
    "apps.control_api.schemas",
    "apps.control_api.routes",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    """Verify each module can be imported without error."""
    __import__(module_name)


if __name__ == "test_gpu_worker":
    # Allow running standalone
    pytest.main([__file__, "-v"])
"""Dagster definitions entry point for the FTB pipeline.

Assets are registered here as adapters are built. The code server loads this module
via the -m flag: dagster api grpc -m ftb.definitions
"""

import dagster


defs = dagster.Definitions(
    assets=[],
    resources={},
)

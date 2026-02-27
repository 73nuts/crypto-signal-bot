#!/bin/bash
# Living Documentation: Architecture Dependency Graph Generator
#
# Usage: ./scripts/gen_docs_graph.sh
# Output: docs/assets/architecture.svg
#
# Key parameter notes:
#   --only src          Show only internal modules, exclude third-party packages
#   --cluster           Group display by package
#   --rankdir TB        Top-to-bottom layout
#   --max-module-depth 3  Limit module depth to avoid excessive granularity
#   --exclude-exact     Exclude "black hole" modules (infrastructure depended on by everyone)

set -e

cd "$(dirname "$0")/.."

echo "Generating architecture dependency graph..."

export PYTHONPATH=$PYTHONPATH:.

pydeps src \
    --only src \
    --cluster \
    --rankdir TB \
    --max-module-depth 3 \
    --exclude-exact \
        src.core.config \
        src.core.structured_logger \
        src.core.database \
        src.i18n \
    --no-show \
    -o docs/assets/architecture.svg

echo "Done: docs/assets/architecture.svg"
echo "Nodes: $(grep -c '<g id=\"node' docs/assets/architecture.svg)"

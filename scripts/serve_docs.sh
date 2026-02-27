#!/bin/bash
# Start local documentation server
# Visit http://127.0.0.1:8000

cd "$(dirname "$0")/.."
mkdocs serve

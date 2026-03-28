#!/bin/bash

SCENARIO_DIR="output/${2:-scenario_1_integration_bug}"

if [ -f "$SCENARIO_DIR/response.json" ] && [ ! -f "$SCENARIO_DIR/patch.diff" ]; then
  python3 ci_loop.py extract-patch "$@" || exit 1
fi

python3 ci_loop.py apply "$@"

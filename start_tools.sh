#!/bin/bash
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

python3 tools/pdu_editor.py &
python3 tools/trace_analyzer.py &
python3 tools/trace_editor.py &
python3 tools/eth_trace_analyzer.py &

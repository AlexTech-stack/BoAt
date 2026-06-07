#!/bin/bash

pkill -f "python3 ui/launcher.py" 2>/dev/null && echo "Stopped launcher" || true
pkill -f "python3 ui/dashboard.py" 2>/dev/null && echo "Stopped dashboard" || true
pkill -f "python3 ui/commander.py" 2>/dev/null && echo "Stopped commander" || true
pkill -f "python3 ui/control_panel.py" 2>/dev/null && echo "Stopped control panel" || true
pkill -f "python3 ui/recorder.py" 2>/dev/null && echo "Stopped recorder" || true
pkill -f "python3 ui/pdu_editor.py" 2>/dev/null && echo "Stopped pdu editor" || true
pkill -f "python3 ui/trace_analyzer.py" 2>/dev/null && echo "Stopped trace analyzer" || true

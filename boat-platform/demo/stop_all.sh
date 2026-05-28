#!/bin/bash

pkill -f "python3 launcher.py" 2>/dev/null && echo "Stopped launcher" || true
pkill -f "python3 dashboard.py" 2>/dev/null && echo "Stopped dashboard" || true
pkill -f "python3 commander.py" 2>/dev/null && echo "Stopped commander" || true
pkill -f "python3 control_panel.py" 2>/dev/null && echo "Stopped control panel" || true
pkill -f "python3 recorder.py" 2>/dev/null && echo "Stopped recorder" || true

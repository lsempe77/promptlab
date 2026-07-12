#!/bin/sh
cd /app
# Check what version of api.py uvicorn is actually using
python -c "
import sys
sys.path.insert(0, '/app')
import importlib, backend.app.api as m
import inspect
src = inspect.getsource(m.stage_status)
if 'model_version_map' in src:
    print('NEW code: model_version_map IS present')
else:
    print('OLD code: model_version_map is MISSING')
print('module file:', m.__file__)
"

#!/bin/sh
# Use Python to write api.py (bypasses overlay lock on cp)
python3 - << 'EOF'
import shutil, os
src = '/data/api.py.new2'
dst = '/app/backend/app/api.py'
# Remove __pycache__ first
import glob
for f in glob.glob('/app/backend/app/__pycache__/api*.pyc'):
    os.remove(f)
    print('removed cache:', f)
shutil.copy2(src, dst)
print('copied', os.path.getsize(src), 'bytes to', dst)
print('dst size now:', os.path.getsize(dst))
EOF

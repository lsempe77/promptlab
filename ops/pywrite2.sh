#!/bin/sh
python3 - << 'EOF'
import os, glob

src = '/data/api.py.new2'
dst = '/app/backend/app/api.py'

# Remove pyc cache
for f in glob.glob('/app/backend/app/__pycache__/api*.pyc'):
    try:
        os.remove(f)
        print('removed:', f)
    except Exception as e:
        print('could not remove:', f, e)

# Read source
with open(src, 'rb') as f:
    content = f.read()
print(f'read {len(content)} bytes from {src}')

# Write destination using open() directly
try:
    with open(dst, 'wb') as f:
        f.write(content)
    actual = os.path.getsize(dst)
    print(f'wrote to {dst}, size now: {actual}')
    if actual != len(content):
        print('ERROR: sizes mismatch!')
    else:
        print('SUCCESS')
except Exception as e:
    print('WRITE FAILED:', e)
    # Try alternative: write to a temp file in /data and use os.replace
    tmp = '/data/api_tmp.py'
    with open(tmp, 'wb') as f:
        f.write(content)
    try:
        os.replace(tmp, dst)
        print('os.replace succeeded')
    except Exception as e2:
        print('os.replace also failed:', e2)
EOF

#!/bin/sh
cd /app
python /data/reset_exhausted.py --field authors
python /data/reset_exhausted.py --field sub_sector

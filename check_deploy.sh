#!/bin/sh
python -c "
import sys; sys.path.insert(0,'/app')
from backend.app.prompts import _categorical_options_block
from backend.app.fields import FIELDS
block = _categorical_options_block(FIELDS['sub_sector'])
print(block[:300])
"

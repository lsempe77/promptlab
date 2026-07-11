import sys
sys.path.insert(0, '/app')
try:
    import pandas as pd
    print('pandas ok:', pd.__version__)
except Exception as e:
    print('pandas FAIL:', e)
try:
    import python_calamine
    print('python_calamine ok')
except Exception as e:
    print('python_calamine FAIL:', e)
try:
    df = pd.read_excel('/tmp/test_check.xlsx', engine='calamine')
    print('read_excel ok')
except Exception as e:
    print('read_excel FAIL (expected - no file):', type(e).__name__)
# Check what engines are available
try:
    from pandas.io.excel import _EXCEL_WRITERS
    print('writers:', list(_EXCEL_WRITERS.keys())[:5])
except:
    pass

"""
Examine 'wrong' predictions to identify false negatives from minor text differences.
Samples across fields and models, categorises the type of mismatch.
"""
import sqlite3, json, sys, re, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from backend.app import scoring
from rapidfuzz import fuzz

conn = sqlite3.connect('promptlab_inspect.db')
conn.row_factory = sqlite3.Row

FIELDS = ['authors', 'author_country', 'author_affiliation', 'sector_name', 'sub_sector']

def fold(v):
    """Apply the same folding the scorer uses."""
    if v is None:
        return ''
    if isinstance(v, list):
        return [fold(x) for x in v]
    return scoring.fold_value(v)

def classify_mismatch(pred_str, truth_str):
    """Classify why pred != truth."""
    if not pred_str and not truth_str:
        return 'both_empty'
    if not pred_str:
        return 'missing_value'
    if not truth_str:
        return 'hallucination'
    
    pred_f = scoring.fold_value(str(pred_str))
    truth_f = scoring.fold_value(str(truth_str))
    
    if pred_f == truth_f:
        return 'IDENTICAL_after_fold'
    
    ratio = fuzz.ratio(pred_f, truth_f)
    partial = fuzz.partial_ratio(pred_f, truth_f)
    token_sort = fuzz.token_sort_ratio(pred_f, truth_f)
    
    if ratio >= 95:
        return f'near_identical (ratio={ratio})'
    if token_sort >= 95:
        return f'word_order_only (token_sort={token_sort})'
    if ratio >= 80:
        return f'minor_diff (ratio={ratio})'
    if partial >= 90:
        return f'substring (partial={partial})'
    # Check specific patterns
    pred_lower = pred_f.lower()
    truth_lower = truth_f.lower()
    if pred_lower == truth_lower:
        return 'case_only'
    # Abbreviation check
    if len(pred_f) < len(truth_f) * 0.6 or len(truth_f) < len(pred_f) * 0.6:
        return f'abbreviation_or_truncation (ratio={ratio})'
    return f'genuine_mismatch (ratio={ratio})'


print("=" * 100)
print("ANALYSIS OF 'WRONG' PREDICTIONS — Are they genuine errors or scoring artefacts?")
print("=" * 100)

for field in FIELDS:
    print(f"\n{'='*80}")
    print(f"FIELD: {field.upper()}")
    print(f"{'='*80}")
    
    is_list = field not in ('sector_name', 'sub_sector')
    
    # Get wrong predictions (is_correct=0, has parsed value, has GT)
    rows = conn.execute('''
        SELECT r.model_id, r.record_id, r.parsed_value_json, r.outcome, r.score,
               g.value_json
        FROM runs r
        JOIN ground_truth g ON g.record_id = r.record_id AND g.field_name = r.field_name
        JOIN prompt_versions pv ON r.prompt_version_id = pv.id
        WHERE r.field_name = ?
          AND r.is_correct = 0
          AND r.parsed_value_json IS NOT NULL
          AND r.error IS NULL
          AND pv.accepted = 1
        ORDER BY RANDOM()
        LIMIT 60
    ''', (field,)).fetchall()
    
    categories = {}
    examples = {}
    
    for row in rows:
        try:
            pred = json.loads(row['parsed_value_json'])
            truth = json.loads(row['value_json'])
        except:
            continue
        
        model_short = str(row['model_id']).split('/')[-1][:20] if row['model_id'] else '-'
        
        if is_list:
            # For list fields, check element-level mismatches
            pred_list = pred if isinstance(pred, list) else [pred]
            truth_list = truth if isinstance(truth, list) else [truth]
            
            # Find elements in truth not in pred (misses) and pred not in truth (hallucinations)
            pred_folded = set(fold(p) for p in pred_list if p)
            truth_folded = set(fold(t) for t in truth_list if t)
            
            misses = truth_folded - pred_folded  # in GT but not predicted
            extras = pred_folded - truth_folded  # predicted but not in GT
            
            for miss in list(misses)[:2]:
                # Find best match in pred
                best_match = max(pred_folded, key=lambda p: fuzz.ratio(p, miss)) if pred_folded else ''
                cat = classify_mismatch(best_match, miss)
                categories[cat] = categories.get(cat, 0) + 1
                if cat not in examples:
                    examples[cat] = []
                if len(examples[cat]) < 3:
                    examples[cat].append({
                        'model': model_short,
                        'rec': row['record_id'],
                        'miss': miss[:80],
                        'best_pred': best_match[:80],
                        'all_pred': [fold(p)[:40] for p in pred_list[:3]],
                    })
        else:
            # Categorical: single value comparison
            pred_str = str(pred).strip() if pred else ''
            truth_str = str(truth).strip() if truth else ''
            cat = classify_mismatch(pred_str, truth_str)
            categories[cat] = categories.get(cat, 0) + 1
            if cat not in examples:
                examples[cat] = []
            if len(examples[cat]) < 3:
                examples[cat].append({
                    'model': model_short,
                    'rec': row['record_id'],
                    'pred': pred_str[:80],
                    'truth': truth_str[:80],
                })
    
    # Print category summary
    total = sum(categories.values())
    print(f"\nMismatch categories ({total} wrong elements sampled):")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        pct = 100*count/total if total else 0
        flag = '  ⚠️ SCORING ARTEFACT?' if any(k in cat for k in ['after_fold','near_identical','word_order','case_only','minor_diff']) else ''
        print(f"  {cat:<45} {count:>4} ({pct:.0f}%){flag}")
    
    # Print examples for the most suspicious categories
    print("\nExamples by category:")
    for cat in sorted(categories.keys(), key=lambda c: -categories[c]):
        if not any(k in cat for k in ('after_fold','near_identical','word_order','case_only','minor_diff','substring','abbreviation')):
            ex_list = examples.get(cat, [])
            if ex_list:
                print(f"\n  [{cat}]")
                for ex in ex_list[:2]:
                    if is_list:
                        print(f"    rec={ex['rec']} model={ex['model']}")
                        print(f"    MISS:  {ex['miss']}")
                        print(f"    PRED:  {ex['best_pred']}")
                    else:
                        print(f"    rec={ex['rec']} model={ex['model']}")
                        print(f"    PRED:  {ex['pred']}")
                        print(f"    TRUTH: {ex['truth']}")
        else:
            ex_list = examples.get(cat, [])
            if ex_list:
                print(f"\n  [{cat}]  ← Possible scoring artefact")
                for ex in ex_list[:2]:
                    if is_list:
                        print(f"    rec={ex['rec']} model={ex['model']}")
                        print(f"    MISS:  {ex['miss']}")
                        print(f"    BEST_PRED: {ex['best_pred']}")
                    else:
                        print(f"    rec={ex['rec']} model={ex['model']}")
                        print(f"    PRED:  {ex['pred']}")
                        print(f"    TRUTH: {ex['truth']}")

conn.close()
print("\nDone.")

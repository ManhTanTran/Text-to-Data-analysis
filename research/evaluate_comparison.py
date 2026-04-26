"""
Script 2: Evaluation so sánh DIN-SQL vs DAIL-SQL.

Tính:
- Execution Accuracy (EX): chạy SQL trên SQLite, so sánh kết quả với ground truth
- Accuracy theo độ khó (EASY/MEDIUM/HARD/EXTRA)
- Export báo cáo CSV chi tiết

Usage:
    python evaluate_comparison.py \
        --din_file    path/to/predicted_din_sql.txt \
        --dail_file   path/to/predicted_dail_sql_cleaned.txt \
        --gold_file   path/to/dev_gold.sql \
        --db_dir      path/to/spider_data/database \
        --dev_json    path/to/spider_data/dev.json \
        --output_csv  comparison_results.csv
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd
from tqdm import tqdm


# =============================================================================
# SQL EXECUTION
# =============================================================================

def execute_sql(db_path: str, sql: str, timeout: int = 15):
    """Chạy SQL, trả về (success, rows, error)."""
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.text_factory = lambda b: b.decode(errors="ignore")
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        conn.close()
        return True, rows, None
    except Exception as e:
        return False, None, str(e)[:200]


def is_order_sensitive(sql: str) -> bool:
    return "order by" in sql.lower()


def compare_results(pred_sql: str, gold_sql: str, db_path: str) -> bool:
    """So sánh execution results của 2 SQL."""
    ok_pred, pred_rows, _ = execute_sql(db_path, pred_sql)
    ok_gold, gold_rows, _ = execute_sql(db_path, gold_sql)

    if not ok_pred or not ok_gold:
        return False
    if pred_rows is None or gold_rows is None:
        return False

    # Với ORDER BY: so sánh có thứ tự
    if is_order_sensitive(gold_sql):
        return pred_rows == gold_rows

    # Không ORDER BY: so sánh dạng multiset
    try:
        return sorted(pred_rows) == sorted(gold_rows)
    except TypeError:
        return set(map(tuple, pred_rows)) == set(map(tuple, gold_rows))


# =============================================================================
# SPIDER DIFFICULTY CLASSIFICATION
# =============================================================================

# Theo Spider paper: đánh giá độ khó dựa trên số SQL components
HARDNESS_KEYWORDS = {
    'easy': {'select', 'from', 'where'},
    'hard': {'join', 'group by', 'having', 'order by', 'union', 'intersect', 'except'},
}

def classify_hardness(sql: str) -> str:
    """Phân loại độ khó (heuristic đơn giản)."""
    sql_lower = sql.lower()
    hard_count = sum(1 for kw in HARDNESS_KEYWORDS['hard'] if kw in sql_lower)
    has_nested = sql_lower.count('select') > 1

    if hard_count == 0 and not has_nested:
        return 'easy'
    elif hard_count <= 1 and not has_nested:
        return 'medium'
    elif hard_count <= 2:
        return 'hard'
    else:
        return 'extra'


# =============================================================================
# MAIN EVALUATION
# =============================================================================

def load_gold_file(gold_file: str):
    """Đọc dev_gold.sql — format: SQL<TAB>db_id per line."""
    entries = []
    with open(gold_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if '\t' in line:
                sql, db_id = line.rsplit('\t', 1)
                entries.append({'sql': sql.strip(), 'db_id': db_id.strip()})
    return entries


def load_predictions(pred_file: str):
    """Đọc file predictions — mỗi dòng 1 SQL."""
    # Thử nhiều encoding
    for enc in ['utf-8', 'utf-8-sig', 'latin-1']:
        try:
            with open(pred_file, 'r', encoding=enc) as f:
                return [line.strip() for line in f if line.strip()]
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Cannot decode {pred_file}")


def load_dev_questions(dev_json: str):
    """Đọc dev.json để lấy question text."""
    with open(dev_json, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return [{'question': item['question'], 'db_id': item['db_id']} for item in data]


def evaluate(pred_sqls, gold_entries, db_dir, questions, method_name):
    """Đánh giá 1 method: return list of dict."""
    assert len(pred_sqls) == len(gold_entries), \
        f"Mismatch: {len(pred_sqls)} predictions vs {len(gold_entries)} gold"

    results = []
    for i, (pred, gold_entry) in enumerate(tqdm(
        zip(pred_sqls, gold_entries),
        total=len(pred_sqls),
        desc=f"Eval {method_name}"
    )):
        db_id = gold_entry['db_id']
        gold_sql = gold_entry['sql']
        db_path = os.path.join(db_dir, db_id, f"{db_id}.sqlite")

        correct = False
        error = None
        if os.path.exists(db_path):
            try:
                correct = compare_results(pred, gold_sql, db_path)
            except Exception as e:
                error = str(e)[:100]
        else:
            error = f"DB not found: {db_path}"

        q_text = questions[i]['question'] if i < len(questions) else ""

        results.append({
            'idx': i,
            'db_id': db_id,
            'question': q_text,
            'gold_sql': gold_sql,
            f'{method_name}_pred': pred,
            f'{method_name}_correct': correct,
            f'{method_name}_error': error,
            'hardness': classify_hardness(gold_sql),
        })
    return results


def merge_results(din_results, dail_results):
    """Merge kết quả DIN và DAIL theo idx."""
    merged = []
    for din, dail in zip(din_results, dail_results):
        merged.append({
            'idx': din['idx'],
            'db_id': din['db_id'],
            'question': din['question'],
            'gold_sql': din['gold_sql'],
            'hardness': din['hardness'],
            'din_sql_pred': din['din_sql_pred'],
            'din_sql_correct': din['din_sql_correct'],
            'dail_sql_pred': dail['dail_sql_pred'],
            'dail_sql_correct': dail['dail_sql_correct'],
        })
    return merged


def print_report(merged, din_details_csv=None):
    """In báo cáo so sánh."""
    total = len(merged)
    din_correct = sum(1 for r in merged if r['din_sql_correct'])
    dail_correct = sum(1 for r in merged if r['dail_sql_correct'])

    print("\n" + "=" * 60)
    print(" " * 15 + "DIN-SQL vs DAIL-SQL COMPARISON")
    print("=" * 60)
    print(f"\nTotal queries: {total}")
    print(f"\n{'Metric':<30} {'DIN-SQL':>12} {'DAIL-SQL':>12}")
    print("-" * 60)
    print(f"{'Execution Accuracy (EX)':<30} {din_correct/total*100:>11.2f}% {dail_correct/total*100:>11.2f}%")
    print(f"{'Correct queries':<30} {din_correct:>12} {dail_correct:>12}")

    # Per-hardness breakdown
    print("\n--- Accuracy by Hardness ---")
    print(f"{'Hardness':<15} {'Count':>8} {'DIN-SQL':>12} {'DAIL-SQL':>12}")
    print("-" * 50)
    for h in ['easy', 'medium', 'hard', 'extra']:
        subset = [r for r in merged if r['hardness'] == h]
        if not subset:
            continue
        c = len(subset)
        d = sum(1 for r in subset if r['din_sql_correct']) / c * 100
        da = sum(1 for r in subset if r['dail_sql_correct']) / c * 100
        print(f"{h:<15} {c:>8} {d:>11.2f}% {da:>11.2f}%")

    # Agreement analysis
    both_correct = sum(1 for r in merged if r['din_sql_correct'] and r['dail_sql_correct'])
    only_din = sum(1 for r in merged if r['din_sql_correct'] and not r['dail_sql_correct'])
    only_dail = sum(1 for r in merged if not r['din_sql_correct'] and r['dail_sql_correct'])
    both_wrong = sum(1 for r in merged if not r['din_sql_correct'] and not r['dail_sql_correct'])

    print("\n--- Agreement Analysis ---")
    print(f"Both correct:       {both_correct:>5} ({both_correct/total*100:>5.2f}%)")
    print(f"Only DIN correct:   {only_din:>5} ({only_din/total*100:>5.2f}%)")
    print(f"Only DAIL correct:  {only_dail:>5} ({only_dail/total*100:>5.2f}%)")
    print(f"Both wrong:         {both_wrong:>5} ({both_wrong/total*100:>5.2f}%)")

    # Latency/tokens from DIN details file
    if din_details_csv and os.path.exists(din_details_csv):
        try:
            df = pd.read_csv(din_details_csv, encoding='utf-8')
            cols = [c.lower() for c in df.columns]
            print("\n--- DIN-SQL Performance (from details CSV) ---")
            if 'latency' in cols:
                lat_col = df.columns[cols.index('latency')]
                print(f"Avg latency: {df[lat_col].mean():.2f}s")
            if 'tokens' in cols or 'token' in cols:
                tok_col = df.columns[cols.index('tokens' if 'tokens' in cols else 'token')]
                print(f"Avg tokens:  {df[tok_col].mean():.0f}")
        except Exception as e:
            pass

    print("=" * 60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--din_file', required=True)
    parser.add_argument('--dail_file', required=True)
    parser.add_argument('--gold_file', required=True, help='dev_gold.sql')
    parser.add_argument('--db_dir', required=True, help='Spider database/ directory')
    parser.add_argument('--dev_json', required=True, help='dev.json')
    parser.add_argument('--din_details_csv', default='', help='predicted_din_sql_details.csv (optional)')
    parser.add_argument('--output_csv', default='comparison_results.csv')
    args = parser.parse_args()

    print("Loading files...")
    din_preds = load_predictions(args.din_file)
    dail_preds = load_predictions(args.dail_file)
    gold = load_gold_file(args.gold_file)
    questions = load_dev_questions(args.dev_json)

    print(f"  DIN predictions:  {len(din_preds)}")
    print(f"  DAIL predictions: {len(dail_preds)}")
    print(f"  Gold entries:     {len(gold)}")
    print(f"  Dev questions:    {len(questions)}")

    # Evaluate both
    print("\nEvaluating DIN-SQL...")
    din_results = evaluate(din_preds, gold, args.db_dir, questions, 'din_sql')

    print("\nEvaluating DAIL-SQL...")
    dail_results = evaluate(dail_preds, gold, args.db_dir, questions, 'dail_sql')

    # Merge & report
    merged = merge_results(din_results, dail_results)
    print_report(merged, args.din_details_csv)

    # Save CSV
    df = pd.DataFrame(merged)
    df.to_csv(args.output_csv, index=False, encoding='utf-8-sig')
    print(f"\n✓ Saved detailed comparison to: {args.output_csv}")

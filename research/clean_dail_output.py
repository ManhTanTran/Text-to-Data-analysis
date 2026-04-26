"""
Script 1: Clean output của DAIL-SQL (DeepSeek trả về SQL bọc trong markdown).
Input:  predicted_dail_sql.txt (output thô từ ask_llm.py)
Output: predicted_dail_sql_cleaned.txt (SQL thuần, mỗi dòng 1 query)
"""
import re
import sys
import os

def extract_sql(raw_line):
    """Extract SQL thuần từ output của DAIL-SQL (DeepSeek)."""
    line = raw_line.strip()

    # Bỏ "SELECT " prefix mà ask_llm.py tự gắn vào đầu (khi output không start với SELECT)
    # Nhận diện: có ``` hoặc có text giải thích
    if line.startswith('SELECT ') and (
        '```' in line or
        'Based on' in line[:80] or
        'the query' in line[:100].lower() or
        'to find' in line[:100].lower() or
        'we can' in line[:100].lower()
    ):
        line = line[len('SELECT '):]

    # Pattern 1: có ```sql ... ```
    match = re.search(r'```sql\s*(.*?)\s*```', line, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip().rstrip(';').strip()

    # Pattern 2: có ``` ... ``` (không có sql tag)
    match = re.search(r'```\s*(SELECT.*?)\s*```', line, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip().rstrip(';').strip()

    # Pattern 3: SELECT thuần ngay từ đầu
    if line.upper().startswith('SELECT '):
        return line.rstrip(';').strip()

    # Pattern 4: tìm SELECT ... FROM ... ở bất kỳ đâu trong text
    match = re.search(r'(SELECT\s+.*?FROM\s+.*?)(?:\.(?:\s|$)|```|$)', line, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip().rstrip(';')

    # Fallback
    return line.strip().rstrip(';')

if __name__ == '__main__':
    input_file = sys.argv[1] if len(sys.argv) > 1 else 'predicted_dail_sql.txt'
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'predicted_dail_sql_cleaned.txt'

    # Đọc với latin-1 để tránh lỗi encoding
    with open(input_file, 'r', encoding='latin-1') as f:
        lines = f.readlines()

    cleaned = []
    for line in lines:
        sql = extract_sql(line)
        # Đảm bảo start với SELECT
        if not sql.upper().startswith('SELECT'):
            sql = 'SELECT ' + sql
        # Loại bỏ newlines trong query
        sql = ' '.join(sql.split())
        cleaned.append(sql)

    with open(output_file, 'w', encoding='utf-8') as f:
        for sql in cleaned:
            f.write(sql + '\n')

    ok = sum(1 for s in cleaned if s.upper().startswith('SELECT ') and 'FROM' in s.upper())
    print(f"✓ Cleaned {len(cleaned)} queries")
    print(f"✓ {ok}/{len(cleaned)} valid SQL (có SELECT...FROM)")
    print(f"✓ Saved to: {output_file}")

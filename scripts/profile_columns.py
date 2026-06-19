#!/usr/bin/env python3
"""
profile_columns.py — 列を走査し、個人情報・機密の可能性を推定してポリシー雛形を提案する。

目的:
- 加工前に「どの列が危ないか」を機械的に洗い出し、見落としを防ぐ(上流ガバナンス工程)。
- 出力されたポリシー雛形を人が確認・調整してから mask_tabular.py に渡す運用を想定。
  完全自動を信用させない設計: あくまで「叩き台」を出すツール。

使い方:
    python profile_columns.py --input data.csv --out suggested_policy.json
"""

import argparse
import json
import os
import re
import sys

try:
    import pandas as pd
except ImportError:
    sys.stderr.write("pandas が必要です: pip install pandas openpyxl --break-system-packages\n")
    raise

# 列名のヒント -> 推奨メソッド (個情法で要配慮/識別子になりやすいもの優先)
NAME_HINTS = [
    (("氏名", "name", "なまえ", "お名前", "担当者"), ("redact", {"token": "[氏名]"})),
    (("マイナンバー", "個人番号", "mynumber"), ("drop", {})),
    (("メール", "mail", "email", "e-mail"), ("hash", {})),
    (("電話", "tel", "phone", "携帯"), ("partial", {"keep_head": 3})),
    (("住所", "address", "所在地"), ("partial", {"keep_head": 3})),
    (("郵便", "zip", "postal"), ("redact", {"token": "[郵便]"})),
    (("生年月日", "birth", "誕生"), ("generalize_date", {"level": "year"})),
    (("年齢", "age"), ("generalize_age", {"bucket": 10})),
    (("id", "番号", "顧客", "会員", "口座"), ("hash", {})),
    (("カード", "card", "クレジット"), ("drop", {})),
    (("備考", "メモ", "note", "comment", "自由"), ("regex_scrub", {})),
]

# 値の中身から推定するパターン
VALUE_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), ("hash", {}), "email_like"),
    (re.compile(r"^0\d{9,10}$"), ("partial", {"keep_head": 3}), "phone_like"),
    (re.compile(r"^\d{3}-?\d{4}$"), ("redact", {"token": "[郵便]"}), "postal_like"),
    (re.compile(r"^\d{12,16}$"), ("drop", {}), "long_number_like"),
]


def guess_for_column(colname, series):
    lname = str(colname).lower()
    for hints, (method, params) in NAME_HINTS:
        if any(h.lower() in lname for h in hints):
            return method, params, f"name_hint:{colname}"

    sample = series.dropna().astype(str).head(50)
    for rx, (method, params), label in VALUE_PATTERNS:
        if len(sample) and (sample.map(lambda s: bool(rx.search(s))).mean() > 0.5):
            return method, params, f"value_pattern:{label}"

    # 高カーディナリティの文字列列 = 識別子の疑い
    nunique = series.nunique(dropna=True)
    if len(series) and nunique / max(len(series), 1) > 0.9 and series.dtype == object:
        return "review", {}, "high_cardinality_review"

    return "keep", {}, "no_signal"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    ext = os.path.splitext(args.input)[1].lower()
    df = pd.read_excel(args.input, dtype=str) if ext in (".xlsx", ".xlsm", ".xls") else pd.read_csv(args.input, dtype=str)

    columns = {}
    rationale = {}
    for col in df.columns:
        method, params, why = guess_for_column(col, df[col])
        rule = {"method": method}
        rule.update(params)
        columns[col] = rule
        rationale[col] = why

    policy = {
        "name": f"suggested-policy-for-{os.path.basename(args.input)}",
        "regulation": "APPI_Japan",
        "description": "自動生成された叩き台。必ず人が確認・調整してから使用すること。",
        "default_action": "review",
        "k": 2,
        "quasi_identifiers": [c for c, r in columns.items()
                              if r["method"] in ("generalize_age", "generalize_date")],
        "columns": columns,
        "_rationale": rationale,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(policy, f, ensure_ascii=False, indent=2)

    print(f"[OK] ポリシー雛形を生成: {args.out}")
    print("     review/keep と判定された列は必ず人手で確認してください。")
    flagged = [c for c, r in columns.items() if r["method"] not in ("keep",)]
    print(f"     要処理候補 {len(flagged)} 列: {', '.join(flagged) if flagged else '(なし)'}")


if __name__ == "__main__":
    main()

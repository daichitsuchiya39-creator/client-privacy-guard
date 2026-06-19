#!/usr/bin/env python3
"""
mask_tabular.py — 表形式データ(CSV/Excel)のマスキング・匿名化エンジン

設計思想:
- ポリシー(JSON)で「どの列を・どう処理するか」を宣言的に定義する。
  手作業の置換ではなく、ポリシー駆動で再現性・監査可能性を担保する。
- 不可逆(ハッシュ/抑制/一般化)をデフォルトとし、可逆(暗号化)は明示時のみ。
- 処理後に必ず監査ログ(JSON)を出力し、「いつ・何を・どう処理したか」を残す。

使い方:
    python mask_tabular.py --input data.csv --policy policy.json \
        --output masked.csv --audit audit.json

ポリシーJSONの例は references/policy_template.json を参照。
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone

try:
    import pandas as pd
except ImportError:
    sys.stderr.write(
        "pandas が必要です: pip install pandas openpyxl --break-system-packages\n"
    )
    raise


# --- 各マスキング手法 -------------------------------------------------

def _load_salt(policy):
    """ハッシュ用ソルト。ポリシーに無ければ生成し監査ログに残す方針。
    同一値→同一ハッシュ(参照整合性)を保つため、1回の処理で固定する。"""
    salt = policy.get("hash_salt")
    if not salt:
        salt = secrets.token_hex(16)
    return salt


def m_hash(series, salt):
    """不可逆。SHA-256。同一入力は同一出力になり、結合キーとして使える。"""
    def h(v):
        if pd.isna(v):
            return v
        return hashlib.sha256((salt + str(v)).encode("utf-8")).hexdigest()[:16]
    return series.map(h)


def m_redact(series, token="***"):
    """完全抑制。値を固定トークンに置換。最も安全だが情報量はゼロ。"""
    return series.map(lambda v: v if pd.isna(v) else token)


def m_partial(series, keep_head=1, keep_tail=0, fill="*"):
    """部分マスク。先頭/末尾を残し中間を伏せる。例: 田** / 09012345678→090*****678"""
    def p(v):
        if pd.isna(v):
            return v
        s = str(v)
        if len(s) <= keep_head + keep_tail:
            return fill * len(s)
        return s[:keep_head] + fill * (len(s) - keep_head - keep_tail) + (s[len(s) - keep_tail:] if keep_tail else "")
    return series.map(p)


def m_generalize_age(series, bucket=10):
    """一般化。数値を区間に丸める。例: 37→「30代」。k-匿名化の準備に有効。"""
    def g(v):
        if pd.isna(v):
            return v
        try:
            n = int(float(v))
        except (ValueError, TypeError):
            return v
        low = (n // bucket) * bucket
        return f"{low}-{low + bucket - 1}"
    return series.map(g)


def m_generalize_date(series, level="month"):
    """日付の粒度を落とす。level: year / month。例: 2024-03-15→2024-03"""
    def g(v):
        if pd.isna(v):
            return v
        try:
            d = pd.to_datetime(v)
        except (ValueError, TypeError):
            return v
        return d.strftime("%Y") if level == "year" else d.strftime("%Y-%m")
    return series.map(g)


def m_regex_scrub(series, patterns):
    """セル内テキストから個人情報パターン(メール/電話等)を除去。
    自由記述列の「うっかり混入」対策。patterns は {regex: 置換文字列}。"""
    compiled = [(re.compile(p), repl) for p, repl in patterns.items()]
    def scrub(v):
        if pd.isna(v):
            return v
        s = str(v)
        for rx, repl in compiled:
            s = rx.sub(repl, s)
        return s
    return series.map(scrub)


def m_drop(df, col):
    """列ごと削除。クライアント目的に不要な機密列は持たないのが最善。"""
    return df.drop(columns=[col])


# 自由記述列向けの既定スクラブパターン(日本の個人情報を想定)
DEFAULT_SCRUB_PATTERNS = {
    r"[\w.+-]+@[\w-]+\.[\w.-]+": "[EMAIL]",
    r"\d{2,4}-\d{2,4}-\d{3,4}": "[PHONE]",
    r"0\d{9,10}": "[PHONE]",
    r"\d{3}-?\d{4}": "[POSTAL]",
    r"〒\s*\d{3}-?\d{4}": "[POSTAL]",
    r"\d{12}": "[MYNUMBER_LIKE]",
}


# --- メイン処理 -------------------------------------------------------

def apply_policy(df, policy, audit):
    salt = _load_salt(policy)
    audit["hash_salt_used"] = salt
    actions = audit.setdefault("column_actions", [])

    columns = policy.get("columns", {})

    for col, rule in columns.items():
        method = rule.get("method")
        rec = {"column": col, "method": method, "params": {k: v for k, v in rule.items() if k != "method"}}

        if col not in df.columns:
            rec["status"] = "skipped_not_found"
            actions.append(rec)
            continue

        if method == "drop":
            df = m_drop(df, col)
        elif method == "hash":
            df[col] = m_hash(df[col], salt)
        elif method == "redact":
            df[col] = m_redact(df[col], rule.get("token", "***"))
        elif method == "partial":
            df[col] = m_partial(df[col], rule.get("keep_head", 1),
                                rule.get("keep_tail", 0), rule.get("fill", "*"))
        elif method == "generalize_age":
            df[col] = m_generalize_age(df[col], rule.get("bucket", 10))
        elif method == "generalize_date":
            df[col] = m_generalize_date(df[col], rule.get("level", "month"))
        elif method == "regex_scrub":
            pats = rule.get("patterns") or DEFAULT_SCRUB_PATTERNS
            df[col] = m_regex_scrub(df[col], pats)
        elif method == "keep":
            pass  # 明示的に「そのまま残す」と宣言された列(過剰隠蔽の回避)
        else:
            rec["status"] = "error_unknown_method"
            actions.append(rec)
            continue

        rec["status"] = "applied"
        actions.append(rec)

    # ポリシー未記載列の扱い: default_action で安全側に倒す
    default = policy.get("default_action", "review")
    unlisted = [c for c in df.columns if c not in columns]
    audit["unlisted_columns"] = unlisted
    audit["unlisted_default_action"] = default
    if default == "drop" and unlisted:
        df = df.drop(columns=unlisted)
        audit["unlisted_dropped"] = unlisted
    elif default == "redact" and unlisted:
        for c in unlisted:
            df[c] = m_redact(df[c])
        audit["unlisted_redacted"] = unlisted
    # default == "review" / "keep" のときは触らず、監査ログで人手確認を促す

    return df


def k_anonymity_check(df, quasi_identifiers, k=2):
    """準識別子の組み合わせでk未満のグループ(再識別リスク)を検出。"""
    qi = [c for c in quasi_identifiers if c in df.columns]
    if not qi:
        return {"checked": False, "reason": "no_quasi_identifiers_in_data"}
    sizes = df.groupby(qi, dropna=False).size()
    violating = int((sizes < k).sum())
    return {
        "checked": True,
        "quasi_identifiers": qi,
        "k": k,
        "violating_groups": violating,
        "min_group_size": int(sizes.min()) if len(sizes) else 0,
        "pass": violating == 0,
    }


def read_table(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str)


def write_table(df, path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        df.to_excel(path, index=False)
    else:
        df.to_csv(path, index=False)


def main():
    ap = argparse.ArgumentParser(description="表形式データのポリシー駆動マスキング")
    ap.add_argument("--input", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--audit", required=True, help="監査ログ(JSON)の出力先")
    args = ap.parse_args()

    with open(args.policy, encoding="utf-8") as f:
        policy = json.load(f)

    df = read_table(args.input)
    n_rows, n_cols = df.shape

    audit = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input_file": os.path.abspath(args.input),
        "output_file": os.path.abspath(args.output),
        "policy_name": policy.get("name", "unnamed"),
        "policy_regulation": policy.get("regulation", "unspecified"),
        "input_rows": int(n_rows),
        "input_columns": list(df.columns),
    }

    df = apply_policy(df, policy, audit)

    qi = policy.get("quasi_identifiers", [])
    if qi:
        audit["k_anonymity"] = k_anonymity_check(df, qi, policy.get("k", 2))

    write_table(df, args.output)
    audit["output_rows"] = int(df.shape[0])
    audit["output_columns"] = list(df.columns)

    with open(args.audit, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)

    print(f"[OK] {args.input} -> {args.output}")
    print(f"     監査ログ: {args.audit}")
    if "k_anonymity" in audit and not audit["k_anonymity"].get("pass", True):
        print(f"[WARN] k-匿名性違反グループ: {audit['k_anonymity']['violating_groups']} 件。"
              f"準識別子の一般化を検討してください。")


if __name__ == "__main__":
    main()

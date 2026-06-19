---
name: client-privacy-guard
description: >-
  クライアントから預かった表形式データ(CSV/Excel)を加工・分析する前に、個人情報・機密情報を
  ポリシー駆動で安全にマスキング/匿名化するためのスキル。日本の個人情報保護法(APPI)を中心に、
  委託先としての安全管理措置・監査ログ・k-匿名性チェックまでを一連の工程として提供する。
  次のような場合に必ずこのスキルを使うこと: クライアントデータ・顧客データ・他社データの加工や
  分析を頼まれたとき、「マスキング」「匿名化」「アノニマイズ」「個人情報を消して」「機密を伏せて」
  「秘匿化」「仮名化」と言われたとき、CSV/Excelの個人情報を含む列を扱うとき、納品前に
  プライバシー観点でチェックしたいとき。クライアントの生データを後続処理(分析・別のAI・レポート作成)に
  渡す前段として、原則このスキルを通すこと。
---

# Client Privacy Guard

クライアント機密を守りながら表形式データを加工するための、3工程ワークフロー。
個人事業/小規模で他社データを請け負う際に、「適切に秘匿化する工程」を標準化し、
サービスの信頼性を担保することを目的とする。

## 基本原則

- **生データを後続処理に直接渡さない。** 必ずこのスキルでマスキングした出力を使う。
- **ポリシー駆動。** 手作業の置換ではなく、JSONポリシーで宣言的に処理し再現性と監査可能性を残す。
- **過剰隠蔽を避ける。** 全部伏せれば安全だがデータは無価値になる。直接識別子は強く落とし、
  分析に要る属性は粒度を残す（generalize/keep）。判断根拠は記録する。
- **不可逆をデフォルトに。** hash/redact/drop/generalize を基本とする。
- **監査ログを必ず残す。** 「いつ・何を・どう処理したか」を audit.json に出力する。

## ワークフロー（この順で進める）

### 工程1: ガバナンス — ポリシー策定（上流）

1. 入力データの列構成を把握する。`profile_columns.py` で危険な列を機械的に洗い出し、
   ポリシーの叩き台を生成する：

   ```bash
   pip install pandas openpyxl --break-system-packages
   python scripts/profile_columns.py --input <data.csv> --out suggested_policy.json
   ```

2. 生成された `suggested_policy.json` を**人の目で必ず確認・調整**する。
   - `review`/`keep` 判定の列は本当にそれでよいか確認する。
   - `references/policy_template.json` を手本に、案件の目的に合わせて method を調整する。
   - 利用可能な method: `hash`(不可逆・結合可) / `redact`(完全抑制) / `partial`(部分マスク) /
     `generalize_age`(年代化) / `generalize_date`(年・月粒度) / `regex_scrub`(自由記述内の混入除去) /
     `drop`(列削除) / `keep`(あえて残す明示)。
   - 過剰隠蔽の判断は `references/compliance_checklist.md` の第3節を参照。

### 工程2: 実装 — マスキング適用（中流）

確定したポリシーで加工を実行する。出力データと監査ログが生成される：

```bash
python scripts/mask_tabular.py \
  --input <data.csv> \
  --policy <policy.json> \
  --output masked.csv \
  --audit audit.json
```

- 同一値→同一ハッシュなので、複数テーブルを跨ぐ結合キーは `hash` で保持できる。
- `quasi_identifiers` を設定すると k-匿名性チェックが走り、再識別リスクのある
  グループ数が audit.json に記録される。違反が出たら準識別子を `generalize` で粗くする。

### 工程3: コンプライアンス監査（下流・納品前）

`references/compliance_checklist.md` を開き、audit.json と突き合わせてチェックリストを確認する。
特に以下を確認：
- 個人識別符号（マイナンバー等）が残っていないか
- ポリシー未記載列（`unlisted_columns`）が妥当に処理されたか
- `k_anonymity.pass` が true か
- 作っているのが仮名加工情報か匿名加工情報か、目的と整合するか
- `hash_salt_used`（案件鍵）の保管/破棄運用が決まっているか

監査結果を案件記録として保存し、必要なら audit.json をそのまま証跡にする。

## やってはいけないこと

- 確認なしに `profile_columns.py` の出力を本番適用しない（叩き台にすぎない）。
- 生データを直接、別AI・外部サービス・レポートに貼り付けない。
- 「とりあえず全部 redact」で済ませない（価値を損ない、かえって信頼を下げる）。
- 法的判断を断定しない。要配慮個人情報や越境移転が絡む場合は社内法務にエスカレーションする。

## 参照ファイル

- `references/policy_template.json` — 個情法準拠のポリシー雛形（複製して使う）
- `references/compliance_checklist.md` — 納品前チェックリストと法令観点
- `scripts/profile_columns.py` — 列プロファイリングとポリシー叩き台生成
- `scripts/mask_tabular.py` — ポリシー駆動マスキングエンジン

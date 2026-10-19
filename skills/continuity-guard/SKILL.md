---
name: continuity-guard
description: Restore project goals, constraints and unresolved evidence on start/resume/context loss; checkpoint substantial work at milestones. Skip trivial one-turn work and unchanged context.
---
# Codex Continuity Guard — global

スローガン: **品質を落とさない程度に軽くする。** 原記録の保存と、モデルへ毎回渡す情報を分離する。

共通core: NGCG checkoutの `continuity.py`（以下 `<NGCG_ROOT>/continuity.py`）。対象project rootをcwdにする。

- Charterがあるprojectの開始・再開・状態不足時に一度 `py -3.10 -X utf8 -B "<NGCG_ROOT>/continuity.py" restore --brief`。既定2500文字の索引と原文抜粋を読む。取得済み状態を毎turn再読しない。
- 原目標・制約・承認条件・FAIL/BLOCKED/UNKNOWNは原本から削除しない。省略箇所は未読であり、解決済み・許可済みと解釈しない。必要なsourceだけ `recall <docs/ai-continuityからの相対パス> [--task 完全一致ID | --section 完全一致見出し]` で読む。継続pageには表示された `--offset` と `--sha256` を使う。変更検知時は再確認する。適用範囲が不明な制約・未解決は確認するまでHOLD。`restore --full` は明示的に全記録が必要な場合だけ。
- substantialな複数session作業だけ `init --no-hooks --mission "<人間の目的>" [--constraints "<明示された制約>"]`。導入しただけで全projectをinitしない。既存Charter/CURRENT/証拠を上書きしない。
- 重要工程完了・中断前・重要状態変更で `checkpoint --task <ID> --agent <NAME> --status <STATUS> --evidence <PATH>`。system側の記録があれば重複AI要約は作らない。CURRENT本文は保持されるので、次手順・未決・阻害要因・証拠を短く更新する。
- 明示変更はAMENDMENTS、実検証はEVIDENCE_INDEX。完了claimには対応task IDと実command/log SHAを持つmachine receiptが必要。必要な実検証を `evidence_runner.py --project <ROOT> --task <ID> --timeout <SECONDS> -- <COMMAND...>` で記録できる。旧結果の包装や確認だけのrerunをしない。receiptはcommand実行の証拠で、試験十分性やproduct達成の証明ではない。
- projectの重要milestone完了claimやdrift確認で `audit`。小修正・通常の質問で新規initや全体auditを重ねず、必要な検証をまとめて実行する。self-hash/USER表示だけで外部承認を捏造しない。旧承認・旧receipt不足はHOLDとし原記録を保持する。restoreで読めても承認済みではない。
- `approve-charter` は人間が実TTYで原Charterを読みchallengeを入力する操作。AIから自動実行・承認receipt生成をしない。承認不足を空init/旧記録削除で回避しない。
- Hooksとproject別Team Chatは既定OFF。chat ONからagent自動起動や実Codex配送を推定しない。SQLite状態・承認recordはproject別で旧版へ無断流用・削除しない。
- 最新人間指示を最優先。context-mode/superpowers/Astra相談は設計上重要と主担当が判断した場合のみ。通常作業の自動適用・反復相談をしない。CCGのrestore/checkpointと実検証は別の責任として維持する。

使い方はNGCGのREADME.md、復元契約はdocs/DESIGN.mdを参照。導入と既存projectのCharter承認完了は別。

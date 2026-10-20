# NGCG

**品質を落とさない程度に軽くする。**

NGCGは、CCG（Continuity Guard）を引き継ぐ、AI coding agent用の継続作業支援ツールです。目標・制約・進捗・検証証拠を原記録として残し、再開時には短い索引を読み、必要な原文だけを追加取得します。

Python標準ライブラリだけで動作します。記録・索引・ハッシュ検証にモデルやクラウドサービスは不要です。Python 3.10以上を使用してください。主な検証環境はWindows／PowerShellです。

## 使い方

このリポジトリをcloneし、**作業対象のプロジェクトをカレントディレクトリ**にして実行します。以下の`<NGCG_ROOT>`はclone先の絶対パスに置き換えてください。

```sh
python <NGCG_ROOT>/continuity.py restore --brief
```

- 初回のbriefは既定2,500文字以内。原文の行位置、SHA-256、検証状態の件数、省略箇所の確認手順を返します。
- 同じ文脈では復元を繰り返さず、必要なsourceだけを読みます。
- 省略は未読です。適用される目的・制約・未解決事項を確認し、範囲が不明ならHOLDとします。索引は実行許可や承認を与えません。

```sh
python <NGCG_ROOT>/continuity.py recall CURRENT.md
python <NGCG_ROOT>/continuity.py recall CURRENT.md --section "Next steps"
python <NGCG_ROOT>/continuity.py recall EVIDENCE_INDEX.md --task "Task-ID"
python <NGCG_ROOT>/continuity.py recall AMENDMENTS
```

sourceは`docs/ai-continuity/`からの相対パスです。続きを読む場合は同じsource・選択条件で、返された`--offset`と`--sha256`を指定します。原本の変更を検出したら読み直します。全記録の展開が必要なときだけ`restore --full`を使います。

### 新しいプロジェクトの記録

複数sessionにまたがる実質的な作業で必要な場合だけ初期化します。通常の質問や小修正に初期化は不要です。

```sh
python <NGCG_ROOT>/continuity.py init --no-hooks --mission "このプロジェクトの目的"
python <NGCG_ROOT>/continuity.py checkpoint --task TASK-1 --agent Codex
```

初期化はプロジェクト内に継続文書とSkillを作成します。既存の原記録を置換せず、HooksとTeam Chatは既定OFFです。Charterの承認は、人間が原文を確認して対話端末から`approve-charter`を実行する別の操作です。初期化や自己SHAを人間の承認に転用しません。

### 検証の記録

```sh
python <NGCG_ROOT>/evidence_runner.py --project . --task TASK-1 -- python -m unittest discover -s tests
python <NGCG_ROOT>/continuity.py audit
```

実行command・終了コード・log SHAをreceiptに記録します。再検証でも過去のFAILを一覧から削除しません。UNKNOWNや任意形式のタスクIDも保持します。receiptはcommand実行の証拠であり、試験の十分性や製品全体の完成を証明するものではありません。全体auditは重要な節目やdrift確認時に実行し、小修正ごとに不要な監査・要約を重ねない運用にします。

## 任意のGit worktree比較（親＋2つの研究室）

一つの方法を並行開発するだけなら、従来どおり同じ作業フォルダで構いません。**2案を独立して実装・比較する実益があるとAIが判断した場合だけ**、Git標準機能で比較用のlinked worktreeを2本作れます。

親は元のmain worktreeで、子はそれぞれ独立したブランチ・作業フォルダです。これはGit worktreeの本数としては「親1本＋子2本」の構成です。NGCGがworktreeやエージェントを勝手に作成することはありません。

```sh
# 元のプロジェクトフォルダで（作業の基準となる目標・制約は先に保存する）
git worktree add -b experiment-a ../project-experiment-a
git worktree add -b experiment-b ../project-experiment-b

# 親または子のフォルダから、現在の関連研究室をまとめて確認
python <NGCG_ROOT>/continuity.py worktrees
python <NGCG_ROOT>/continuity.py worktrees --json
```

**各子のフォルダをカレントディレクトリとして**、普段どおり`restore --brief`、`recall`、`checkpoint`、`evidence_runner.py`を使います。文書（`docs/ai-continuity/`）は各ブランチの作業ツリー内に、SQLite履歴は各フォルダ固有の既存保存領域に残ります。親の`worktrees`は双方の作業状態・証拠件数・直近チェックポイントを読み取り専用で表示します。**子のPASSや承認を、親プロジェクト全体のPASSや承認へ自動昇格しません。**

注意：子は作成時点のコミット済み文書を引き継ぎますが、その後の編集は自動同期されません。親で方針と採用条件を管理し、各子は独立に記録します。比較後の採否・Gitマージ・承認は明示的な判断が必要です。Git管理外のSQLite履歴もマージされません。2案の比較が不要ならworktreeを増やさないでください。

## 任意のローカルHub

```sh
python <NGCG_ROOT>/hub_runtime.py --project . team on
python <NGCG_ROOT>/hub_runtime.py --project . serve
python <NGCG_ROOT>/hub_runtime.py --project . team off
```

localhostのUIで記録を閲覧します。部署数やチーム構成の既定値は設けず、全体連絡と使用済みchannelだけを表示します。UIからモデルを起動せず、Codexへの自動配送も行いません。

## 開発・検証

リポジトリのルートから実行します。

```sh
python -B -m unittest discover -s tests -v
node --check hub_ui/app.js
```

Pythonのテストには外部パッケージは不要です。Node.jsはUIのJavaScript構文確認にだけ使用します。

| 構成 | 内容 |
|---|---|
| `continuity.py` | CLI、初期化、checkpoint、audit |
| `loader.py` / `recovery.py` | 原本解析、上限付き復元、原文の段階取得 |
| `evidence_runner.py` / `validator.py` | command実行証拠と完了claimの検査 |
| `state_store.py` / `approval_guard.py` | プロジェクト別SQLiteと外部承認record |
| `hub_runtime.py` / `hub_server.py` / `hub_ui/` | 任意のローカルHub |
| `skills/continuity-guard/` | 個人パスに依存しない運用Skill |
| `tests/` | 復元・原本保持・解析・CLI・Hubの回帰テスト |

既存環境との互換性のため、CLI名・Skill名・`CCG`の保存先識別子は維持しています。既存の稼働CCGを自動置換せず、SQLiteや承認recordを移行・削除しません。

設計・予算の制約・外部参考は[docs/DESIGN.md](docs/DESIGN.md)を参照してください。文字数予算はモデルの正確なトークン数ではなく、作業全体の課金削減率や品質不変を保証するものではありません。

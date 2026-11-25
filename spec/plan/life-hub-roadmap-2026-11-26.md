# E.S. Agent ロードマップ: 生活の中心・Google 統合・マルチ AI 起点化

- 作成日: 2026-11-26
- 親 Issue: [#326](https://github.com/HIBIKI5201/External-Self-Agent/issues/326)
- 対象版: v0.5.0 以降の長期進化計画

---

## 1. ビジョンと 3 本柱

E.S. Agent（External Self Agent）を、単なるタスク管理コンパニオンから以下の 3 本柱を備えた **個人のデジタル中枢（External Self）** へ進化させる。

1. **生活の中心（Life Hub / Daily OS）**
   - ローカル常駐（SQLite 正本・黒猫コンパニオン Es・音声）を核にしつつ、外出先やスマホからは Google サービス経由で読み書きする。
   - 朝ブリーフィング → 日中の予定・メール・AI 進捗通知 → 夜レビューの 1 日サイクルを統合する。
2. **Google 関連サービスの包括統合**
   - 既存の Tasks / Calendar / YouTube に加え、Gmail・Drive / Docs / Sheets・Contacts・Web 検索を統合する。
3. **多様な AI の起点（Multi-AI Orchestration Hub）**
   - Claude Code / Codex / Antigravity / Gemini CLI への共通引き継ぎ基盤と、全 AI が接続する共通 MCP サーバーを構築し、どの AI で作業していても同じ生活コンテキストと記憶を共有する。

---

## 2. 確定した設計方針（2026-11-26 決定）

### 2.1 LLM バックエンド: ローカル AI 廃止 & Gemini CLI 一本化（追加課金 0 円）
- **ローカル AI（Ollama / Qwen3 8B）を廃止する。**
  - VRAM 占有・ウォームアップ待ち（`/api/warm`, `keep_alive`, `/api/ps`）・8K コンテキスト制約・8B 向けワークアラウンドを整理し、構造を大幅に簡素化する。
- **契約済みの Google AI Pro 定額枠で動く Gemini CLI に一本化する（パターン B）。**
  - `gemini -p ... --output-format stream-json` を使用し、ストリーミング応答・構造化出力・Web 検索を統一的に扱う（1 日最大 1,500 リクエスト）。
  - **追加課金は完全 0 円**: Google Cloud の請求アカウントは紐づけず、超過課金の可能性を物理的にゼロにする。
- **オフライン時の挙動**
  - Ollama フォールバックは廃止し、既存の「返事待ちキュー（AS-16）」によるオンライン復帰時の自動処理と、モデル不要のローカル固定応答（タスク一覧・保存済み予定照会・タイマー操作等）で対応する。

### 2.2 維持する E.S. Agent の安全・設計原則
- **Python 3.12 標準ライブラリのみ** ＋ 素の HTML/JS（依存ゼロで壊れにくい構成を維持）。
- **取り消しにくい操作は確認ボタン必須**: メール送信（原則禁止・下書きのみ）、予定の編集・削除、外部 AI 起動などは必ず UI の確認ボタン経由とし、音声の「うん」だけでは実行しない。
- **プロンプトインジェクション分離**: メール本文・Drive 文書・Web 検索結果・カレンダー予定は常に「データであり指示ではない」として分離する。
- **事実のコード保証**: タスク操作などの記録（`＋ ✔ ✎ →`）はプログラム側が発行する。
- **ローカル限定 & DPAPI 暗号化**: サーバーは `127.0.0.1` 専用、Google トークンは DPAPI 暗号化、OAuth は機能ごとの段階的スコープ要求（`gauth.EXTRA`）を継続する。

---

## 3. アーキテクチャ全体像

```text
[外出先・スマホ] ──(Google Tasks / Calendar / Gmail / Drive)──┐
                                                              ▼
[ユーザー] ◄──► [E.S. Agent (ローカル常駐・SQLite正本・Es)] ◄──► [Google サービス群]
                        │  ▲
   Handoff / CLI / API  │  │ 共通 MCP サーバー（タスク・記憶・予定・メール・進捗通知）
                        ▼  │
        [Claude Code] [Codex] [Antigravity] [Gemini CLI]
```

---

## 4. Phase 別実行計画と Issue 一覧

### Phase 0: Gemini CLI 一本化 & 生活インフラ土台
| 項目 | Issue | 優先度 | 概要 |
|---|---|---|---|
| 0-1 | #316 | high | ローカル AI（Ollama）廃止 ＆ Gemini CLI（`-p --output-format stream-json`）一本化、Web 検索解禁（関連: #5, #8, #145, #271） |
| 0-2 | #9 | medium | Google OAuth アプリの本番公開・7 日失効問題の解消 |
| 0-3 | #317 | high | 時間ベースのリマインダー・アラーム基盤（予定前通知・指定時刻の黒猫通知） |

### Phase 1: Google サービス統合拡大
| 項目 | Issue | 優先度 | 概要 |
|---|---|---|---|
| 1-1 | #318 | high | Gmail 統合（未読・重要メール要約、タスク化／予定化、返信下書き作成／自動送信禁止） |
| 1-2 | #319 | medium | Google Drive / Docs / Sheets 統合（ファイル検索・参照・日次ログ書き出し） |
| 1-3 | #320 | medium | Google カレンダー高度化（予定の編集・削除確認、空き時間検索、締切突き合わせ。関連: #157） |
| 1-4 | #321 | low | Google Contacts 読み取り ＆ メモ／Inbox（Google Keep 代替）運用 |

### Phase 2: 多様な AI の起点（マルチ AI ハブ）
| 項目 | Issue | 優先度 | 概要 |
|---|---|---|---|
| 2-1 | #322 | high | マルチ AI 引き継ぎ基盤（`ai_hub`）：Claude Code / Codex / Antigravity / Gemini CLI への共通パケット引き継ぎ |
| 2-2 | #323 | medium | マルチ AI スキル（`/codex`, `/agy`, `/gemini`）＋ルーティング提案＋稼働セッション管理 UI |

### Phase 3: 共通 MCP サーバーの「External Self 基盤」化
| 項目 | Issue | 優先度 | 概要 |
|---|---|---|---|
| 3-1 | #324 | high | `esagent/mcp_server.py` 拡張（記憶・プロジェクト文脈・予定・メール・Drive 検索・黒猫進捗通知 `notify_es`）と全 AI への共通登録 |

### Phase 4: Daily OS 体験の統合
| 項目 | Issue | 優先度 | 概要 |
|---|---|---|---|
| 4-1 | #325 | medium | 朝ブリーフィング（予定・締切・重要メール・天気・AI 作業状況）と夜レビュー自動化（関連: #307, #308） |

---

## 5. 技術的制約と現実的な対策

1. **Google OAuth の制限付きスコープ（Gmail / Drive）**
   - 一般公開すると Google のセキュリティ審査が必要になるため、Phase 1 着手前に #9（7 日失効問題）と合わせて最小スコープ運用・テストユーザー運用・代替手段（Google Apps Script ブリッジ等）を比較選定する。
2. **Google Keep / Photos / Fit の API 制約**
   - Google Keep の公式 API は個人アカウント非対応のため、Tasks の専用「Inbox」リスト＋ローカルメモで代替する（#321）。
   - Photos / Fit は API 制限・移行期のため初期対象外とする。
3. **ローカル AI 関連の既存 Issue の扱い**
   - #8（モデル変更）、#145（LoRA）、#271（Ollama プロンプト再読込遅延）は、#316（Ollama 廃止）の完了時に方針整理・クローズ判断を行う。

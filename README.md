# E.S. Agent（External Self Agent）

この PC に常駐する、ローカル LLM のタスク管理アシスタント兼作業コンパニオン。
コンパニオンの **Es**（デスクトップの黒猫）が、隣で一緒に作業してくれる。

- タスクを 1 つのリストで横断管理（Google ToDo と双方向同期、締切は Google カレンダーへ）
- 集中タイマー、雨・風の環境音、YouTube / YouTube Music の BGM、なかよし度
- `/` で呼び出すスキル（`skills/*/SKILL.md` で定義。自分のスキルは `data/skills/*/SKILL.md` に置く。以前 `skills/<名前>/SKILL.md` を直接書き換えていた場合は、`git pull` の前に `data/skills/<名前>/SKILL.md` へコピーし、`git checkout -- skills/<名前>/SKILL.md` で元に戻す）
- 重い作業は Claude Code に引き継ぎ、結果は MCP でタスクに書き戻す
- Es の返事の読み上げ（VOICEVOX、任意。仕様 AS-19）と、マイクで話しかける会話モード（Edge の音声認識、仕様 AS-20）
- Google カレンダーの読み取りと予定の追加（チャット画面のカレンダー欄、「明日の予定は？」、仕様 AS-11）
- 返事の 👍 / 👎 と `/feedback`（返事への評価を記録し、直す手がかりにする）
- ほかのリポジトリの TODO ファイルを読み取り専用のタスクとして取り込む（仕様 AS-18）

## 必要なもの

- Windows 11、Python 3.12（標準ライブラリのみ使用）
- [Ollama](https://ollama.com/) と `qwen3:8b`
- Microsoft Edge（チャット画面。会話モードはマイクと Edge の音声認識を使う。API キーは要らない）
- Claude Code（引き継ぎ・MCP）
- 任意: [VOICEVOX](https://voicevox.hiroshiba.jp/)（読み上げ。`config.json` の `voicevox_*`）

## セットアップ

```powershell
git clone https://github.com/HIBIKI5201/External-Self-Agent C:\ESAgent
ollama pull qwen3:8b
powershell -ExecutionPolicy Bypass -File C:\ESAgent\install.ps1
```

- 初回起動時に `config.example.json` から `config.json` が作られる。`project_roots` などを自分の環境に合わせる。
- Google 連携を使う場合は、OAuth クライアント（デスクトップアプリ）の JSON を `secrets/google_client.json` に置き、画面の「Google ToDo に接続」から許可する。
- Google カレンダーを読む・予定を入れる場合は、カレンダー欄の「Google カレンダーに接続」から追加の権限（予定の読み書き `calendar.events`・カレンダー一覧の読み取り `calendar.calendarlist.readonly`）を許可する。表示するカレンダーは欄の中で選ぶ。
- YouTube の検索・自分のプレイリストを使う場合は、同じ Google Cloud プロジェクトで YouTube Data API v3 を有効にし、BGM 欄の「GoogleでYouTubeに接続」から読み取りを許可する。[設定手順と再生の制限](docs/youtube.md)。
- ほかのリポジトリの TODO（例: WarframeDocument の装備）をタスクに取り込む場合は、`config.json` の `linked_todos` に `{"name": "WarframeDocument 装備", "project": "Warframe", "file": "C:\\WarframeDocument\\TODO\\data\\01-equipment.toml"}` を足す。読み取りだけで、完了は元のファイルで付ける（仕様 AS-18）。
- 読み上げを使う場合は VOICEVOX を入れ、チャット画面の読み上げをオンにする。エンジン（`vv-engine\run.exe`）は Es が必要なときに起動し、しばらく話さないと止める。場所が既定と違うときは `voicevox_engine_path` を設定する。
- 起動: ログオン時に自動。チャット画面は **Ctrl+Alt+E** または黒猫のダブルクリック。

## ドキュメント

| パス | 内容 |
|---|---|
| `spec/` | 仕様書（`spec/README.md` から） |
| `CLAUDE.md` | 開発ハーネス（構成・動作確認・反映手順） |
| `AGENTS.md` | AI エージェント向けの入口（仕様・規則の場所） |
| `docs/git-workflow.md` | Git 運用規定（Issue 起点） |
| `docs/youtube.md` | YouTube の接続の設定手順と再生の制限 |
| `docs/conversation-eval.md` | 会話の評価（モデル・プロンプトの版の比べ方） |
| `docs/log-diagnostics.md` | ローカルログの診断コマンド |
| `CHANGELOG.md` | 変更履歴 |

## 開発する

- 稼働用の `C:\ESAgent` は常に `main` のままにし、開発は worktree の `C:\ESAgent-dev`（ポート 8766）で行う。手順は [`CLAUDE.md`](CLAUDE.md) と [`docs/git-workflow.md`](docs/git-workflow.md)。
- クローンしたら `git config core.hooksPath .githooks`（`main` への直接 push を止める）。
- テストは `python tests/run.py`（全部）／`python tests/run.py --offline`（モデル不要のみ、CI と同じ）。

## クレジット

- 黒猫のドット絵: [Pet Cats Pack](https://luizmelo.itch.io/pet-cat-pack) by LuizMelo（CC0）

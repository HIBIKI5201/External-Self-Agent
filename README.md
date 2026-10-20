# E.S. Agent（External Self Agent）

この PC に常駐する、ローカル LLM のタスク管理アシスタント兼作業コンパニオン。
コンパニオンの **Es**（デスクトップの黒猫）が、隣で一緒に作業してくれる。

- タスクを 1 つのリストで横断管理（Google ToDo と双方向同期、締切は Google カレンダーへ）
- 集中タイマー、雨・風の環境音、YouTube / YouTube Music の BGM、なかよし度
- `/` で呼び出すスキル（`skills/*/SKILL.md` で定義。自分のスキルは `data/skills/*/SKILL.md` に置く）
- 重い作業は Claude Code に引き継ぎ、結果は MCP でタスクに書き戻す

## 必要なもの

- Windows 11、Python 3.12（標準ライブラリのみ使用）
- [Ollama](https://ollama.com/) と `qwen3:8b`
- Microsoft Edge（チャット画面）、Claude Code（引き継ぎ・MCP）

## セットアップ

```powershell
ollama pull qwen3:8b
powershell -ExecutionPolicy Bypass -File C:\ESAgent\install.ps1
```

- 初回起動時に `config.example.json` から `config.json` が作られる。`project_roots` などを自分の環境に合わせる。
- Google 連携を使う場合は、OAuth クライアント（デスクトップアプリ）の JSON を `secrets/google_client.json` に置き、画面の「Google ToDo に接続」から許可する。
- YouTube の検索・自分のプレイリストを使う場合は、同じ Google Cloud プロジェクトで YouTube Data API v3 を有効にし、BGM 欄の「GoogleでYouTubeに接続」から読み取りを許可する。[設定手順と再生の制限](docs/youtube.md)。
- ほかのリポジトリの TODO（例: WarframeDocument の装備）をタスクに取り込む場合は、`config.json` の `linked_todos` に `{"name": "WarframeDocument 装備", "project": "Warframe", "file": "C:\\WarframeDocument\\TODO\\data\\01-equipment.toml"}` を足す。読み取りだけで、完了は元のファイルで付ける（仕様 AS-18）。
- 起動: ログオン時に自動。チャット画面は **Ctrl+Alt+E** または黒猫のダブルクリック。

## ドキュメント

| パス | 内容 |
|---|---|
| `spec/` | 仕様書（`spec/README.md` から） |
| `CLAUDE.md` | 開発ハーネス（構成・動作確認・反映手順） |
| `docs/git-workflow.md` | Git 運用規定（Issue 起点） |

## クレジット

- 黒猫のドット絵: [Pet Cats Pack](https://luizmelo.itch.io/pet-cat-pack) by LuizMelo（CC0）

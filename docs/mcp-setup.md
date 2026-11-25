# 共通 MCP サーバー（`esagent`）のセットアップ手順

E.S. Agent の `esagent/mcp_server.py` を各 AI クライアントへ登録すると、**Claude Code / Codex / Antigravity / Gemini CLI のどこからでも共通のタスク・記憶・プロジェクト文脈・カレンダー予定・黒猫 Es への進捗通知（`notify_es`）** を利用できます。

## 起動コマンド（共通）

- コマンド: `python`
- 引数: `["C:\\ESAgent\\esagent\\mcp_server.py"]`
- トランスポート: `stdio`

## 1. Claude Code

```powershell
claude mcp add esagent -- python C:\ESAgent\esagent\mcp_server.py
```

## 2. Gemini CLI

```powershell
gemini mcp add esagent python C:\ESAgent\esagent\mcp_server.py
```

## 3. Codex CLI

`~/.codex/config.toml` に以下を追記します:

```toml
[mcp_servers.esagent]
command = "python"
args = ["C:\\ESAgent\\esagent\\mcp_server.py"]
```

## 4. Antigravity

Antigravity の MCP 設定ファイル（`mcp_config.json`）の `mcpServers` に以下を追記します:

```json
{
  "mcpServers": {
    "esagent": {
      "command": "python",
      "args": ["C:\\ESAgent\\esagent\\mcp_server.py"]
    }
  }
}
```

## 提供されるツール一覧（11 ツール）

| ツール | 役割 |
|---|---|
| `list_tasks` / `get_task` / `add_task` / `update_task` | タスク一覧・詳細・追加・更新 |
| `complete_handoff` | 引き継ぎ作業の完了要約をタスクメモへ書き戻す |
| `get_memories` / `add_memory` | Es の記憶（好み・方針・前提）の読み書き |
| `get_project_context` | プロジェクト一覧（説明・フォルダ）の取得 |
| `get_recent_chat` | E.S. Agent の直近会話履歴の取得 |
| `get_calendar_events` | キャッシュ済み Google カレンダー予定の取得 |
| `notify_es` | デスクトップ黒猫 Es の吹き出し・発言ログへ進捗・完了を通知 |

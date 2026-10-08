# YouTube の検索・プレイリスト・アプリ内再生

## 接続

1. Google ToDo・カレンダーと同じ Google Cloud プロジェクトで **YouTube Data API v3** を有効にする。既存の `secrets/google_client.json` をそのまま使える。
2. 初めて Google に接続する場合は、デスクトップアプリ用の OAuth クライアントを作り、JSON を上記の場所に置く。OAuth アプリがテスト中なら自分のアカウントをテストユーザーに追加する。
3. チャット画面の **YouTube・BGM → 動画・プレイリスト → GoogleでYouTubeに接続** を押す。
4. 通常のブラウザで権限を確認して許可し、E.S. Agent に戻る。YouTube の権限は読み取りのみ。ToDo・カレンダーの既存権限も同じ接続で保持する。

権限を一部拒否した場合や更新トークンを取得できない場合は、以前の接続を上書きしない。接続期限が切れた場合は再接続する。

## 利用

- **自分のプレイリスト** を押し、プレイリストを選ぶと動画の一覧が開く。API が取得できる自分のプレイリストを対象とし、YouTube のホーム・履歴・「後で見る」全体を再現するものではない。
- 検索欄に言葉を入力して **検索** を押す。検索は埋め込み可能な動画を対象にし、入力中の自動検索はしない。
- 一覧は20件ずつ取得する。前後のページに移動できる。
- 動画を押すとアプリ内のプレーヤーで再生する。BGM 欄を閉じると一時停止する。
- 一覧を開いている間はアプリ内の再生を一時停止する。動画を選ぶと一覧を閉じ、プレーヤーを表示して再生する。
- アプリ内で再生できない場合は、エラーの **YouTube ウィンドウで流す** から公式サイトを開く。

Google連携によるAPI認証と、YouTubeサイトのログインは別の仕組み。API認証をプレーヤーへ渡すことはしない。Premium機能、年齢確認が必要な動画、非公開動画の再生は保証できない。プレイリストがAPIに表示されても、その中のすべての動画が埋め込みで再生できるとは限らない。

## 接続できないとき

- 設定案内が出る: OAuthクライアントJSONの配置と、対象プロジェクトでYouTube Data API v3が有効かを確認する。
- 権限不足・期限切れ: YouTube欄からGoogleに再接続する。
- 利用上限: 時間をおいて再試行する。各ページの取得もAPIの利用量に数えられる。
- ネットワーク障害: 接続が戻ってから一覧を開き直す。

## 実装と検証

`esagent/youtube.py` が共有Google HTTP処理を通してGETリクエストだけを行う。画面は秘密情報やトークンを受け取らない。一覧取得のAPIは `X-EsAgent` のあるPOSTリクエストのみを受け付け、外部のWebページから呼び出せない。YouTubeの一覧取得はToDo同期を起動しない。

`tests/test_offline_youtube.py` で一時認証ファイル、偽API、ローカルHTTPサーバーを使い、追加権限・既存接続の維持・ページ送り・エラー案内・外部ページからの呼び出し防止を検証する。

公式資料: [プレイリストAPI](https://developers.google.com/youtube/v3/docs/playlists/list)、[動画一覧API](https://developers.google.com/youtube/v3/docs/playlistItems/list)、[検索API](https://developers.google.com/youtube/v3/docs/search/list)、[埋め込みプレーヤー](https://developers.google.com/youtube/iframe_api_reference)、[デスクトップOAuth](https://developers.google.com/identity/protocols/oauth2/native-app)。

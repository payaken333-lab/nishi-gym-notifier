# 西宮市体育館 空き状況通知

## 仕組み
ログイン不要の「空き状況をゲスト検索」機能を自動操作して、
以下4施設のクォーターテニス・ピックルボール用コマの空きを定期チェックし、
条件に合う空きがあればLINEに通知します。

- 中央体育館分館 体育室半面
- 今津体育館 体育室半面
- 鳴尾体育館 体育館半面
- 松原体育館 体育館半面

利用者番号・パスワードは一切使いません(空き照会は会員登録なしで見られるため)。

## 監視条件の変更
`config.py` を編集してください。
- `PURPOSE_CATEGORY` / `PURPOSE_ITEM` … 検索する利用目的
- `TARGET_FACILITIES` … チェックを入れる施設名(サイトの表示と完全一致させる)
- `WATCH_CONDITIONS` … 曜日・時間帯の条件(複数可)
- `DAYS_AHEAD` … 何日先まで見るか

## LINE Messaging API の準備
1. https://developers.line.biz/ja/ で LINE Developers アカウントを作成
2. 「Messaging API」チャネルを新規作成(無料)
3. チャネルアクセストークン(長期)を発行 → `LINE_CHANNEL_TOKEN`
4. 作成したBotを自分のLINEで友だち追加
5. 自分のLINEユーザーIDを確認 → `LINE_USER_ID`
   (LINE Developersコンソールの「あなたのユーザーID」欄で確認可能)

## GitHubでの設定手順
1. このフォルダの中身をGitHubの新規リポジトリにpush
2. リポジトリの Settings → Secrets and variables → Actions で以下を登録:
   - `LINE_CHANNEL_TOKEN`
   - `LINE_USER_ID`
3. Actionsタブを開き、「体育館空き状況チェック」→「Run workflow」で手動実行して動作確認
4. 問題なければ20分おきに自動実行されます

## うまく動かないときは
サイトの実際のHTML構造(class名など)を完全には確認できていないため、
`select_facilities()` や `parse_calendar_html()` が想定通りに動かない可能性があります。
その場合は Actions の実行ログ(特に「[警告]」で始まる行や、
「該当する空きはありませんでした」の後ろの数字が0のまま)を教えてください。
ログを見ながら調整します。

## 注意
- 施設側のお知らせに「不審な連続アクセス」への注意喚起があります。
  頻度を上げすぎず(20分間隔程度を推奨)、あくまで個人の空き確認目的で使ってください。

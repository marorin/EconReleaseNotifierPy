## EconReleaseNotifierPy

指定した経済指標の発表時刻までの残り時間を、`ntfy.sh` へ通知するPythonスクリプトです。

- **内部処理の時刻基準はUTC**
- **通知メッセージには UTC と JST を併記**
- **既定は dry-run（送信しない / stateを書かない）**

## ⚠ 注意事項（必読）

- **必須設定**
  - **データ取得**: RapidAPIキーの設定が必須です（環境変数 `RAPIDAPI_KEY` または `--rapidapi-key`）。
  - **通知受取**: ntfyトピック名の設定が必須です（`--ntfy-topic`）。※購読に使う値を指定
- **本スクリプトは既定で dry-run です。実際に通知を送るには `--apply` が必要です。**
- **`ntfy-topic`（トピック名）は他と被らないよう、推測されにくい複雑な文字列にしてください。**
  - `ntfy.sh` のトピックは「URLの一部＝共有の受信口」です。短い/一般的な名前だと、第三者が同じトピックへ投稿できる可能性があります。
- `er.state.json` に通知済み情報を保存し、**同一内容の重複通知を抑止**します。
  - stateファイルの置き場所は `--state` で変更できますが、**危険なパス（ドライブ直下等）は検知して停止**します。
- APIレスポンス形式が変更された場合、イベントの抽出に失敗して「対象なし」になる可能性があります。

## 仕組み（概要）

1. Economic Calendar API（RapidAPI）から、期間指定のエンドポイントでデータを取得します
   - `.../calendar?countryCode=US&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD`
2. 現在時刻（UTC）から **H時間以内** のイベントに絞り込みます（既定24時間、最大168時間）
3. 国・指標名（キーワード/ルール）でMatchし、Ignoreルールを最優先で除外します
4. 発表が近い順に最大X件だけ通知します（既定5件）
5. `er.state.json` に通知済みキーを保存して重複通知を抑止します（`--apply` 時のみ）
   - 同一イベントでも **最小通知間隔（分）** を超えていれば再通知できます（カウントダウン用途を想定）
   - 1回の実行で送る通知数にも上限があります（大量通知の安全弁）

## 必要なもの

- Python 3.10+ 推奨
- RapidAPIキー（Economic Calendar API）
- 通知先: `ntfy.sh`（アカウントは不要／セルフホストでも可）
- **通知受取用のクライアント**
  - スマホ: ntfyアプリ（Android / iOS）
  - Web: ntfy Web UI（ブラウザで購読）

参考:
- Economic Calendar API: `https://economic-calendar.horizonfx.id/`
- RapidAPI仕様: `https://rapidapi.com/yasimpratama88/api/economic-calendar-api`
- ntfy 公式: `https://ntfy.sh/`
- ntfy ドキュメント: `https://docs.ntfy.sh/`
- ntfy Web UI: `https://ntfy.sh/app`
- ntfy Androidアプリ: `https://play.google.com/store/apps/details?id=io.heckel.ntfy`
- ntfy iOSアプリ: `https://apps.apple.com/app/ntfy/id1625396347`

## インストール

依存は標準ライブラリのみです（追加パッケージ不要）。

## 設定項目（CLIオプション）

|項目|型|役割|デフォルト|
|---|---|---|---|
|`--apply`|bool|実際に通知送信・state更新を行う（未指定はdry-run）|`False`|
|`--now`|str|テスト用に現在時刻(UTC)を固定して実行|未指定|
|`--lookahead-hours`|int|現在から何時間以内の指標を対象にするか（最大168）|`24`|
|`--max-items`|int|通知する最大件数（近い順）|`5`|
|`--min-interval-minutes`|int|同一イベントの最小通知間隔（分）|`1`|
|`--max-notify-per-run`|int|1回の実行で実際に送る通知の最大件数|`10`|
|`--debug-api`|bool|API取得結果のデバッグ情報（取得件数/キー例/イベント件数）を表示|`False`|
|`--debug-api-print-raw`|bool|フィルタ前のAPI生データ（期間指定の取得結果）を標準出力へJSONで表示|`False`|
|`--debug-api-print-raw-limit`|int|`--debug-api-print-raw` の出力件数上限（1エンドポイントあたり、-1で全件）|`10`|
|`--debug-api-save`|str|APIデバッグ情報をJSON保存（上書き）|未指定|
|`--debug-api-save-limit`|int|`--debug-api-save` 時に保存するサンプル件数（1エンドポイントあたり）|`50`|
|`--country`|str(複数)|対象国（複数指定可、ISO alpha-2想定）|`US/EU/JP/UK/CA/CH/AU/NZ`|
|`--match-keyword`|str(複数)|指標名の部分一致キーワード（英語）|（英語キーワード既定）|
|`--match`|str(複数)|個別マッチ（形式: `Country|name_contains`）|なし|
|`--ignore`|str(複数)|個別除外（形式: `Country|name_contains`、matchより優先）|なし|
|`--ntfy-server`|str|ntfyサーバURL|`https://ntfy.sh`|
|`--ntfy-topic`|str|ntfyトピック名（推測されにくい値推奨）|`econ-release-notifier`|
|`--ntfy-title`|str|通知タイトル|`Econ Release Notifier`|
|`--ntfy-priority`|str|優先度（`min/low/default/high/max` または `1-5`）|`default`|
|`--state`|str|stateファイルパス（相対ならスクリプト同階層基準）|`er.state.json`|
|`--rapidapi-key`|str|RapidAPIキー（未指定なら `RAPIDAPI_KEY` を参照）|未指定|

### Windowsのコマンドライン（cmd.exe / PowerShell）での入力ルール

- **基本**: `--match-keyword` のような単語は、通常 **クォート不要**です（例: `--match-keyword ISM`）。
- **cmd.exe注意（重要）**: シングルクォート `'...'` はクォートとして扱われず、**文字として残る**ことがあります。`--match-keyword 'ISM'` のような書き方は避けてください。
- **`|` を含む値（`--match` / `--ignore`）**:
  - **推奨（cmd.exe / PowerShell の両方で安全）**: ダブルクォートで囲む
    - cmd.exe: `--match "US|ISM"` / `--ignore "US|CPI"`
    - PowerShell: `--match "US|ISM"` / `--ignore "US|CPI"`
  - **cmd.exe代替**: `|` をエスケープする（クォートなし）
    - cmd.exe: `--match US^|ISM`

## 定数（コード内で管理している既定値 / 上限値）

多くの既定値はCLIで変更できますが、**安全のための上限値**や**API接続先**などはコード内の定数として固定しています。

|定数名|意味|関係するCLI/補足|
|---|---|---|
|`DEFAULT_LOOKAHEAD_HOURS`|対象とする「先の時間窓(H)」の既定値|`--lookahead-hours` のデフォルト（既定24）|
|`MAX_LOOKAHEAD_HOURS`|`--lookahead-hours` に許可する最大値（安全弁）|最大168（=7日）|
|`DEFAULT_MAX_ITEMS`|通知する最大件数の既定値|`--max-items` のデフォルト（既定5）|
|`DEFAULT_MIN_INTERVAL_MINUTES`|同一イベントの最小通知間隔（分）の既定値|`--min-interval-minutes` のデフォルト（既定1）|
|`DEFAULT_MAX_NOTIFY_PER_RUN`|1実行あたり最大通知数の既定値|`--max-notify-per-run` のデフォルト（既定10）|
|`DEFAULT_NTFY_SERVER`|ntfyサーバURLの既定値|`--ntfy-server` のデフォルト（既定 `https://ntfy.sh`）|
|`DEFAULT_NTFY_TOPIC`|ntfyトピックの既定値|`--ntfy-topic` のデフォルト（推測されにくい値推奨）|
|`DEFAULT_NTFY_TITLE`|ntfy通知タイトルの既定値|`--ntfy-title` のデフォルト|
|`DEFAULT_NTFY_PRIORITY`|ntfy優先度の既定値|`--ntfy-priority` のデフォルト（`min/low/default/high/max` または `1-5`）|
|`DEFAULT_COUNTRIES`|対象国リストの既定値|`--country` 未指定時に使用（国コード揺れ対策: `GB` は `UK` として扱い、ユーロ圏はAPIが `EMU` を返す場合があるため `EMU` は `EU` として扱います）: `US/EU/JP/UK/CA/CH/AU/NZ`|
|`DEFAULT_MATCH_KEYWORDS`|指標名の部分一致キーワード既定値|`--match-keyword` 未指定時に使用（英語）|
|`RAPIDAPI_BASE` / `RAPIDAPI_CALENDAR_ENDPOINT` / `RAPIDAPI_HOST_HEADER`|Economic Calendar API(RapidAPI)の接続先情報|期間指定 `/calendar` を使って取得→時間で絞り込み|
|`ENV_RAPIDAPI_KEY`|RapidAPIキーを読む環境変数名|`--rapidapi-key` 未指定時に参照（既定 `RAPIDAPI_KEY`）|
|`DEFAULT_STATE_FILENAME`|stateファイル名の既定値|`--state` のデフォルト（既定 `er.state.json`）|

### 危険性の高い項目（例）

- `--state`
  - **NG例**: `--state C:\er.state.json`（ドライブ直下は危険として停止します）
  - **OK例**: `--state .\data\er.state.json`（プロジェクト配下のサブフォルダ）

## 実行例

### 1) dry-run（送信せず、結果だけ確認）

PowerShell例:

```powershell
$env:RAPIDAPI_KEY="YOUR_KEY"
python .\econ_release_notifier.py --lookahead-hours 24 --max-items 5
```

### API取得のデバッグ

取得件数や、APIの先頭アイテムのキー、日時パースできたイベント件数を表示します。

```powershell
$env:RAPIDAPI_KEY="YOUR_KEY"
python .\econ_release_notifier.py --debug-api
```

フィルタ前の生データ（期間指定の取得結果）を標準出力に出す場合（例: 先頭50件まで）:

```powershell
$env:RAPIDAPI_KEY="YOUR_KEY"
python .\econ_release_notifier.py --debug-api-print-raw --debug-api-print-raw-limit 50
```

全件出力したい場合（出力が非常に大きくなる可能性があります）:

```powershell
$env:RAPIDAPI_KEY="YOUR_KEY"
python .\econ_release_notifier.py --debug-api-print-raw --debug-api-print-raw-limit -1
```

APIレスポンスの一部（先頭N件）をJSONで保存する場合:

```powershell
$env:RAPIDAPI_KEY="YOUR_KEY"
python .\econ_release_notifier.py --debug-api --debug-api-save api_debug.json --debug-api-save-limit 50
```

### バージョン確認

```powershell
python .\econ_release_notifier.py --version
```

### 文字化けする場合（Windows / PowerShell）

環境によっては、日本語が文字化けすることがあります。以下はPowerShellでUTF-8表示に寄せる例です。

```powershell
chcp 65001
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$env:PYTHONUTF8="1"
```

### 2) 実送信（通知 + state更新）

```powershell
$env:RAPIDAPI_KEY="YOUR_KEY"
python .\econ_release_notifier.py --apply --ntfy-topic "your-topic" --ntfy-priority high
```

### 3) テストモード（仮の現在日時で判定）

> `--now` のタイムゾーン未指定（例: `2026-01-03T12:00:00`）は **UTCとして扱います**。

```powershell
$env:RAPIDAPI_KEY="YOUR_KEY"
python .\econ_release_notifier.py --now 2026-01-03T12:00:00Z --lookahead-hours 24
```

## 生成物

- `er.state.json`: 通知済み情報（重複通知抑止）
  - **保存場所**: 既定ではスクリプトと同フォルダ
  - **上書き**: `--apply` 実行時に更新（置換）します


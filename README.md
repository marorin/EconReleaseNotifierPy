## EconReleaseNotifierPy

指定した経済指標の発表時刻までの残り時間を、`ntfy.sh` へ通知するPythonスクリプトです。

- **内部処理の時刻基準はUTC**
- **通知メッセージには UTC と JST を併記**
- **既定は dry-run（送信しない / stateを書かない）**

## ⚠ 注意事項（必読）

- **本スクリプトは既定で dry-run です。実際に通知を送るには `--apply` が必要です。**
- **RapidAPIキーが必要**です（環境変数 `RAPIDAPI_KEY` または `--rapidapi-key` で設定）。
- `er.state.json` に通知済み情報を保存し、**同一内容の重複通知を抑止**します。
  - stateファイルの置き場所は `--state` で変更できますが、**危険なパス（ドライブ直下等）は検知して停止**します。
- APIレスポンス形式が変更された場合、イベントの抽出に失敗して「対象なし」になる可能性があります。

## 仕組み（概要）

1. Economic Calendar API（RapidAPI）から、以下2つのエンドポイントでデータを取得します
   - `.../calendar/history/this-week`
   - `.../calendar/history/next-week`
2. 現在時刻（UTC）から **H時間以内** のイベントに絞り込みます（既定24時間、最大168時間）
3. 国・指標名（キーワード/ルール）でMatchし、Ignoreルールを最優先で除外します
4. 発表が近い順に最大X件だけ通知します（既定1件）
5. `er.state.json` に通知済みキーを保存して重複通知を抑止します（`--apply` 時のみ）

## 必要なもの

- Python 3.10+ 推奨
- RapidAPIキー（Economic Calendar API）
- 通知先: `ntfy.sh`（セルフホストでも可）

参考:
- Economic Calendar API: `https://economic-calendar.horizonfx.id/`
- RapidAPI仕様: `https://rapidapi.com/yasimpratama88/api/economic-calendar-api`

## インストール

依存は標準ライブラリのみです（追加パッケージ不要）。

## 設定項目（CLIオプション）

|項目|型|役割|デフォルト|
|---|---|---|---|
|`--apply`|bool|実際に通知送信・state更新を行う（未指定はdry-run）|`False`|
|`--now`|str|テスト用に現在時刻(UTC)を固定して実行|未指定|
|`--lookahead-hours`|int|現在から何時間以内の指標を対象にするか（最大168）|`24`|
|`--max-items`|int|通知する最大件数（近い順）|`1`|
|`--country`|str(複数)|対象国（複数指定可、ISO alpha-2想定）|`US/EU/JP/GB/CA/CH/AU/NZ`|
|`--match-keyword`|str(複数)|指標名の部分一致キーワード（英語）|（英語キーワード既定）|
|`--match`|str(複数)|個別マッチ（形式: `Country\|name_contains`）|なし|
|`--ignore`|str(複数)|個別除外（形式: `Country\|name_contains`、matchより優先）|なし|
|`--ntfy-server`|str|ntfyサーバURL|`https://ntfy.sh`|
|`--ntfy-topic`|str|ntfyトピック名|`econ-release-notifier`|
|`--ntfy-title`|str|通知タイトル|`Econ Release Notifier`|
|`--ntfy-priority`|str|優先度（`min/low/default/high/max` または `1-5`）|`default`|
|`--state`|str|stateファイルパス（相対ならスクリプト同階層基準）|`er.state.json`|
|`--rapidapi-key`|str|RapidAPIキー（未指定なら `RAPIDAPI_KEY` を参照）|未指定|

## 定数（コード内で管理している既定値 / 上限値）

多くの既定値はCLIで変更できますが、**安全のための上限値**や**API接続先**などはコード内の定数として固定しています。

|定数名|意味|関係するCLI/補足|
|---|---|---|
|`DEFAULT_LOOKAHEAD_HOURS`|対象とする「先の時間窓(H)」の既定値|`--lookahead-hours` のデフォルト（既定24）|
|`MAX_LOOKAHEAD_HOURS`|`--lookahead-hours` に許可する最大値（安全弁）|指示書要件で最大168（=7日）|
|`DEFAULT_MAX_ITEMS`|通知する最大件数の既定値|`--max-items` のデフォルト（既定1）|
|`DEFAULT_NTFY_SERVER`|ntfyサーバURLの既定値|`--ntfy-server` のデフォルト（既定 `https://ntfy.sh`）|
|`DEFAULT_NTFY_TOPIC`|ntfyトピックの既定値|`--ntfy-topic` のデフォルト|
|`DEFAULT_NTFY_TITLE`|ntfy通知タイトルの既定値|`--ntfy-title` のデフォルト|
|`DEFAULT_NTFY_PRIORITY`|ntfy優先度の既定値|`--ntfy-priority` のデフォルト（`min/low/default/high/max` または `1-5`）|
|`DEFAULT_COUNTRIES`|対象国リストの既定値|`--country` 未指定時に使用（ISO alpha-2: `US/EU/JP/GB/CA/CH/AU/NZ`）|
|`DEFAULT_MATCH_KEYWORDS`|指標名の部分一致キーワード既定値|`--match-keyword` 未指定時に使用（英語）|
|`RAPIDAPI_BASE` / `RAPIDAPI_ENDPOINTS` / `RAPIDAPI_HOST_HEADER`|Economic Calendar API(RapidAPI)の接続先情報|現状は固定（要件: this-week / next-week を取得→時間で絞り込み）|
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
python .\econ_release_notifier.py --lookahead-hours 24 --max-items 1
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


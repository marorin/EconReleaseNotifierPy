"""
Econ Release Notifier
--------------------
指定した経済指標の発表時刻までの残り時間を ntfy.sh で通知します。

安全性・説明性を最優先し、以下を徹底します:
- 既定は dry-run（送信しない / stateを書かない）
- 送信・state更新は --apply を明示した場合のみ実施
- 書き込み対象（stateファイル）と通知内容を実行前に表示
- 危険なパス（ドライブ直下/ルート等）を検知して停止
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# =========================
# 文字化け対策（Windows向け）
# =========================
def try_configure_stdio_utf8() -> None:
    """
    可能な環境では標準出力/標準エラーのエンコーディングをUTF-8に寄せます。
    ただし、コンソール側がUTF-8表示に対応していない場合は完全には防げません。
    """
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # 安全第一: ここで落ちるくらいなら何もしない
        pass


# =========================
# 定数（1か所に集約）
# =========================
#
# ここにある定数は、主に「CLIオプションのデフォルト値」または「安全のための上限値」です。
# - できるだけ CLI オプションで設定変更できるようにしてあります（READMEの表参照）
# - 上限値（MAX_*）は誤設定で想定外の大量通知や重いAPI取得にならないための安全弁です
#

# 何時間先までのイベントを対象にするか（CLI: --lookahead-hours のデフォルト）
DEFAULT_LOOKAHEAD_HOURS = 24
# 何時間先までのイベントを許可するか（CLI: --lookahead-hours の最大）
# 指示書要件: 最大168（= 7日）
MAX_LOOKAHEAD_HOURS = 168

# 通知対象の最大件数（CLI: --max-items のデフォルト）
DEFAULT_MAX_ITEMS = 1

# 同一イベントの最小通知間隔（分）（CLI: --min-interval-minutes のデフォルト）
# cronで頻繁に回した場合でも、同じイベントの通知が短時間に連打されるのを抑止します。
DEFAULT_MIN_INTERVAL_MINUTES = 1

# 1回の実行で実際に送る通知の最大件数（CLI: --max-notify-per-run のデフォルト）
# 設定ミスやAPI異常などで「短時間に大量通知」になった場合の安全弁です。
DEFAULT_MAX_NOTIFY_PER_RUN = 10

# ntfy 通知先のデフォルト（CLIで上書き可能）
# - server: ntfyサーバURL（CLI: --ntfy-server）
# - topic : 通知トピック名（CLI: --ntfy-topic）
# - title : 通知タイトル（CLI: --ntfy-title）
# - priority: 優先度（CLI: --ntfy-priority）
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
# 注意: ntfyのトピック名(DEFAULT_NTFY_TOPIC)は「購読/投稿の識別子」でもあります。
# 推測しやすい/他と被るトピック名を使うと、第三者が同じトピックへ投稿できる可能性があります。
# 運用する場合は、十分に複雑で被りにくいトピック名に変更してください（README参照）。
DEFAULT_NTFY_TOPIC = "econ-release-notifier"
DEFAULT_NTFY_TITLE = "Econ Release Notifier"
DEFAULT_NTFY_PRIORITY = "default"  # ntfy: min/low/default/high/max または 1-5

# 対象国のデフォルト（CLI: --country が未指定の場合に使用）
# 指示: ISO 3166-1 alpha-2（2文字国コード）に準拠。
# ※EU は国ではありませんが、経済指標API側で地域コードとして使われる想定のため含めます。
DEFAULT_COUNTRIES: List[str] = ["US", "EU", "JP", "GB", "CA", "CH", "AU", "NZ"]

# 指標名の部分一致キーワードのデフォルト（CLI: --match-keyword が未指定の場合に使用）
# ここに含まれる文字列が「指標名に含まれる」場合、Match対象になります（Ignoreルールが最優先）。
# 指示: APIは海外（英語表記）前提のため、日本語キーワードは入れません。
DEFAULT_MATCH_KEYWORDS: List[str] = [
    "Interest Rate Decision",
    "Policy Rate",
    "Non-Farm Payrolls",
    "Employment",
    "Consumer Price Index",
    "CPI",
]

# RapidAPI（Economic Calendar API）の接続先情報
# - BASE: APIホスト（固定）
# - ENDPOINTS: 指示書要件に従い this-week / next-week を取得してから時間窓で絞り込みます（固定）
# - HOST_HEADER: RapidAPIのHostヘッダ（固定）
RAPIDAPI_BASE = "https://economic-calendar-api.p.rapidapi.com"
RAPIDAPI_ENDPOINTS = [
    "/calendar/history/this-week",
    "/calendar/history/next-week",
]
RAPIDAPI_HOST_HEADER = "economic-calendar-api.p.rapidapi.com"

# RapidAPIキーを読む環境変数名（CLI: --rapidapi-key が未指定の場合に参照）
ENV_RAPIDAPI_KEY = "RAPIDAPI_KEY"

# stateファイル名のデフォルト（CLI: --state が未指定の場合）
# 通知済み情報を保存して重複通知を抑止します（--apply 実行時のみ更新）。
DEFAULT_STATE_FILENAME = "er.state.json"


# =========================
# データ構造
# =========================
@dataclass(frozen=True)
class MatchRule:
    country: str
    name_contains: str


@dataclass(frozen=True)
class IgnoreRule:
    country: str
    name_contains: str


@dataclass(frozen=True)
class Event:
    name: str
    country: str
    time_utc: datetime  # timezone-aware(UTC)
    raw: Dict[str, Any]

    @property
    def key(self) -> str:
        # 重複通知抑止用キー（同一内容判定）
        t = self.time_utc.isoformat().replace("+00:00", "Z")
        return f"{t}|{self.country}|{self.name}"


@dataclass(frozen=True)
class Settings:
    rapidapi_key: str

    lookahead_hours: int
    max_items: int
    min_interval_minutes: int
    max_notify_per_run: int

    countries: List[str]
    match_keywords: List[str]
    match_rules: List[MatchRule]
    ignore_rules: List[IgnoreRule]

    ntfy_server: str
    ntfy_topic: str
    ntfy_title: str
    ntfy_priority: str

    state_path: Path

    now_override_utc: Optional[datetime]  # timezone-aware(UTC) or None
    apply: bool


# =========================
# 例外（説明性重視）
# =========================
class SafeUsageError(RuntimeError):
    """ユーザー設定や環境に起因する、安全に止めるべきエラー。"""


# =========================
# ユーティリティ
# =========================
def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise SafeUsageError("内部エラー: naive datetime をUTCに変換しようとしました。")
    return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime_to_utc(text: str) -> datetime:
    """
    受け入れる例:
    - 2026-01-03T12:34:56Z
    - 2026-01-03T12:34:56+09:00
    - 2026-01-03 12:34:56Z
    - 2026-01-03 12:34:56  (※UTCとして扱う。曖昧さはREADMEで明記)
    """
    s = text.strip()
    s = s.replace(" ", "T", 1) if " " in s and "T" not in s else s
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as ex:
        raise SafeUsageError(
            f"now の日時指定が解析できません: {text!r}\n"
            "ISO 8601形式の例: 2026-01-03T12:34:56Z"
        ) from ex

    if dt.tzinfo is None:
        # 指示書が「内部はUTC基準」なので、曖昧入力はUTC扱いに固定
        dt = dt.replace(tzinfo=timezone.utc)
    return to_utc(dt)


def normalize_text(s: str) -> str:
    return " ".join(s.strip().split()).casefold()


def is_dangerous_path(p: Path) -> Tuple[bool, str]:
    """
    危険なパス例:
    - Windows: C:\\ , D:\\ などドライブ直下
    - *nix: / 直下
    - 空パス/相対が混ざる等（resolve失敗も危険扱い）
    """
    try:
        rp = p.resolve()
    except Exception:
        return True, f"パスを正規化できません: {p}"

    # ルート自体（/）は危険
    if rp == rp.anchor and str(rp) in ("/", "\\"):
        return True, f"ルート直下は危険です: {rp}"

    # Windows: ドライブ直下（例: C:\foo.json の parent が C:\）
    # Path.anchor 例: "C:\\"
    if rp.parent == Path(rp.anchor):
        return True, f"ドライブ直下は危険です: {rp}"

    # 現在のプロジェクト外への書き込みを強制はしない（要件なし）が、警告理由として返せるようにしておく
    return False, ""


def read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as ex:
        raise SafeUsageError(
            f"stateファイルが壊れている可能性があります: {path}\n"
            "危険: これ以上の上書きで復旧が困難になる可能性があります。"
        ) from ex
    except OSError as ex:
        raise SafeUsageError(f"stateファイルを読み込めません: {path}") from ex


def write_json_file_atomic(path: Path, obj: Dict[str, Any]) -> None:
    """
    可能な範囲で安全に上書き（同一フォルダに一時ファイル→置換）。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(obj, ensure_ascii=False, indent=2)
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(data)
        tmp.replace(path)
    except OSError as ex:
        raise SafeUsageError(f"stateファイルを書き込めません: {path}") from ex


# =========================
# API 呼び出し
# =========================
def http_get_json(url: str, headers: Dict[str, str], timeout_sec: int = 20) -> Any:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as ex:
        body = ""
        try:
            body = ex.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise SafeUsageError(
            f"API呼び出しに失敗しました (HTTP {ex.code})\nURL: {url}\n"
            f"危険: APIキー/ホスト設定の誤り、またはアクセス制限の可能性があります。\n"
            f"レスポンス: {body[:500]}"
        ) from ex
    except urllib.error.URLError as ex:
        raise SafeUsageError(
            f"APIに接続できません。\nURL: {url}\n"
            "危険: ネットワーク/プロキシ/証明書設定の問題の可能性があります。"
        ) from ex

    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as ex:
        raise SafeUsageError(
            f"APIレスポンスがJSONではありません。\nURL: {url}\n"
            "危険: エンドポイント/ホストの誤り、またはサービス側の障害の可能性があります。"
        ) from ex


def fetch_events(settings: Settings) -> List[Dict[str, Any]]:
    headers = {
        "X-RapidAPI-Key": settings.rapidapi_key,
        "X-RapidAPI-Host": RAPIDAPI_HOST_HEADER,
        "Accept": "application/json",
    }
    out: List[Dict[str, Any]] = []
    for ep in RAPIDAPI_ENDPOINTS:
        url = RAPIDAPI_BASE + ep
        data = http_get_json(url, headers=headers)

        # API仕様の揺れに備えて、list/dictどちらでも受ける
        if isinstance(data, list):
            out.extend([x for x in data if isinstance(x, dict)])
        elif isinstance(data, dict):
            # よくある形: {"data":[...]} など
            for k in ("data", "result", "items", "events"):
                v = data.get(k)
                if isinstance(v, list):
                    out.extend([x for x in v if isinstance(x, dict)])
                    break
        else:
            # 期待外は無視しつつ安全に止める
            raise SafeUsageError(
                "APIの応答形式が想定外です（list/dictではありません）。\n"
                "危険: API仕様変更の可能性があります。READMEのAPIリンクを確認してください。"
            )
    return out


# =========================
# イベントの抽出・フィルタ
# =========================
def extract_event_datetime_utc(raw: Dict[str, Any]) -> Optional[datetime]:
    """
    可能性のあるキー:
    - "datetime", "dateTime", "time"
    - "date" + "time"
    - "timestamp" (秒/ミリ秒)
    """
    # timestamp
    ts = raw.get("timestamp") or raw.get("timeStamp") or raw.get("ts")
    if isinstance(ts, (int, float)):
        # ミリ秒か秒か推測（10^12 以上ならミリ秒想定）
        sec = float(ts) / 1000.0 if ts > 1e12 else float(ts)
        try:
            return datetime.fromtimestamp(sec, tz=timezone.utc)
        except (OverflowError, OSError):
            return None

    # datetime text
    for k in ("datetime", "dateTime", "date_time", "eventTime", "event_time", "time"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            try:
                return parse_datetime_to_utc(v)
            except SafeUsageError:
                # 次の候補へ
                pass

    # date + time
    d = raw.get("date")
    t = raw.get("time")
    if isinstance(d, str) and d.strip():
        if isinstance(t, str) and t.strip():
            # "2026-01-03" + "12:30" 等
            combo = f"{d.strip()}T{t.strip()}"
            try:
                return parse_datetime_to_utc(combo)
            except SafeUsageError:
                return None
        try:
            return parse_datetime_to_utc(d.strip())
        except SafeUsageError:
            return None

    return None


def extract_event_name(raw: Dict[str, Any]) -> str:
    for k in ("event", "name", "title", "indicator", "economicIndicator"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "(unknown event)"


def extract_country(raw: Dict[str, Any]) -> str:
    # 国コード(alpha-2)運用を優先するため、コード系キーを先に見る
    for k in ("countryCode", "country_code", "country", "countryName", "country_name"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "(unknown country)"


def build_events(raw_items: List[Dict[str, Any]]) -> List[Event]:
    events: List[Event] = []
    for raw in raw_items:
        dt = extract_event_datetime_utc(raw)
        if dt is None:
            continue
        if dt.tzinfo is None:
            continue
        dt_utc = to_utc(dt)
        events.append(
            Event(
                name=extract_event_name(raw),
                country=extract_country(raw),
                time_utc=dt_utc,
                raw=raw,
            )
        )
    return events


def country_matches(event_country: str, allowed: List[str]) -> bool:
    ec = normalize_text(event_country)
    for a in allowed:
        if ec == normalize_text(a):
            return True
    return False


def event_matches_keywords(name: str, keywords: List[str]) -> bool:
    n = normalize_text(name)
    for kw in keywords:
        if normalize_text(kw) in n:
            return True
    return False


def rule_matches(country: str, name: str, rules: List[MatchRule]) -> bool:
    c = normalize_text(country)
    n = normalize_text(name)
    for r in rules:
        if c == normalize_text(r.country) and normalize_text(r.name_contains) in n:
            return True
    return False


def apply_filters(settings: Settings, events: List[Event]) -> List[Event]:
    now_utc = settings.now_override_utc or utc_now()
    end_utc = now_utc + timedelta(hours=settings.lookahead_hours)

    filtered: List[Event] = []
    for ev in events:
        # 時間窓
        if ev.time_utc < now_utc:
            continue
        if ev.time_utc > end_utc:
            continue

        # 国フィルタ（必須）
        if settings.countries and not country_matches(ev.country, settings.countries):
            continue

        # match 判定（キーワード or 明示ルール）
        is_match = event_matches_keywords(ev.name, settings.match_keywords) or rule_matches(
            ev.country, ev.name, settings.match_rules
        )
        if not is_match:
            continue

        # ignore は最優先
        is_ignore = rule_matches(ev.country, ev.name, [MatchRule(r.country, r.name_contains) for r in settings.ignore_rules])
        if is_ignore:
            continue

        filtered.append(ev)

    filtered.sort(key=lambda e: e.time_utc)
    return filtered[: settings.max_items]


# =========================
# 表示・通知
# =========================
def format_dt_pair(dt_utc: datetime) -> Tuple[str, str]:
    dt_utc = to_utc(dt_utc)
    jst = timezone(timedelta(hours=9))
    dt_jst = dt_utc.astimezone(jst)
    return (
        dt_utc.isoformat().replace("+00:00", "Z"),
        dt_jst.isoformat(),
    )


def humanize_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}時間")
    if minutes or hours:
        parts.append(f"{minutes}分")
    parts.append(f"{seconds}秒")
    return "".join(parts)


def build_message(now_utc: datetime, ev: Event) -> str:
    now_utc = to_utc(now_utc)
    utc_s, jst_s = format_dt_pair(ev.time_utc)
    remain = ev.time_utc - now_utc
    return (
        f"対象: {ev.name}\n"
        f"国: {ev.country}\n"
        f"発表時刻(UTC): {utc_s}\n"
        f"発表時刻(JST): {jst_s}\n"
        f"残り: {humanize_timedelta(remain)}\n"
    )


def ntfy_send(settings: Settings, message: str) -> None:
    url = settings.ntfy_server.rstrip("/") + "/" + settings.ntfy_topic
    body = message.encode("utf-8")
    headers = {
        "Title": settings.ntfy_title,
        "Priority": settings.ntfy_priority,
        "Content-Type": "text/plain; charset=utf-8",
        "User-Agent": "econ-release-notifier/1.0",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as ex:
        body = ""
        try:
            body = ex.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise SafeUsageError(
            f"ntfy通知に失敗しました (HTTP {ex.code})\n"
            f"URL: {url}\n"
            "危険: topic名の誤り、またはネットワーク制限の可能性があります。\n"
            f"レスポンス: {body[:500]}"
        ) from ex
    except urllib.error.URLError as ex:
        raise SafeUsageError(
            f"ntfyに接続できません。\nURL: {url}\n"
            "危険: ネットワーク/プロキシ/証明書設定の問題の可能性があります。"
        ) from ex


# =========================
# State（重複通知抑止）
# =========================
def load_state(path: Path) -> Dict[str, Any]:
    state = read_json_file(path)
    if not state:
        return {"events": {}, "last_notified_time_utc": None}

    # 最低限の形に整形（新形式: events）
    if "events" not in state or not isinstance(state.get("events"), dict):
        state["events"] = {}

    # 旧形式（notified配列）からの読み替え:
    # - 旧形式は「同一イベントは永続抑止」だったため、単純移行すると厳しすぎます。
    # - かといって、アップデート直後に即再通知されると混乱するため、
    #   初回だけ「今通知した扱い」にして min-interval で短時間の再通知を抑止します。
    if isinstance(state.get("notified"), list) and state.get("notified"):
        now_s = utc_now().isoformat().replace("+00:00", "Z")
        for k in state.get("notified", []):
            if isinstance(k, str) and k not in state["events"]:
                state["events"][k] = {"last_notified_at_utc": now_s}

    if "last_notified_time_utc" not in state:
        state["last_notified_time_utc"] = None
    return state


def parse_utc_iso(text: str) -> Optional[datetime]:
    s = text.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def should_skip_due_to_min_interval(
    state: Dict[str, Any],
    ev: Event,
    now_utc: datetime,
    min_interval_minutes: int,
) -> Tuple[bool, Optional[int]]:
    """
    returns: (skip?, remaining_seconds_if_skipped)
    """
    if min_interval_minutes <= 0:
        return False, None

    events = state.get("events", {})
    if not isinstance(events, dict):
        return False, None
    entry = events.get(ev.key)
    if not isinstance(entry, dict):
        return False, None
    last_s = entry.get("last_notified_at_utc")
    if not isinstance(last_s, str):
        return False, None

    last_dt = parse_utc_iso(last_s)
    if last_dt is None:
        return False, None

    now_utc = to_utc(now_utc)
    delta = now_utc - last_dt
    if delta.total_seconds() < min_interval_minutes * 60:
        remaining = int(min_interval_minutes * 60 - delta.total_seconds())
        if remaining < 0:
            remaining = 0
        return True, remaining
    return False, None


def update_state_after_send(state: Dict[str, Any], ev: Event, now_utc: datetime) -> Dict[str, Any]:
    events = state.get("events")
    if not isinstance(events, dict):
        events = {}
        state["events"] = events

    # 同一イベントの最終通知時刻（UTC）
    events[ev.key] = {"last_notified_at_utc": to_utc(now_utc).isoformat().replace("+00:00", "Z")}

    # 無制限肥大化を避ける（最新500件まで保持）
    if len(events) > 500:
        # eventsは順序保証されないため、古いものを正確に落とすことは難しい。
        # ここでは安全側に「適当に間引く」だけに留めます（頻繁に500超になる運用は想定しない）。
        for i, k in enumerate(list(events.keys())):
            if i >= len(events) - 500:
                break
            events.pop(k, None)

    state["last_notified_time_utc"] = ev.time_utc.isoformat().replace("+00:00", "Z")
    return state


# =========================
# CLI / 設定
# =========================
def parse_rules(texts: List[str], kind: str) -> List[MatchRule]:
    """
    形式: "Country|name_contains"
    例: "United States|CPI"
    """
    rules: List[MatchRule] = []
    for t in texts:
        if "|" not in t:
            raise SafeUsageError(f"{kind} の形式が不正です: {t!r} (例: \"United States|CPI\")")
        c, n = t.split("|", 1)
        c = c.strip()
        n = n.strip()
        if not c or not n:
            raise SafeUsageError(f"{kind} の形式が不正です: {t!r} (国/指標名が空です)")
        rules.append(MatchRule(country=c, name_contains=n))
    return rules


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="econ_release_notifier.py",
        description="指定した経済指標の発表時刻までの残り時間を ntfy.sh に通知します（既定はdry-run）。",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="実際に通知送信と state 更新を行います（未指定の場合はdry-run）。",
    )
    p.add_argument(
        "--now",
        type=str,
        default=None,
        help="テスト用: 現在時刻(UTC)を固定します。例: 2026-01-03T12:34:56Z",
    )
    p.add_argument(
        "--lookahead-hours",
        type=int,
        default=DEFAULT_LOOKAHEAD_HOURS,
        help=f"現在から何時間以内の指標を対象にするか（既定{DEFAULT_LOOKAHEAD_HOURS}, 最大{MAX_LOOKAHEAD_HOURS}）。",
    )
    p.add_argument(
        "--max-items",
        type=int,
        default=DEFAULT_MAX_ITEMS,
        help=f"通知対象の最大件数（発表が近い順、既定{DEFAULT_MAX_ITEMS}）。",
    )
    p.add_argument(
        "--min-interval-minutes",
        type=int,
        default=DEFAULT_MIN_INTERVAL_MINUTES,
        help=f"同一イベントの最小通知間隔（分）。既定{DEFAULT_MIN_INTERVAL_MINUTES}（0で無効）。",
    )
    p.add_argument(
        "--max-notify-per-run",
        type=int,
        default=DEFAULT_MAX_NOTIFY_PER_RUN,
        help=f"1回の実行で実際に送る通知の最大件数。既定{DEFAULT_MAX_NOTIFY_PER_RUN}。",
    )
    p.add_argument(
        "--country",
        action="append",
        default=[],
        help="対象国（複数指定可）。未指定ならデフォルトの国リストを使います。",
    )
    p.add_argument(
        "--match-keyword",
        action="append",
        default=[],
        help="指標名の部分一致キーワード（複数指定可）。未指定ならデフォルトを使います。",
    )
    p.add_argument(
        "--match",
        action="append",
        default=[],
        help="個別マッチ条件。形式: \"Country|name_contains\"（複数指定可）。",
    )
    p.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="個別除外条件（matchより優先）。形式: \"Country|name_contains\"（複数指定可）。",
    )
    p.add_argument(
        "--ntfy-server",
        type=str,
        default=DEFAULT_NTFY_SERVER,
        help=f"ntfyサーバURL（既定: {DEFAULT_NTFY_SERVER}）。",
    )
    p.add_argument(
        "--ntfy-topic",
        type=str,
        default=DEFAULT_NTFY_TOPIC,
        help=f"ntfyトピック名（既定: {DEFAULT_NTFY_TOPIC}）。",
    )
    p.add_argument(
        "--ntfy-title",
        type=str,
        default=DEFAULT_NTFY_TITLE,
        help=f"ntfy通知タイトル（既定: {DEFAULT_NTFY_TITLE}）。",
    )
    p.add_argument(
        "--ntfy-priority",
        type=str,
        default=DEFAULT_NTFY_PRIORITY,
        help=f"ntfy優先度（既定: {DEFAULT_NTFY_PRIORITY}）。",
    )
    p.add_argument(
        "--state",
        type=str,
        default=DEFAULT_STATE_FILENAME,
        help=f"stateファイルパス（既定: {DEFAULT_STATE_FILENAME}）。",
    )
    p.add_argument(
        "--rapidapi-key",
        type=str,
        default=None,
        help=f"RapidAPIキー。未指定の場合は環境変数 {ENV_RAPIDAPI_KEY} を参照。",
    )
    return p


def validate_settings(args: argparse.Namespace, project_dir: Path) -> Settings:
    # RapidAPI key
    rapidapi_key = args.rapidapi_key or os.environ.get(ENV_RAPIDAPI_KEY)
    if not rapidapi_key:
        raise SafeUsageError(
            "RapidAPIキーが未設定です。\n"
            f"設定方法: --rapidapi-key で指定するか、環境変数 {ENV_RAPIDAPI_KEY} を設定してください。"
        )

    # lookahead
    h = int(args.lookahead_hours)
    if h <= 0 or h > MAX_LOOKAHEAD_HOURS:
        raise SafeUsageError(
            f"lookahead-hours が不正です: {h}（1〜{MAX_LOOKAHEAD_HOURS} の範囲で指定してください）"
        )

    # max items
    mi = int(args.max_items)
    if mi <= 0 or mi > 50:
        raise SafeUsageError("max-items が不正です（1〜50の範囲で指定してください）")

    # min interval minutes
    min_int = int(args.min_interval_minutes)
    if min_int < 0 or min_int > 24 * 60:
        raise SafeUsageError("min-interval-minutes が不正です（0〜1440の範囲で指定してください）")

    # max notify per run
    mnr = int(args.max_notify_per_run)
    if mnr <= 0 or mnr > 200:
        raise SafeUsageError("max-notify-per-run が不正です（1〜200の範囲で指定してください）")

    # now override
    now_override = parse_datetime_to_utc(args.now) if args.now else None

    # lists
    countries = args.country if args.country else list(DEFAULT_COUNTRIES)
    match_keywords = args.match_keyword if args.match_keyword else list(DEFAULT_MATCH_KEYWORDS)
    match_rules = parse_rules(args.match, "match") if args.match else []
    ignore_rules = [IgnoreRule(r.country, r.name_contains) for r in parse_rules(args.ignore, "ignore")] if args.ignore else []

    # ntfy values (light validation)
    ntfy_server = args.ntfy_server.strip()
    if not (ntfy_server.startswith("http://") or ntfy_server.startswith("https://")):
        raise SafeUsageError("ntfy-server は http:// または https:// で始めてください。")
    ntfy_topic = args.ntfy_topic.strip()
    if not ntfy_topic or "/" in ntfy_topic:
        raise SafeUsageError("ntfy-topic が不正です（空やスラッシュを含む値は不可）。")

    # state path (same folder default)
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = project_dir / state_path

    danger, reason = is_dangerous_path(state_path)
    if danger:
        raise SafeUsageError(
            f"stateファイルパスが危険なため停止します: {state_path}\n理由: {reason}\n"
            "危険: 誤って重要な場所にファイルを作成/上書きする可能性があります。"
        )

    return Settings(
        rapidapi_key=rapidapi_key,
        lookahead_hours=h,
        max_items=mi,
        min_interval_minutes=min_int,
        max_notify_per_run=mnr,
        countries=countries,
        match_keywords=match_keywords,
        match_rules=match_rules,
        ignore_rules=ignore_rules,
        ntfy_server=ntfy_server,
        ntfy_topic=ntfy_topic,
        ntfy_title=args.ntfy_title,
        ntfy_priority=args.ntfy_priority,
        state_path=state_path,
        now_override_utc=now_override,
        apply=bool(args.apply),
    )


def print_plan(settings: Settings, now_utc: datetime) -> None:
    end_utc = now_utc + timedelta(hours=settings.lookahead_hours)
    now_s, now_jst = format_dt_pair(now_utc)
    end_s, end_jst = format_dt_pair(end_utc)

    print("=== 実行前確認（影響範囲）===")
    print(f"- モード: {'APPLY(通知送信/書き込みあり)' if settings.apply else 'DRY-RUN(送信なし/書き込みなし)'}")
    print(f"- 現在時刻(UTC): {now_s}")
    print(f"- 現在時刻(JST): {now_jst}")
    print(f"- 対象時間窓(UTC): {now_s} 〜 {end_s}")
    print(f"- 対象時間窓(JST): {now_jst} 〜 {end_jst}")
    print(f"- 対象国: {settings.countries}")
    print(f"- キーワード: {settings.match_keywords}")
    print(f"- 追加matchルール: {[f'{r.country}|{r.name_contains}' for r in settings.match_rules]}")
    print(f"- ignoreルール: {[f'{r.country}|{r.name_contains}' for r in settings.ignore_rules]}")
    print(f"- 通知最大件数: {settings.max_items}")
    print(f"- 同一イベント最小通知間隔(分): {settings.min_interval_minutes}")
    print(f"- 1実行あたり最大通知数: {settings.max_notify_per_run}")
    print(f"- ntfy: {settings.ntfy_server.rstrip('/')}/{settings.ntfy_topic} (Title={settings.ntfy_title}, Priority={settings.ntfy_priority})")
    print(f"- stateファイル: {settings.state_path}")
    print("==============================")


# =========================
# main
# =========================
def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    try:
        try_configure_stdio_utf8()
        project_dir = Path(__file__).resolve().parent
        args = build_arg_parser().parse_args(argv)
        settings = validate_settings(args, project_dir=project_dir)

        now_utc = settings.now_override_utc or utc_now()
        print_plan(settings, now_utc)

        raw_items = fetch_events(settings)
        events = build_events(raw_items)
        targets = apply_filters(settings, events)

        if not targets:
            print("対象となる指標がありません（通知しません）。")
            return 0

        state = load_state(settings.state_path)

        any_sent = False
        sent_count = 0
        for ev in targets:
            if sent_count >= settings.max_notify_per_run:
                print(f"1実行あたり最大通知数に達したため以降は送信しません: {settings.max_notify_per_run}")
                break

            skip, remaining = should_skip_due_to_min_interval(
                state=state,
                ev=ev,
                now_utc=now_utc,
                min_interval_minutes=settings.min_interval_minutes,
            )
            if skip:
                if remaining is not None:
                    print(f"同一イベントの最小通知間隔により抑止しました（残り約{remaining}秒）: {ev.key}")
                else:
                    print(f"同一イベントの最小通知間隔により抑止しました: {ev.key}")
                continue

            msg = build_message(now_utc, ev)
            print("--- 通知メッセージ ---")
            print(msg.rstrip())
            print("----------------------")

            if settings.apply:
                ntfy_send(settings, msg)
                state = update_state_after_send(state, ev, now_utc=now_utc)
                any_sent = True
                sent_count += 1
            else:
                print("dry-runのため通知送信は行いません（--applyで実送信）。")

        if settings.apply and any_sent:
            write_json_file_atomic(settings.state_path, state)
            print(f"stateを更新しました: {settings.state_path}")
        elif settings.apply and not any_sent:
            print("送信対象がありません（重複抑止など）: stateは更新しません。")

        return 0

    except SafeUsageError as ex:
        eprint("ERROR: " + str(ex))
        return 2
    except KeyboardInterrupt:
        eprint("中断されました。")
        return 130
    except Exception as ex:
        # 予期せぬ例外は、何が危険かを明示しつつ止める
        eprint(
            "ERROR: 予期せぬ例外が発生しました。\n"
            f"内容: {type(ex).__name__}: {ex}\n"
            "危険: 途中で処理が止まったため、通知/state更新が完了していない可能性があります。"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


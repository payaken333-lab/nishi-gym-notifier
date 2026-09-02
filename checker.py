# -*- coding: utf-8 -*-
"""
西宮市スポーツネット 空き状況チェッカー(ログイン不要・ゲスト検索版)

流れ:
  トップページ
    → 「ログインせずに空き状況を検索」
    → 「利用目的で検索」
    → PURPOSE_CATEGORY (例: 体育室)
    → PURPOSE_ITEM (例: クォーターテニス・ピックルボール)
    → 「選択した条件で次へ」
    → 検索結果一覧で TARGET_FACILITIES にチェック
    → 「選択した施設で検索」
    → 空き状況カレンダー(複数施設が縦に並ぶ)を読み取り
    → WATCH_CONDITIONS に合う「○」があればLINE通知

必要なGitHub Secrets:
    LINE_CHANNEL_TOKEN … LINE Messaging APIのチャネルアクセストークン
    LINE_USER_ID        … 通知を送る先(自分)のLINEユーザーID

ログイン用の利用者番号・パスワードは不要(空き照会はゲストで可能なため)。
"""

import json
import os
import re
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone

import jpholiday
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from config import (
    PURPOSE_CATEGORY,
    PURPOSE_ITEM,
    TARGET_FACILITIES,
    WATCH_CONDITIONS,
    DAYS_AHEAD,
)

BASE_URL = "https://yoyaku-nishi.growone.net/sportsnet/Welcome.cgi"
SEEN_FILE = "seen_slots.json"

JST = timezone(timedelta(hours=9))
ACTIVE_START = dtime(6, 45)   # この時刻以降にチェックを行う
ACTIVE_END = dtime(23, 0)     # この時刻以降はチェックを行わない


def is_within_active_hours() -> bool:
    """日本時間で 06:45〜23:00 の間かどうか"""
    now_jst = datetime.now(JST).time()
    return ACTIVE_START <= now_jst < ACTIVE_END

LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")


def slot_key(slot: dict) -> str:
    """空きコマを一意に表す文字列(施設+日付+時間帯)"""
    return f"{slot['facility']}|{slot['date']}|{slot['time_slot']}"


def load_seen_keys() -> set:
    """前回までに通知済みの空きコマ一覧を読み込む"""
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen_keys(keys: set) -> None:
    """今回時点で空いているコマ一覧を保存する(次回との比較用)"""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(keys), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[警告] {SEEN_FILE} の保存に失敗しました: {e}")


def send_line_message(text: str) -> None:
    """LINE Messaging API で自分に通知を送る"""
    if not LINE_CHANNEL_TOKEN or not LINE_USER_ID:
        print("LINEの設定が未登録のため通知をスキップしました:\n", text)
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}],
    }
    res = requests.post(url, headers=headers, json=payload, timeout=10)
    if res.status_code != 200:
        print("LINE通知に失敗しました:", res.status_code, res.text)


def save_debug(page, label: str) -> None:
    """デバッグ用に、その時点の画面のHTMLとスクリーンショットを保存する"""
    try:
        os.makedirs("debug", exist_ok=True)
        with open(f"debug/{label}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        page.screenshot(path=f"debug/{label}.png", full_page=True)
    except Exception as e:
        print(f"[警告] デバッグ保存({label})に失敗しました: {e}")


def navigate_to_results(page):
    """トップページから検索結果一覧までクリックで進む"""
    page.goto(BASE_URL)
    page.get_by_text("ログインせずに空き状況を検索", exact=False).first.click()
    page.wait_for_load_state("networkidle")
    save_debug(page, "0a_after_guest_search")

    page.get_by_text("利用目的で検索", exact=False).first.click()
    page.wait_for_load_state("networkidle")
    save_debug(page, "0b_after_purpose_search")

    page.get_by_text(PURPOSE_CATEGORY, exact=True).first.click()
    page.wait_for_load_state("networkidle")
    save_debug(page, "0c_after_category")

    page.get_by_text(PURPOSE_ITEM, exact=True).first.click()
    page.wait_for_load_state("networkidle")
    save_debug(page, "0d_after_item")

    page.get_by_text("選択した条件で次へ", exact=False).first.click()
    page.wait_for_load_state("networkidle")
    save_debug(page, "0e_facility_list")


def _normalize(text: str) -> str:
    """改行・スペースの違いを無視して比較するための正規化"""
    return re.sub(r"\s+", "", text or "")


def select_facilities(page):
    """
    検索結果一覧で対象施設にチェックを入れて次へ進む。

    施設名と部屋名が画面上で2行に分かれて表示されており、
    改行やスペースの入り方がテキスト完全一致では拾いきれないため、
    「チェックボックスを囲むカードの中の文字列」を正規化して
    部分一致で判定する方式にしている。
    """
    remaining = {_normalize(label): label for label in TARGET_FACILITIES}
    checkboxes = page.locator('input[type="checkbox"]')
    count = checkboxes.count()

    checked_labels = []
    for i in range(count):
        cb = checkboxes.nth(i)
        try:
            container = cb.locator(
                "xpath=ancestor::*[self::li or self::tr or self::div][1]"
            ).first
            container_text = _normalize(container.inner_text())
        except Exception:
            continue

        for norm_label, original_label in list(remaining.items()):
            if norm_label in container_text:
                cb.check()
                checked_labels.append(original_label)
                del remaining[norm_label]
                break

    for missing in remaining.values():
        print(f"[警告] 施設「{missing}」のチェックボックスが見つかりませんでした。"
              f" config.py の表記がサイトの実際の文言と一致しているか確認してください。")

    if checked_labels:
        print(f"[情報] チェックを入れた施設: {', '.join(checked_labels)}")

    save_debug(page, "1_facilities_checked")

    # デバッグ用: 検索ボタンが呼び出すJS関数の中身を直接のぞいておく
    try:
        func_src = page.evaluate(
            "typeof isSubmitDataSet !== 'undefined' ? isSubmitDataSet.toString() : '(isSubmitDataSet is not defined)'"
        )
        ignore_check_src = page.evaluate(
            "typeof isIgnoreCheck !== 'undefined' ? isIgnoreCheck.toString() : '(isIgnoreCheck is not defined)'"
        )
        checked_count = page.evaluate(
            "document.querySelectorAll('input[type=checkbox]:checked').length"
        )
        with open("debug/isSubmitDataSet.js", "w", encoding="utf-8") as f:
            f.write(
                f"// checked checkboxes: {checked_count}\n\n"
                f"{func_src}\n\n// --- isIgnoreCheck ---\n\n{ignore_check_src}"
            )
    except Exception as e:
        print(f"[警告] JS関数の取得に失敗しました: {e}")

    try:
        page.get_by_text("選択した施設で検索", exact=False).first.click()
        page.wait_for_timeout(1000)
    except Exception as e:
        print(f"[警告] 「選択した施設で検索」のクリックに失敗しました: {e}")

    save_debug(page, "2_after_search_click")

    # クリックだけで画面が進まない場合(二重送信チェック等)への保険:
    # フォームを直接送信してみる
    still_same_page = False
    try:
        still_same_page = page.get_by_text("選択した施設で検索", exact=False).first.is_visible()
    except Exception:
        pass

    if still_same_page:
        print("[情報] 通常のクリックで進まなかったため、フォームを直接送信します。")
        try:
            page.evaluate(
                "(() => { const f = document.getElementById('formMain');"
                " if (f) { f.action.value = 'Enter'; f.submit(); } })()"
            )
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"[警告] フォームの直接送信に失敗しました: {e}")

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as e:
        print(f"[警告] ページ読み込み待機でタイムアウトしました: {e}")

    save_debug(page, "3_after_wait")


def _max_table_columns(page) -> int:
    """ページ内の表の中で、一番列数が多いものの列数を返す(表示日数の判定用)"""
    try:
        return page.evaluate(
            "Math.max(0, ...Array.from(document.querySelectorAll('table')).map("
            "t => { const tr = t.querySelector('tr'); return tr ? tr.children.length : 0; }))"
        )
    except Exception:
        return 0


def try_expand_to_31_days(page):
    """表示切替を31日間にして、反映ボタンまで押す(必要な場合だけ直接送信も試す)"""
    try:
        page.get_by_text("31日間", exact=True).first.click()
    except Exception as e:
        print(f"[警告] 「31日間」の選択に失敗しました: {e}")
        return

    try:
        page.get_by_text("選択した条件で表示", exact=False).first.click()
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as e:
        print(f"[警告] 「選択した条件で表示」のクリックに失敗しました: {e}")

    # 列数が8(時間帯列+7日)以下のままなら、まだ7日間表示のまま変わっていないと判断し、
    # フォームを直接送信して反映を試みる(二重送信を避けるため、必要な時だけ実行)
    if _max_table_columns(page) <= 8:
        print("[情報] 表示切替クリックで反映されなかったため、フォームを直接送信します。")
        try:
            page.evaluate(
                "(() => { const f = document.getElementById('formMain');"
                " if (f) { f.action.value = 'Setup'; f.submit(); } })()"
            )
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"[警告] 表示切替フォームの直接送信に失敗しました: {e}")


def guess_year_for(month: int, day: int, today: date) -> int:
    """月/日だけの表記から年を推定する(年またぎ対応)"""
    year = today.year
    if month < today.month - 6:
        year += 1
    elif month > today.month + 6:
        year -= 1
    return year


def parse_calendar_html(html: str, today: date) -> list[dict]:
    """
    ページ全体のHTMLから、施設ごとのカレンダー表を探して
    空き(○)のマスを抽出する。

    サイトの正確なタグ名・class名が不明なため、
    「テーブルを見つけて、その直前に出てくる施設名っぽいテキストを探す」
    というヒューリスティックで実装している。
    実際の出力がずれる場合は、workflow実行結果のログを見て調整する。
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    tables = soup.find_all("table")
    print(f"[デバッグ] 見つかったtable数: {len(tables)} / "
          f"ページ内の「空いています」アイコン数: {html.count('空いています')}")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # ヘッダ行から日付を取得(1列目は時間帯なので2列目以降)
        header_cells = rows[0].find_all(["th", "td"])
        dates = []
        for c in header_cells[1:]:
            text = c.get_text(" ", strip=True)
            m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                year = guess_year_for(month, day, today)
                dates.append(date(year, month, day))
            else:
                dates.append(None)

        # このテーブルがどの施設のものかを、直前のテキストから推定する。
        # 「体育室半面」は4施設共通の文言で判定材料にならないため、
        # 施設名そのもの(先頭の部分)だけで判定する。
        facility_name = None
        for prev in table.find_all_previous(string=True):
            prev_text = prev.strip()
            if not prev_text:
                continue
            prev_norm = _normalize(prev_text)
            for target in TARGET_FACILITIES:
                facility_only = _normalize(target.split("　")[0])
                if facility_only and facility_only in prev_norm:
                    facility_name = target
                    break
            if facility_name:
                break

        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            if not cells:
                continue
            time_slot = cells[0].get_text(" ", strip=True)
            time_slot = re.sub(r"\s+", "", time_slot)

            for d, cell in zip(dates, cells[1:]):
                if d is None:
                    continue
                img = cell.find("img")
                alt_text = (img.get("alt") if img else "") or ""
                text = cell.get_text(" ", strip=True)
                if "空いて" in alt_text:
                    results.append(
                        {
                            "facility": facility_name or "(不明な施設)",
                            "date": d.isoformat(),
                            "weekday": d.weekday(),
                            "time_slot": time_slot,
                            "raw": text,
                        }
                    )

    return results


def is_weekday_business_day(d: date) -> bool:
    """平日(月〜金)かつ祝日でない日か"""
    return d.weekday() < 5 and not jpholiday.is_holiday(d)


def matches_watch_conditions(slot: dict) -> bool:
    d = date.fromisoformat(slot["date"])
    is_business_day = is_weekday_business_day(d)

    m = re.match(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", slot["time_slot"])
    if not m:
        return False
    slot_start, slot_end = m.group(1), m.group(2)

    for cond in WATCH_CONDITIONS:
        if cond["type"] == "weekday" and not is_business_day:
            continue
        if cond["type"] == "holiday" and is_business_day:
            continue

        if "time_slots" in cond:
            if slot["time_slot"] not in cond["time_slots"]:
                continue

        if "time_range" in cond:
            range_start, range_end = cond["time_range"]
            if not (slot_start >= range_start and slot_end <= range_end):
                continue

        return True

    return False


def main():
    if not is_within_active_hours():
        print("[情報] 現在は監視対象外の時間帯(23:00〜06:45)のため、チェックをスキップします。")
        return

    today = date.today()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[console:{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: console_logs.append(f"[pageerror] {err}"))
        page.on("dialog", lambda dialog: (
            console_logs.append(f"[dialog:{dialog.type}] {dialog.message}"),
            dialog.accept(),
        ))

        navigate_to_results(page)
        select_facilities(page)
        try_expand_to_31_days(page)
        save_debug(page, "4_before_parse")

        try:
            os.makedirs("debug", exist_ok=True)
            with open("debug/console.log", "w", encoding="utf-8") as f:
                f.write("\n".join(console_logs) if console_logs else "(何も記録されませんでした)")
        except Exception as e:
            print(f"[警告] コンソールログの保存に失敗しました: {e}")

        all_slots = []
        days_covered = 0
        max_pages = 6  # 安全のための上限(31日間 x 6 = 186日ぶん)

        for _ in range(max_pages):
            html = page.content()
            all_slots.extend(parse_calendar_html(html, today))
            days_covered += 31

            if days_covered >= DAYS_AHEAD:
                break

            try:
                page.get_by_text("次の31日分", exact=False).first.click()
                page.wait_for_load_state("networkidle")
            except Exception:
                break

        browser.close()

    hits = [s for s in all_slots if matches_watch_conditions(s)]
    current_keys = {slot_key(s) for s in hits}
    seen_keys = load_seen_keys()

    new_hits = [s for s in hits if slot_key(s) not in seen_keys]

    if new_hits:
        lines = ["🏸 新しい空きが見つかりました!"]
        for s in new_hits:
            lines.append(f"{s['facility']} / {s['date']} / {s['time_slot']}")
        send_line_message("\n".join(lines))
    else:
        print(f"新しい空きはありませんでした。(現在の該当コマ数: {len(current_keys)} / 全チェック数: {len(all_slots)})")

    save_seen_keys(current_keys)


if __name__ == "__main__":
    main()

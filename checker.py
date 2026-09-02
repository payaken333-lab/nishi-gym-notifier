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

import re
import sys
from datetime import date

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

import os

BASE_URL = "https://yoyaku-nishi.growone.net/sportsnet/Welcome.cgi"

LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")


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


def navigate_to_results(page):
    """トップページから検索結果一覧までクリックで進む"""
    page.goto(BASE_URL)
    page.get_by_text("ログインせずに空き状況を検索", exact=False).first.click()
    page.wait_for_load_state("networkidle")

    page.get_by_text("利用目的で検索", exact=False).first.click()
    page.wait_for_load_state("networkidle")

    page.get_by_text(PURPOSE_CATEGORY, exact=True).first.click()
    page.wait_for_load_state("networkidle")

    page.get_by_text(PURPOSE_ITEM, exact=True).first.click()
    page.wait_for_load_state("networkidle")

    page.get_by_text("選択した条件で次へ", exact=False).first.click()
    page.wait_for_load_state("networkidle")


def select_facilities(page):
    """検索結果一覧で対象施設にチェックを入れて次へ進む"""
    for facility_label in TARGET_FACILITIES:
        checked = False
        # パターン1: ラベルのテキスト自体をクリックするとチェックが入るタイプ
        try:
            page.get_by_text(facility_label, exact=True).first.click()
            checked = True
        except Exception:
            pass

        # パターン2: 近くのチェックボックスを直接探して押すタイプ(フォールバック)
        if not checked:
            try:
                el = page.get_by_text(facility_label, exact=True).first
                checkbox = el.locator(
                    "xpath=ancestor::*[self::li or self::tr or self::div][1]//input[@type='checkbox']"
                ).first
                checkbox.check()
                checked = True
            except Exception:
                pass

        if not checked:
            print(f"[警告] 施設「{facility_label}」のチェックボックスが見つかりませんでした。"
                  f" config.py の表記がサイトの実際の文言と一致しているか確認してください。")

    page.get_by_text("選択した施設で検索", exact=False).first.click()
    page.wait_for_load_state("networkidle")


def try_expand_to_31_days(page):
    """表示切替を31日間にする(あれば)"""
    try:
        page.get_by_text("31日間", exact=True).first.click()
        page.wait_for_load_state("networkidle")
    except Exception:
        pass


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
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # ヘッダ行から日付を取得(1列目は時間帯なので2列目以降)
        header_cells = rows[0].find_all(["th", "td"])
        dates = []
        for c in header_cells[1:]:
            text = c.get_text(" ", strip=True)
            m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                year = guess_year_for(month, day, today)
                dates.append(date(year, month, day))
            else:
                dates.append(None)

        # このテーブルがどの施設のものかを、直前のテキストから推定する
        facility_name = None
        for prev in table.find_all_previous(string=True):
            prev_text = prev.strip()
            if not prev_text:
                continue
            for target in TARGET_FACILITIES:
                if prev_text in target or target in prev_text:
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
                text = cell.get_text(" ", strip=True)
                if "○" in text:
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


def matches_watch_conditions(slot: dict) -> bool:
    for cond in WATCH_CONDITIONS:
        if cond.get("weekday") is not None and cond["weekday"] != slot["weekday"]:
            continue
        if cond.get("time_slot") and cond["time_slot"] != slot["time_slot"]:
            continue
        return True
    return False


def main():
    today = date.today()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        navigate_to_results(page)
        select_facilities(page)
        try_expand_to_31_days(page)

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

    if hits:
        lines = ["🏸 空きが見つかりました!"]
        for s in hits:
            lines.append(f"{s['facility']} / {s['date']} / {s['time_slot']}")
        send_line_message("\n".join(lines))
    else:
        print(f"該当する空きはありませんでした。(チェックしたコマ数: {len(all_slots)})")


if __name__ == "__main__":
    main()

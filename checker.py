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
ACTIVE_START = dtime(6, 15)   # この時刻以降にチェックを行う


def is_within_active_hours() -> bool:
    """日本時間で 06:15〜24:00(実質、夜だけ休む)かどうか"""
    now_jst = datetime.now(JST).time()
    return now_jst >= ACTIVE_START


LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHAN

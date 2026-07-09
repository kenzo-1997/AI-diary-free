#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
無料版・AI日記ジェネレーター
------------------------------------------------
GitHub Issue に書いた「その日のメモ」を読み取り、
Google Gemini で 思考ログ・日記本文・絵日記(SVG) を生成して保存する。

GitHub Actions から毎晩23時(JST)に自動実行される想定。
必要な環境変数（Actions が自動で渡す/Secretsで設定）:
  - GEMINI_API_KEY : Google AI Studio で取得したAPIキー（Secretsに登録）
  - GEMINI_MODEL   : 使うモデル名（任意。未設定なら gemini-2.0-flash）
  - GH_TOKEN       : GitHub Actions が自動発行するトークン
  - GH_REPO        : "owner/repo"（Actions が自動で渡す）

依存ライブラリなし（Python標準ライブラリのみ）。
"""
import os, re, sys, json, ssl, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
TODAY = NOW.strftime("%Y-%m-%d")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.0-flash"
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
GH_REPO = os.environ.get("GH_REPO", "").strip()
DIARY_LABEL = os.environ.get("DIARY_LABEL", "diary").strip()


# ---------- GitHub API ----------
def gh_api(method, path, payload=None):
    url = f"https://api.github.com{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-diary-free",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
        return json.loads(body) if body else None


def fetch_diary_issues():
    """diaryラベルの付いた open issue を集める（ラベルが無ければ全 open issue）。"""
    issues = gh_api("GET", f"/repos/{GH_REPO}/issues?state=open&labels={DIARY_LABEL}&per_page=50") or []
    if not issues:
        # ラベル運用してない人向けフォールバック: 全 open issue
        issues = gh_api("GET", f"/repos/{GH_REPO}/issues?state=open&per_page=50") or []
    # Pull Request を除外
    return [i for i in issues if "pull_request" not in i]


def fetch_comments(number):
    return gh_api("GET", f"/repos/{GH_REPO}/issues/{number}/comments?per_page=100") or []


# ---------- Gemini ----------
def call_gemini(raw_notes):
    endpoint = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    prompt = f"""あなたはユーザー専属の「記録係」です。以下は本人がその日に書いた素朴なメモです。
これをもとに、日本語で「思考ログ」「日記本文」「絵日記カードの素材」を作ってください。
批評や評価はせず、本人の言葉と流れを尊重して記録に徹してください。

# その日のメモ（{TODAY}）
{raw_notes}

# 出力形式（必ず下記キーのJSONだけを返す。前後に説明文やコードフェンスを付けない）
{{
  "mood": "その日の気分を表す絵文字1つ",
  "tags": ["トピックを表す短い日本語タグ", "最大5個"],
  "highlights": ["その日の要点を短く", "最大3個"],
  "thought_log_md": "思考ログのMarkdown本文。'## 時刻や場面 タイトル' の見出しで、背景・検討・結論を箇条書きで整理。",
  "diary_body_md": "日記本文のMarkdown。その日の流れを叙述的に。900文字前後。見出し(##)を2〜3個使ってよい。",
  "one_line": "絵日記に載せる、その日を一言で表すセリフ（30文字以内）"
}}"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "response_mime_type": "application/json"},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(endpoint, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"[エラー] Gemini API 呼び出し失敗: {e.code}\n{e.read().decode()[:500]}")
    try:
        text = res["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        sys.exit(f"[エラー] Geminiの応答形式が想定外:\n{json.dumps(res)[:500]}")
    # 念のためコードフェンスを剥がす
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


# ---------- SVG 絵日記 ----------
def render_svg(mood, highlights, tags, one_line, weekday):
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    md, dd = NOW.strftime("%-m"), NOW.strftime("%-d")
    hi = (highlights + ["", "", ""])[:3]
    hl = "".join(
        f'<circle cx="98" cy="{306+idx*75}" r="7" fill="#5b8def"/>'
        f'<text x="126" y="{318+idx*75}">{esc(h)}</text>'
        for idx, h in enumerate(hi) if h
    )
    tag_x = 84
    tags_svg = ""
    for t in (tags or [])[:5]:
        w = 40 + len(t) * 26
        tags_svg += (f'<rect x="{tag_x}" y="560" width="{w}" height="42" rx="21" '
                     f'fill="#ffffff" opacity="0.14"/>'
                     f'<text x="{tag_x+w/2:.0f}" y="589" text-anchor="middle">{esc(t)}</text>')
        tag_x += w + 12
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" font-family="'Hiragino Sans','Yu Gothic','Meiryo',sans-serif">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1e2745"/><stop offset="0.6" stop-color="#2b3a63"/><stop offset="1" stop-color="#3c5088"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)"/>
  <text x="80" y="150" font-size="96" font-weight="bold" fill="#ffffff">{md}.{dd} <tspan font-size="52" fill="#b9c7ea">{esc(weekday)}</tspan></text>
  <text x="84" y="200" font-size="30" fill="#9fb0d8" letter-spacing="4">{NOW.year}年{md}月{dd}日</text>
  <text x="1130" y="588" font-size="105" text-anchor="end">{esc(mood) or "📝"}</text>
  <rect x="84" y="240" width="640" height="3" rx="1.5" fill="#5b8def" opacity="0.9"/>
  <g font-size="32" fill="#eef2fb">{hl}</g>
  <text x="126" y="530" font-size="26" fill="#9fb0d8">{esc(one_line)}</text>
  <g font-size="24" fill="#e9eef8">{tags_svg}</g>
</svg>
'''


# ---------- ファイル出力 ----------
def write_files(data):
    ym = NOW.strftime("%Y/%m")
    weekday = "月火水木金土日"[NOW.weekday()]

    # 思考ログ
    tdir = f"thoughts/{ym}"
    os.makedirs(tdir, exist_ok=True)
    tpath = f"{tdir}/{TODAY}.md"
    topics = ", ".join(data.get("tags", []))
    with open(tpath, "w", encoding="utf-8") as f:
        f.write(f"---\ndate: {TODAY}\ntopics: [{topics}]\n---\n\n")
        f.write(data.get("thought_log_md", "").strip() + "\n")

    # 日記本文
    ddir = f"diary/{ym}"
    os.makedirs(ddir, exist_ok=True)
    dpath = f"{ddir}/{TODAY}.md"
    hl_yaml = "".join(f"  - {h}\n" for h in data.get("highlights", []))
    with open(dpath, "w", encoding="utf-8") as f:
        f.write(f"---\ndate: {TODAY}\nmood: {data.get('mood','📝')}\n")
        f.write(f"tags: [{topics}]\nhighlights:\n{hl_yaml}---\n\n")
        f.write(data.get("diary_body_md", "").strip() + "\n")

    # 絵日記SVG
    spath = f"{ddir}/{TODAY}.svg"
    with open(spath, "w", encoding="utf-8") as f:
        f.write(render_svg(data.get("mood", "📝"), data.get("highlights", []),
                           data.get("tags", []), data.get("one_line", ""), weekday))

    return tpath, dpath, spath


# ---------- メイン ----------
def main():
    if not GEMINI_API_KEY:
        sys.exit("[エラー] GEMINI_API_KEY が未設定です。リポジトリの Settings → Secrets に登録してください。")

    issues = fetch_diary_issues()
    if not issues:
        print("今日の記録メモ（Issue）が見つかりませんでした。何もせず終了します。")
        return

    # メモを集約
    chunks = []
    for i in issues:
        chunks.append(f"■ {i.get('title','')}\n{i.get('body','') or ''}")
        for c in fetch_comments(i["number"]):
            chunks.append(c.get("body", "") or "")
    raw_notes = "\n\n".join(x for x in chunks if x.strip())
    print(f"{len(issues)}件のIssueからメモを取得しました。")

    data = call_gemini(raw_notes)
    tpath, dpath, spath = write_files(data)
    print(f"生成完了: {tpath} / {dpath} / {spath}")

    # Issueに結果を返信してクローズ
    link = f"https://github.com/{GH_REPO}/blob/main/{dpath}"
    for i in issues:
        try:
            gh_api("POST", f"/repos/{GH_REPO}/issues/{i['number']}/comments",
                   {"body": f"📝 今日のまとめを作成しました！\n\n- 日記: {link}\n- 絵日記カードも `diary/` に保存されています。\n\nこのIssueはクローズします。おつかれさまでした。"})
            gh_api("PATCH", f"/repos/{GH_REPO}/issues/{i['number']}", {"state": "closed"})
        except Exception as e:
            print(f"Issue #{i['number']} への返信/クローズに失敗（続行）: {e}")


if __name__ == "__main__":
    main()

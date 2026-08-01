"""profile.json（構造化マスター）の読み書き・profile.md 生成・経験年数の再計算。

依存ゼロ（Python 標準ライブラリのみ）。server.py から利用する。
"""
from __future__ import annotations
import json
import os
import re
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROFILE_JSON = os.path.join(ROOT, "profile.json")
PROFILE_MD = os.path.join(ROOT, "profile.md")
OUTPUT_DIR = os.path.join(ROOT, "output")
RESUME_MD = os.path.join(OUTPUT_DIR, "職務経歴書.md")
SKILLSHEET_MD = os.path.join(OUTPUT_DIR, "スキルシート.md")

# 氏名・年齢・住所・最寄駅などの個人情報ラベル（既定では出力に載せない）
PERSONAL_KEYS = ("氏名", "イニシャル", "生年", "年齢", "住所", "最寄")


# ---------------------------------------------------------------- 期間ユーティリティ
def parse_ym(s: str):
    """'2023/11' -> (2023, 11)。失敗時 None。"""
    if not s:
        return None
    m = re.match(r"\s*(\d{4})\s*/\s*(\d{1,2})", s)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def months_between(start, end):
    """(y,m) start〜end を含む年月の集合。"""
    out = set()
    if not start or not end or start > end:
        return out
    y, m = start
    while (y, m) <= end:
        out.add((y, m))
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def fmt_months(n: int) -> str:
    if n <= 0:
        return ""
    y, m = divmod(n, 12)
    if y and m:
        return f"{y}年{m}カ月"
    if y:
        return f"{y}年"
    return f"{m}カ月"


def years_to_months(s: str) -> int:
    if not s:
        return 0
    a = re.search(r"(\d+)\s*年", s)
    b = re.search(r"(\d+)\s*カ月", s)
    return (int(a.group(1)) * 12 if a else 0) + (int(b.group(1)) if b else 0)


def default_base_date():
    today = date.today()
    return f"{today.year}/{today.month:02d}"


# ---------------------------------------------------------------- 経験年数 再計算
def recompute_years(data: dict, base_date: str | None = None):
    """メイン技術として担当した案件期間の和集合 × 現在日付で経験年数を再計算。

    採用値 = max(本人申告 years_manual, メイン案件の和集合)。
    usage_only / aggregate の技術は対象外。戻り値は変更点のリスト。
    """
    base_date = base_date or data.get("base_date") or default_base_date()
    base = parse_ym(base_date)

    # メイン技術 -> 期間集合 を案件・個人開発から構築
    periods = []  # (set_of_months, [main_tech...])
    for p in data.get("projects", []):
        start = parse_ym(p.get("period_start", ""))
        end = base if p.get("ongoing") else parse_ym(p.get("period_end", ""))
        periods.append((months_between(start, end), set(p.get("main_tech", []))))
    pd = data.get("personal_dev")
    if pd:
        start = parse_ym(pd.get("period_start", ""))
        end = base if pd.get("ongoing") else parse_ym(pd.get("period_end", ""))
        periods.append((months_between(start, end), set(pd.get("main_tech", []))))

    changes = []
    for sk in data.get("skills", []):
        if sk.get("usage_only") or sk.get("aggregate"):
            continue
        union = set()
        for months, mains in periods:
            if sk["name"] in mains:
                union |= months
        floor = years_to_months(sk.get("years_manual", ""))
        adopted = max(floor, len(union))
        new = fmt_months(adopted)
        # 元値も計算結果も無ければ空欄のまま
        old = sk.get("years", "")
        if new != old:
            changes.append({"name": sk["name"], "old": old, "new": new})
        sk["years"] = new
    data["base_date"] = base_date
    return changes


# ---------------------------------------------------------------- Markdown 生成
def _kv(label, value):
    return f"- {label}：{value}"


def render_markdown(data: dict) -> str:
    L = []
    L.append("# プロフィール（マスターデータ）")
    L.append("")
    L.append("> ⚠ このファイルは profile.json から自動生成されます。直接編集せず、エディタ（tools/profile-editor）で編集してください。")
    L.append(">")
    L.append("> このファイルは「事実の唯一の格納先」です。スキルシート/職務経歴書は、この内容を")
    L.append("> サンプルのフォーマットに合わせて出力して作ります。本文に書くのは確認済みの事実のみ。")
    L.append("> 不明な値は空欄のまま、または「（要確認）」と書いてください。創作・推測で埋めないこと。")
    L.append("")

    # 基本情報
    L.append("## 基本情報")
    L.append("")
    for kv in data.get("basic", []):
        L.append(_kv(kv["label"], kv["value"]))
    L.append("")

    # スキルサマリ
    L.append("## スキルサマリ（3〜4行）")
    L.append("")
    for kv in data.get("summary", []):
        L.append(_kv(kv["label"], kv["value"]))
    L.append("")

    # 技術スキル
    L.append("## 技術スキル")
    L.append("")
    L.append("> 習熟度の凡例：◎=実務で主導できる / ○=実務経験あり / △=学習・補助レベル")
    L.append("> ※元資料に習熟度の記載が無いため「習熟度」列は未記入。")
    L.append("> ※経験年数は「メイン技術として担当した案件期間」を基準に算出"
             f"（基準日 {data.get('base_date','')}、採用値=max(本人申告, メイン案件の和集合)）。")
    L.append(">   サブ利用は年数に含めない。各案件のメイン技術はプロジェクト経歴の「メイン技術」行を参照。")
    L.append("> ※構築・運用しておらず「構築済み環境を利用しただけ」の技術（例：AWS）は、経験年数の概念に馴染まないため")
    L.append(">   年数を付けず「利用のみ」と備考に明記する。")
    L.append("")
    L.append("| 分類 | 技術 | 経験年数 | 習熟度 | 備考 |")
    L.append("| --- | --- | --- | --- | --- |")
    for sk in data.get("skills", []):
        L.append(f"| {sk.get('category','')} | {sk.get('name','')} | {sk.get('years','')} | "
                 f"{sk.get('level','')} | {sk.get('note','')} |")
    L.append("")

    # 担当可能工程
    L.append("## 担当可能工程")
    L.append("")
    for pr in data.get("processes", []):
        box = "x" if pr.get("checked") else " "
        note = pr.get("note", "")
        L.append(f"- [{box}] {pr['name']}{note}")
    L.append("")

    # プロジェクト経歴
    L.append("## プロジェクト経歴（新しい順）")
    L.append("")
    L.append("> 客先名・固有のプロジェクト名は伏せ、業種・概要で表現する。")
    L.append("> ※ここには会社業務（受託・常駐）の案件のみを記載する。個人開発は「自己PR」へ。")
    L.append("")
    for p in data.get("projects", []):
        L.append(f"### {p.get('title','')}")
        L.append("")
        L.append(_kv("期間", p.get("period_text", "")))
        L.append(_kv("業種 / 案件概要", p.get("industry", "")))
        for sub in p.get("industry_sub", []):
            L.append(f"  - {sub}")
        L.append(_kv("役割・ポジション", p.get("role", "")))
        L.append(_kv("チーム規模", p.get("team", "")))
        L.append(_kv("担当工程", p.get("phases", "")))
        tech = p.get("tech", [])
        if len(tech) == 1:
            L.append(_kv("使用技術", tech[0]))
        else:
            L.append("- 使用技術：")
            for t in tech:
                L.append(f"  - {t}")
        note = p.get("main_tech_note", "")
        mt = " / ".join(p.get("main_tech", []))
        mt_val = mt + (f"（{note}）" if note else "")
        L.append(_kv("メイン技術（経験年数の算入対象）", mt_val))
        L.append("- 担当内容・実績：")
        for a in p.get("achievements", []):
            L.append(f"  - {a}")
        L.append("")

    # 資格
    L.append("## 資格・認定")
    L.append("")
    for c in data.get("certs", []):
        L.append(f"- {c}")
    L.append("")

    # 公開実績
    L.append("## 公開実績（OSS / 登壇 / 記事 等・任意）")
    L.append("")
    pub = data.get("public", [])
    if pub:
        for x in pub:
            L.append(f"- {x}")
    else:
        L.append("-")
    L.append("")

    # 自己PR
    L.append("## 自己PR（任意）")
    L.append("")
    pd = data.get("personal_dev")
    if pd:
        L.append("### 個人開発（会社業務ではない）")
        L.append("")
        for kv in pd.get("items", []):
            L.append(_kv(kv["label"], kv["value"]))
        L.append("")
    ai = data.get("ai_activity", [])
    if ai:
        L.append("### 個人での AI 活用（Claude Code 等）")
        L.append("")
        for x in ai:
            L.append(f"- {x}")
        L.append("")
    tw = data.get("teamwork", [])
    if tw:
        L.append("### チームワーク・コミュニケーション（元資料の「自己PR」欄より）")
        L.append("")
        for x in tw:
            L.append(f"- {x}")
        L.append("")

    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------- 提出用ドキュメント生成
def _clean(s: str) -> str:
    """内部メモの「（要確認…）」を取り除いた表示用テキスト。空になれば ''。"""
    return re.sub(r"（要確認[^）]*）", "", s or "").strip()


def _is_personal(label: str) -> bool:
    return any(k in label for k in PERSONAL_KEYS)


def _basic_map(data: dict) -> dict:
    return {b.get("label", ""): b.get("value", "") for b in data.get("basic", [])}


def _today() -> str:
    t = date.today()
    return f"{t.year}/{t.month:02d}/{t.day:02d}"


def _skill_tables(data: dict) -> list:
    """技術スキルを分類ごとの小表に分けて返す（職務経歴書向け）。"""
    L = []
    order, groups = [], {}
    for sk in data.get("skills", []):
        cat = sk.get("category", "") or "その他"
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append(sk)
    for cat in order:
        L.append(f"### {cat}")
        L.append("")
        L.append("| 技術 | 経験年数 | 備考 |")
        L.append("| --- | --- | --- |")
        for sk in groups[cat]:
            years = sk.get("years", "") or ("利用のみ" if sk.get("usage_only") else "")
            L.append(f"| {sk.get('name','')} | {years} | {sk.get('note','')} |")
        L.append("")
    return L


def _self_pr(data: dict) -> list:
    L = ["## 自己PR", ""]
    ai = data.get("ai_activity", [])
    tw = data.get("teamwork", [])
    pd = data.get("personal_dev")
    if ai:
        L.append("### AI 活用・生産性への取り組み")
        L.append("")
        for x in ai:
            L.append(f"- {x}")
        L.append("")
    if tw:
        L.append("### チームワーク・コミュニケーション")
        L.append("")
        for x in tw:
            L.append(f"- {x}")
        L.append("")
    if pd:
        L.append("### 個人開発")
        L.append("")
        for kv in pd.get("items", []):
            L.append(_kv(kv.get("label", ""), kv.get("value", "")))
        L.append("")
    return L


def render_resume(data: dict, include_personal: bool = False) -> str:
    """profile.json から標準的な職務経歴書（文章＋案件詳細）を生成。"""
    b = _basic_map(data)
    name = _clean(b.get("氏名 / イニシャル", "")) if include_personal else ""
    L = ["# 職務経歴書", ""]
    L.append(f"作成日：{_today()}")
    L.append("")
    L.append(f"氏名：{name}")
    L.append("")

    # 職務要約
    L.append("## 職務要約")
    L.append("")
    for kv in data.get("summary", []):
        v = _clean(kv.get("value", ""))
        if v:
            L.append(v)
    L.append("")

    # 職務経歴
    L.append("## 職務経歴")
    L.append("")
    L.append("> ※ 守秘義務のため、客先名・固有のプロジェクト名は伏せ、業種・概要で記載しています。")
    L.append("")
    for p in data.get("projects", []):
        L.append(f"### {p.get('title','')}")
        L.append("")
        L.append(_kv("期間", p.get("period_text", "")))
        L.append(_kv("業種 / 案件概要", p.get("industry", "")))
        for sub in p.get("industry_sub", []):
            L.append(f"  - {sub}")
        L.append(_kv("役割", p.get("role", "")))
        L.append(_kv("チーム規模", p.get("team", "")))
        L.append(_kv("担当工程", p.get("phases", "")))
        tech = p.get("tech", [])
        if len(tech) == 1:
            L.append(_kv("使用技術", tech[0]))
        elif tech:
            L.append("- 使用技術：")
            for t in tech:
                L.append(f"  - {t}")
        L.append("- 担当業務・実績：")
        for a in p.get("achievements", []):
            L.append(f"  - {a}")
        L.append("")

    # テクニカルスキル
    L.append("## テクニカルスキル")
    L.append("")
    L.append("> 経験年数は「メイン技術として担当した案件期間」を基準に算出。"
             "「利用のみ」は構築済み環境の利用で経験年数の対象外。")
    L.append("")
    L += _skill_tables(data)

    # 担当可能工程
    L.append("## 担当可能工程")
    L.append("")
    for pr in data.get("processes", []):
        mark = "○" if pr.get("checked") else "×"
        note = pr.get("note", "")
        L.append(f"- {mark} {pr.get('name','')}{note}")
    L.append("")

    # 資格
    L.append("## 資格・認定")
    L.append("")
    for c in data.get("certs", []):
        L.append(f"- {c}")
    L.append("")

    L += _self_pr(data)
    L.append("以上")
    return "\n".join(L).rstrip() + "\n"


def render_skillsheet(data: dict, include_personal: bool = False) -> str:
    """profile.json から表中心の汎用スキルシートを生成。"""
    L = ["# スキルシート", ""]
    L.append(f"作成日：{_today()}")
    L.append("")

    # 基本情報（個人情報は既定で非表示、要確認・空欄は省略）
    L.append("## 基本情報")
    L.append("")
    for kv in data.get("basic", []):
        label = kv.get("label", "")
        if _is_personal(label) and not include_personal:
            continue
        v = _clean(kv.get("value", ""))
        if v:
            L.append(_kv(label, v))
    L.append("")

    # スキルサマリ
    sm = [v for v in (_clean(kv.get("value", "")) for kv in data.get("summary", [])) if v]
    if sm:
        L.append("## スキルサマリ")
        L.append("")
        for x in sm:
            L.append(f"- {x}")
        L.append("")

    # 技術スキル（習熟度つきフル表）
    L.append("## 技術スキル")
    L.append("")
    L.append("| 分類 | 技術 | 経験年数 | 習熟度 | 備考 |")
    L.append("| --- | --- | --- | --- | --- |")
    for sk in data.get("skills", []):
        years = sk.get("years", "") or ("利用のみ" if sk.get("usage_only") else "")
        L.append(f"| {sk.get('category','')} | {sk.get('name','')} | {years} | "
                 f"{sk.get('level','')} | {sk.get('note','')} |")
    L.append("")

    # 担当可能工程
    L.append("## 担当可能工程")
    L.append("")
    L.append("| 工程 | 対応 |")
    L.append("| --- | --- |")
    for pr in data.get("processes", []):
        L.append(f"| {pr.get('name','')} | {'○' if pr.get('checked') else '×'} |")
    L.append("")

    # 案件一覧（表）
    L.append("## 案件一覧")
    L.append("")
    L.append("| No | 期間 | 業種 / 概要 | 役割 | 規模 | 担当工程 | 主な使用技術 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for i, p in enumerate(data.get("projects", []), 1):
        tech = " / ".join(t.replace("\n", " ") for t in p.get("tech", []))
        cells = [str(i), p.get("period_text", ""), p.get("industry", ""),
                 p.get("role", ""), p.get("team", ""), p.get("phases", ""), tech]
        cells = [c.replace("|", "／") for c in cells]  # セル内の | を無害化
        L.append("| " + " | ".join(cells) + " |")
    L.append("")

    # 資格
    L.append("## 資格・認定")
    L.append("")
    for c in data.get("certs", []):
        L.append(f"- {c}")
    L.append("")
    return "\n".join(L).rstrip() + "\n"


def generate_outputs(data: dict, include_personal: bool = False) -> list:
    """職務経歴書・スキルシートを output/ に書き出す。生成したファイル名を返す。"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    outs = [
        (RESUME_MD, render_resume(data, include_personal)),
        (SKILLSHEET_MD, render_skillsheet(data, include_personal)),
    ]
    for path, content in outs:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return [os.path.basename(p) for p, _ in outs]


# ---------------------------------------------------------------- 入出力
def load_profile(path: str = PROFILE_JSON) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_profile(data: dict, json_path: str = PROFILE_JSON, md_path: str = PROFILE_MD):
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(data))


if __name__ == "__main__":
    d = load_profile()
    print(render_markdown(d))

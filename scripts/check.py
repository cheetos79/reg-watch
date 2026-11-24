#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""법령·규정 변경 점검.

sources.json의 출처를 차례로 읽어 직전 스냅샷(data/state.json)과 비교하고,
새로 생긴 항목을 data/pending.json에 쌓는다. 표준 라이브러리만 쓴다.

사용법
  python3 scripts/check.py                 평소 점검
  python3 scripts/check.py --dry-run       파일을 고치지 않고 결과만 출력
  python3 scripts/check.py --only fsc_rule 한 출처만 점검
  python3 scripts/check.py --backfill 7    처음 보는 출처도 최근 7일 항목은 신규로 취급(시험용)
"""
import argparse
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
DATE_RE = re.compile(r"(20\d\d)\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")
SEEN_CAP = 600
DETAIL_LIMIT = 15
DETAIL_CHARS = 2500

_cache = {}
_tls_fallback = set()


# ---------------------------------------------------------------- 공통 도구
def now_kst():
    return datetime.now(KST)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def to_uri(url):
    return urllib.parse.quote(url, safe=":/?&=#%+,;@()'~!*$[]")


def decode(raw):
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def http_get(url, tries=3):
    """페이지 본문을 문자열로 돌려준다. 같은 실행 안에서는 한 번만 받는다."""
    if url in _cache:
        return _cache[url]
    last = None
    for attempt in range(tries):
        for verify in (True, False):
            try:
                req = urllib.request.Request(to_uri(url), headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "ko-KR,ko;q=0.9",
                })
                ctx = None if verify else ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=30, context=ctx) as res:
                    text = decode(res.read())
                if not verify:
                    _tls_fallback.add(urllib.parse.urlsplit(url).netloc)
                _cache[url] = text
                return text
            except Exception as exc:  # noqa: BLE001
                last = exc
                is_tls = isinstance(exc, ssl.SSLError) or "CERTIFICATE" in str(exc).upper()
                if verify and is_tls:
                    continue  # 인증서 사슬이 불완전한 기관 사이트는 검증 없이 한 번 더 시도
                break
        time.sleep(2 * (attempt + 1))
    raise last


def strip_tags(fragment):
    fragment = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?is)<!--.*?-->", " ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def norm_date(text):
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))


def hidden(text, field):
    m = re.search(r'<input[^>]*\bid="%s"[^>]*\bvalue="([^"]*)"' % re.escape(field), text)
    return html.unescape(m.group(1)).strip() if m else ""


# ---------------------------------------------------------------- 출처별 읽기
def read_law(src, state):
    """법률·시행령·시행규칙: 국가법령정보센터 연혁 목록의 (공포번호, 시행일) 조합을 본다."""
    name = src["name"]
    page = "https://www.law.go.kr/법령/" + name.replace(" ", "")
    ids = state.setdefault("meta", {}).setdefault("lsId", {})
    ls_id = ids.get(src["id"])
    if not ls_id:
        ls_id = hidden(http_get(page), "lsId")
        if not ls_id:
            raise ValueError("법령 식별번호(lsId)를 찾지 못함")
        ids[src["id"]] = ls_id
    raw = http_get("https://www.law.go.kr/LSW/lsHstListR.do?lsId=" + ls_id)
    # 연혁 줄마다 붙은 lsViewLsHst2('일련번호','공포일','공포번호','시행일') 에서 개정이유 주소를 만든다
    seqs = {}
    for sm in re.finditer(r"lsViewLsHst2\('(\d+)',\s*'(\d{8})',\s*'(\d+)',\s*'(\d{8})'", raw):
        seq, _anc, no_, ef_ = sm.groups()
        seqs[(no_.lstrip("0"), "%s-%s-%s" % (ef_[:4], ef_[4:6], ef_[6:8]))] = seq
    text = strip_tags(raw)
    pat = re.compile(
        r"\[시행\s*(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})\.?\s*\]\s*"
        r"\[\s*([^\[\]\d]+?)\s*제\s*(\d+)\s*호\s*,?\s*(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})\.?\s*,\s*([^\]]+?)\s*\]")
    items = []
    for m in pat.finditer(text):
        ef, kind, no, anc, how = m.groups()
        ef_d, anc_d = norm_date(ef), norm_date(anc)
        seq = seqs.get((no.lstrip("0"), ef_d))
        items.append({
            "key": "%s|%s" % (no, ef_d),
            "title": "%s %s (%s 제%s호, 공포 %s, 시행 %s)" % (name, how, kind, no, anc_d, ef_d),
            "date": anc_d,
            "effective": ef_d,
            "url": page,
            "detail_url": "https://www.law.go.kr/LSW/lsRvsDocInfoR.do?lsiSeq=" + seq if seq else "",
            "force": True,
        })
        if len(items) >= 15:
            break
    return items


def read_admrul(src, state):
    """금융위 고시: 국가법령정보센터 행정규칙 본문의 발령번호·발령일·시행일을 본다."""
    name = src["name"]
    page = "https://www.law.go.kr/행정규칙/" + name.replace(" ", "")
    text = http_get(page)
    no, issued, ef = hidden(text, "prmlNo"), hidden(text, "prmlYd"), hidden(text, "efYd")
    seq = hidden(text, "admRulSeq")
    if not (no and issued):
        raise ValueError("행정규칙 발령 정보를 찾지 못함")

    def d(v):
        return "%s-%s-%s" % (v[:4], v[4:6], v[6:8]) if len(v) == 8 else v

    return [{
        "key": "%s|%s|%s" % (no, issued, ef),
        "title": "%s 개정 (고시 제%s호, 발령 %s, 시행 %s)" % (name, no, d(issued), d(ef)),
        "date": d(issued),
        "effective": d(ef),
        "url": page,
        "detail_url": "https://www.law.go.kr/LSW/admRulRvsInfoR.do?admRulSeq=" + seq if seq else "",
        "force": True,
    }]


def read_board(src, state):
    """게시판형 출처: 링크 규칙(link_re)에 맞는 요소를 항목으로 읽는다."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", http_get(src["url"]))
    link_re = re.compile(src["link_re"])
    title_re = re.compile(src["title_re"]) if src.get("title_re") else None
    hits = []
    for m in re.finditer(r"(?is)<(a|p)\b([^>]*)>(.*?)</\1>", text):
        attrs = html.unescape(m.group(2))
        lm = link_re.search(attrs)
        if not lm:
            continue
        key = "-".join(g for g in lm.groups() if g) or lm.group(0)
        title = strip_tags(m.group(3))
        if not title:
            tm = re.search(r'title="([^"]+)"', attrs)
            title = tm.group(1).strip() if tm else ""
        if title and (title_re is None or title_re.search(title)):
            hits.append((m, key, title, attrs))
    items, used = [], set()
    for i, (m, key, title, attrs) in enumerate(hits):
        if key in used:
            continue
        used.add(key)
        end = len(text)
        for nxt in hits[i + 1:]:
            if nxt[1] != key:
                end = nxt[0].start()
                break
        date = norm_date(strip_tags(text[m.end():min(end, m.end() + 5000)]))
        if src.get("item_url"):
            url = src["item_url"].format(*key.split("-"))
        else:
            hm = re.search(r'href="([^"]+)"', attrs)
            href = hm.group(1) if hm else ""
            url = urllib.parse.urljoin(src["url"], href) if href and not href.lower().startswith(("javascript", "#")) else src["url"]
        if src.get("key_with_date"):
            key = "%s|%s" % (key, date)
        items.append({"key": key, "title": title, "date": date, "url": url,
                      "detail_url": url if url != src["url"] else ""})
        if len(items) >= src.get("max_items", 60):
            break
    return items


READERS = {"law": read_law, "admrul": read_admrul, "board": read_board}


# ---------------------------------------------------------------- 관련성 표시
def classify(item, src, cfg):
    """제목에 관심 단어가 있으면 matched. 판단은 보고서 작성 단계에서 다시 한다."""
    if item.get("force"):
        return True, []
    title = item["title"]
    words = cfg.get("keywords", []) + src.get("keywords_extra", [])
    blocked = cfg.get("exclude", []) + src.get("exclude_extra", [])
    hints = [w for w in words if w.lower() in title.lower()]
    if any(b in title for b in blocked):
        return False, hints
    return bool(hints), hints


def fetch_detail(item):
    """항목 본문 일부를 붙여 둔다. 보고서 작성 때 원문을 다시 받지 않아도 되게 한다."""
    if not item.get("detail_url"):
        return ""
    try:
        text = strip_tags(http_get(item["detail_url"]))
    except Exception:  # noqa: BLE001
        return ""
    start = 0
    for mark in ("◇ 개정이유", "◇ 개정사유", "◇ 제정이유", item["title"][:12]):
        pos = text.find(mark)
        if pos >= 0:
            start = pos
            break
    text = text[start:]
    cuts = [text.find(m, 60) for m in ("【제정·개정문】", "콘텐츠 내용에 만족", "목록으로 이전글",
                                       "산하기관 및 관련사이트", "의견&회신", "첨부파일 목록")]
    cuts = [c for c in cuts if c > 0]
    if cuts:
        text = text[:min(cuts)]
    return text[:DETAIL_CHARS].strip()


# ---------------------------------------------------------------- 실행
def run(args):
    cfg = load_json(os.path.join(ROOT, "sources.json"), None)
    if cfg is None:
        print("sources.json 없음", file=sys.stderr)
        return 2
    state_path = os.path.join(DATA, "state.json")
    pending_path = os.path.join(DATA, "pending.json")
    status_path = os.path.join(DATA, "status.json")
    state = load_json(state_path, {"sources": {}, "meta": {}})
    pending = load_json(pending_path, {"items": []})
    status = load_json(status_path, {"runs": []})

    today = now_kst().strftime("%Y-%m-%d")
    cutoff = (now_kst() - timedelta(days=args.backfill)).strftime("%Y-%m-%d") if args.backfill else None
    pending_keys = {(p["source"], p["key"]) for p in pending["items"]}
    report, new_items = [], []

    for src in cfg["sources"]:
        if args.only and src["id"] not in args.only:
            continue
        row = {"id": src["id"], "name": src["name"], "area": src.get("area", ""), "type": src["type"],
               "ok": False, "count": 0, "new": 0, "error": ""}
        try:
            items = READERS[src["type"]](src, state)
            if not items and not src.get("allow_empty"):
                raise ValueError("항목을 하나도 읽지 못함(사이트 구조 변경 가능)")
            row["ok"], row["count"] = True, len(items)
            row["latest"] = max((it["date"] for it in items if it["date"]), default="")
            st = state["sources"].get(src["id"])
            first = st is None
            if first:
                st = {"seen": [], "since": today}
                state["sources"][src["id"]] = st
            seen = set(st["seen"])
            for it in items:
                if it["key"] in seen:
                    continue
                st["seen"].append(it["key"])
                if first and not (cutoff and it["date"] and it["date"] >= cutoff):
                    continue  # 처음 보는 출처는 기준선만 잡고 알리지 않음
                matched, hints = classify(it, src, cfg)
                entry = {"source": src["id"], "source_name": src["name"], "area": src.get("area", ""),
                         "key": it["key"], "title": it["title"], "date": it["date"],
                         "effective": it.get("effective", ""), "url": it["url"],
                         "detail_url": it.get("detail_url", ""),
                         "matched": matched, "hints": hints, "found": today}
                row["new"] += 1
                if (entry["source"], entry["key"]) not in pending_keys:
                    new_items.append(entry)
            st["seen"] = st["seen"][-SEEN_CAP:]
        except Exception as exc:  # noqa: BLE001
            row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        report.append(row)
        time.sleep(0.4)

    for entry in [e for e in new_items if e["matched"]][:DETAIL_LIMIT]:
        entry["detail"] = fetch_detail(entry)

    failed = [r for r in report if not r["ok"]]
    summary = {"date": today, "at": now_kst().strftime("%Y-%m-%d %H:%M"),
               "checked": len(report), "failed": len(failed),
               "new": len(new_items), "matched": sum(1 for e in new_items if e["matched"])}
    print(json.dumps(summary, ensure_ascii=False))
    for r in report:
        mark = "정상" if r["ok"] else "실패"
        print("  [%s] %-14s 항목 %3d 신규 %2d %s %s" % (mark, r["id"], r["count"], r["new"], r["name"], r["error"]))
    for e in new_items:
        print("  + (%s) %s | %s | %s" % ("관련" if e["matched"] else "기타", e["source_name"], e["date"], e["title"][:80]))

    if args.dry_run:
        return 0

    if not args.only:
        status["last"] = summary
        status["sources"] = report
        status["tls_unverified"] = sorted(_tls_fallback)
        status["runs"] = ([summary] + [r for r in status.get("runs", []) if r.get("at") != summary["at"]])[:60]
        save_json(status_path, status)
    pending["items"].extend(new_items)
    save_json(pending_path, pending)
    save_json(state_path, state)
    # 모든 출처가 실패하면(망 차단 등) 호출한 쪽이 알 수 있게 3으로 끝낸다
    return 3 if report and len(failed) == len(report) else 0


def main():
    ap = argparse.ArgumentParser(description="법령·규정 변경 점검")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--backfill", type=int, default=0)
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()

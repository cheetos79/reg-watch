#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""일일 보고(OPR) 파일을 검사하고 웹 페이지 목록에 반영한다.

사용법
  python3 scripts/publish.py reports/2026-11-24.json   보고서 검사 후 반영, 처리한 대기 항목 비움
  python3 scripts/publish.py --clear-only              보고할 것이 없을 때 대기 항목만 비움
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
KST = timezone(timedelta(hours=9))
DATE = re.compile(r"^20\d\d-\d\d-\d\d$")
BANNED = re.compile(r"[■▪▶○●※◆□•▷◇★☆✔✅❌⚠]|[\U0001F300-\U0001FAFF]")
AREAS = {"공시", "회계", "IR·지배구조", "세무"}
LIMITS = {"name": 60, "stage": 14, "what": 110, "impact": 110, "action": 70}
MAX_ITEMS = 6


def load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def validate(rep):
    errs = []
    if not DATE.match(str(rep.get("date", ""))):
        errs.append("date는 YYYY-MM-DD 형식이어야 함")
    summary = rep.get("summary")
    if not (isinstance(summary, list) and 1 <= len(summary) <= 3 and all(isinstance(s, str) and s.strip() for s in summary)):
        errs.append("summary는 1~3개 문장의 배열이어야 함")
    items = rep.get("items")
    if not (isinstance(items, list) and 1 <= len(items) <= MAX_ITEMS):
        errs.append("items는 1~%d건이어야 함(넘치면 덜 중요한 건을 more에 한 줄로 적음)" % MAX_ITEMS)
        items = []
    for i, it in enumerate(items, 1):
        tag = "items[%d]" % i
        if it.get("grade") not in ("A", "B"):
            errs.append(tag + " grade는 A 또는 B")
        if it.get("area") not in AREAS:
            errs.append(tag + " area는 %s 중 하나" % "/".join(sorted(AREAS)))
        for field, limit in LIMITS.items():
            val = it.get(field, "")
            if not isinstance(val, str) or not val.strip():
                errs.append("%s %s 비어 있음" % (tag, field))
            elif len(val) > limit:
                errs.append("%s %s가 %d자를 넘음(%d자)" % (tag, field, limit, len(val)))
        if not DATE.match(str(it.get("date", ""))):
            errs.append(tag + " date 형식 오류")
        if it.get("effective") and not DATE.match(it["effective"]):
            errs.append(tag + " effective는 YYYY-MM-DD 또는 빈 값")
        if not str(it.get("url", "")).startswith("https://"):
            errs.append(tag + " url은 https 주소여야 함")
        if not isinstance(it.get("source", ""), str) or not it.get("source"):
            errs.append(tag + " source 비어 있음")
    for j, sc in enumerate(rep.get("schedule", []), 1):
        if not DATE.match(str(sc.get("date", ""))) or not str(sc.get("text", "")).strip():
            errs.append("schedule[%d]는 date와 text가 필요함" % j)
    if len(rep.get("schedule", [])) > 5:
        errs.append("schedule은 5건 이하")
    blob = json.dumps(rep, ensure_ascii=False)
    if BANNED.search(blob):
        errs.append("장식 기호나 이모지가 들어 있음")
    return errs


def clear_pending(upto):
    path = os.path.join(DATA, "pending.json")
    pending = load(path, {"items": []})
    before = len(pending["items"])
    pending["items"] = [p for p in pending["items"] if p.get("found", "") > upto]
    save(path, pending)
    return before - len(pending["items"])


def main():
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if len(sys.argv) == 2 and sys.argv[1] == "--clear-only":
        print("대기 항목 %d건 비움" % clear_pending(today))
        return 0
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    rep = load(path, None)
    if rep is None:
        print("파일 없음: " + path)
        return 2
    errs = validate(rep)
    if errs:
        print("보고서 검사 실패")
        for e in errs:
            print("  " + e)
        return 1
    expect = os.path.join("reports", rep["date"] + ".json").replace("\\", "/")
    if os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/") != expect:
        print("파일 위치는 %s 이어야 함" % expect)
        return 1

    idx_path = os.path.join(DATA, "changes.json")
    idx = load(idx_path, {"days": []})
    day = {
        "date": rep["date"],
        "count": len(rep["items"]) + int(rep.get("more_count", 0) or 0),
        "a": sum(1 for it in rep["items"] if it["grade"] == "A"),
        "b": sum(1 for it in rep["items"] if it["grade"] == "B"),
        "summary": rep["summary"][0],
        "areas": sorted({it["area"] for it in rep["items"]}),
    }
    idx["days"] = sorted([d for d in idx["days"] if d["date"] != rep["date"]] + [day],
                         key=lambda d: d["date"], reverse=True)
    idx["updated"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    save(idx_path, idx)
    cleared = clear_pending(max(today, rep["date"]))
    print("반영 완료: %s, 항목 %d건(A %d, B %d), 대기 항목 %d건 비움" % (rep["date"], day["count"], day["a"], day["b"], cleared))
    return 0


if __name__ == "__main__":
    sys.exit(main())

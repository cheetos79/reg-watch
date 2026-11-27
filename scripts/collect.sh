#!/usr/bin/env bash
# 수집 실행기.
#
# 1) 이 환경에서 직접 수집한다(scripts/check.py).
# 2) 사이트 접속이 막혀 실패한 출처가 있으면 collect-request.txt를 올려 GitHub 서버(Actions)에
#    수집을 맡기고, 결과가 올라올 때까지 기다렸다가 받아 온다.
#
# 종료 코드  0 수집 완료, 3 어느 쪽으로도 수집하지 못함
# 환경 변수  WAIT_SECONDS  GitHub 서버 수집을 기다리는 시간(초, 기본 420)
#            FORCE_REMOTE=1 직접 수집을 건너뛰고 맡기기만 함(시험용)
set -u
cd "$(dirname "$0")/.."

WAIT_SECONDS=${WAIT_SECONDS:-420}

# 마지막 정상 수집의 "실패 수 / 출처 수 / 시각"
last_run() {
  python3 -c '
import json
try:
    last = json.load(open("data/status.json", encoding="utf-8")).get("last", {})
except Exception:
    last = {}
print(last.get("failed", ""), last.get("checked", ""), last.get("at", ""))
'
}

now_kst() {
  python3 -c '
import sys
from datetime import datetime, timedelta, timezone
print(datetime.now(timezone(timedelta(hours=9))).strftime(sys.argv[1]))
' "$1"
}

direct=3
if [ "${FORCE_REMOTE:-0}" != "1" ]; then
  python3 scripts/check.py
  direct=$?
fi

if [ "$direct" -eq 0 ]; then
  read -r failed checked at <<<"$(last_run)"
  if [ "${failed:-1}" = "0" ]; then
    echo "수집 완료: 직접 수집, 출처 ${checked}곳 정상"
    exit 0
  fi
  echo "직접 수집에서 ${failed}곳 실패. GitHub 서버에 수집을 맡김"
else
  echo "이 환경에서는 수집할 수 없음. GitHub 서버에 수집을 맡김"
fi

# 직접 수집이 일부라도 됐으면 그 결과로 진행할 수 있으므로 아래에서 실패해도 0으로 끝낸다
give_up() {
  echo "$1"
  if [ "$direct" -eq 0 ]; then exit 0; fi
  exit 3
}

stamp=$(now_kst '%Y-%m-%d %H:%M:%S')
echo "$stamp" > collect-request.txt
git add -A data collect-request.txt
git commit -qm "수집 요청 $stamp" || give_up "수집 요청을 기록하지 못함"
if ! { git pull -q --rebase origin main && git push -q origin HEAD:main; }; then
  give_up "수집 요청을 올리지 못함"
fi
base=$(git rev-parse HEAD)
echo "수집 요청을 올림($stamp). 결과를 기다림(최대 ${WAIT_SECONDS}초)"

deadline=$(( $(date +%s) + WAIT_SECONDS ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 15
  git fetch -q origin main || continue
  # GitHub 서버의 수집 커밋 제목은 "수집 2026-..." 이다("수집 요청 ..."과 구분)
  if git log --format=%s "$base..origin/main" | grep -q '^수집 20'; then
    git pull -q --rebase origin main
    read -r failed checked at <<<"$(last_run)"
    if [ -n "${checked:-}" ] && [ "${failed:-$checked}" != "$checked" ] && [ ! "$at" \< "${stamp:0:16}" ]; then
      echo "수집 완료: GitHub 서버 수집($at), 출처 ${checked}곳 중 실패 ${failed}곳"
      exit 0
    fi
    give_up "GitHub 서버에서도 수집하지 못함"
  fi
done
give_up "GitHub 서버 수집 결과를 ${WAIT_SECONDS}초 안에 받지 못함"

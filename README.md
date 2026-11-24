# 법령·규정 변경 점검

공시·회계·IR 관련 법령과 규정의 변경을 평일 오전에 점검하고, 변경이 있는 날 한 장 보고서(OPR)를 웹 페이지에 올린다. 모든 자료는 공개된 정부·기관 사이트에서 가져온다.

## 동작 순서

| 시각(한국) | 주체 | 하는 일 |
|---|---|---|
| 평일 07:40 | GitHub Actions `daily-check` | `scripts/check.py`로 출처를 읽어 직전 스냅샷과 비교, 새 항목을 `data/pending.json`에 저장 |
| 평일 08:30 | 예약 작업(Claude) | `ROUTINE.md` 절차대로 새 항목의 관련성을 판단하고 `reports/날짜.json` 작성, `scripts/publish.py`로 검사·반영 |
| 수시 | GitHub Pages | `index.html`이 `data/`와 `reports/`를 읽어 화면에 표시 |

## 파일

| 경로 | 내용 |
|---|---|
| `sources.json` | 점검 출처, 관심 단어, 제외 단어 |
| `scripts/check.py` | 수집과 비교(파이썬 표준 라이브러리만 사용) |
| `scripts/publish.py` | 보고서 형식 검사, 변경 이력 반영, 대기 항목 비우기 |
| `ROUTINE.md` | 예약 작업이 따르는 절차와 보고서 작성 규칙 |
| `data/state.json` | 출처별로 이미 본 항목 |
| `data/pending.json` | 아직 보고서로 정리하지 않은 새 항목 |
| `data/status.json` | 마지막 점검 결과와 출처별 상태 |
| `data/changes.json` | 보고서 목록 |
| `reports/` | 날짜별 보고서 |

## 출처를 더하거나 고칠 때

`sources.json`의 `sources`에 한 줄을 더한다.

- 법률·시행령: `{"id": "...", "type": "law", "area": "공시", "name": "법령 정식 명칭"}`
- 금융위 고시 등 행정규칙: `"type": "admrul"`
- 게시판: `"type": "board"`, 목록 주소 `url`, 글 링크에서 번호를 뽑는 정규식 `link_re`, 글 주소 틀 `item_url`

고친 뒤 `python3 scripts/check.py --dry-run --only 새id`로 읽히는지 확인한다. 처음 보는 출처는 기준선만 잡고 알리지 않는다.

## 한계

- 국회에 계류 중인 의원 발의 법안은 점검하지 않는다. 법률은 공포된 시점에 잡힌다.
- 첨부 파일(hwp, pdf) 안의 내용은 읽지 않는다. 본문에 요지가 없으면 보고서에 원문 확인이 필요하다고 적는다.
- 자동으로 정리한 문서이므로 적용 전에 원문을 확인해야 한다.

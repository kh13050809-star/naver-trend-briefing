"""Collect daily relative search interest. Uses only Python's standard library."""
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
ENDPOINT = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
DOCS = "https://api.ncloud-docs.com/docs/naver-api-hub-search-trend"


def request_body(groups, today):
    if not isinstance(groups, list) or not 1 <= len(groups) <= 5:
        raise ValueError("Expected 1 to 5 keyword groups")
    names = set()
    for group in groups:
        name = group.get("groupName")
        keywords = group.get("keywords")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Group names must be unique and nonempty")
        names.add(name)
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 20:
            raise ValueError("Expected 1 to 20 keywords per group")
        if any(not isinstance(k, str) or not k.strip() for k in keywords):
            raise ValueError("Keywords must be nonempty strings")
    end = today - timedelta(days=1)
    return {
        "startDate": end.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "date",
        "keywordGroups": groups,
    }


def validate_response(raw, body):
    if not isinstance(raw, dict):
        raise ValueError("Response must be an object")
    for field in ("startDate", "endDate", "timeUnit"):
        if raw.get(field) != body[field]:
            raise ValueError("Response request window mismatch")
    results = raw.get("results")
    if not isinstance(results, list) or len(results) != len(body["keywordGroups"]):
        raise ValueError("Missing result groups")
    expected = {g["groupName"]: g["keywords"] for g in body["keywordGroups"]}
    seen = set()
    for group in results:
        title = group.get("title")
        if title not in expected or title in seen or group.get("keywords") != expected[title]:
            raise ValueError("Unexpected result group")
        seen.add(title)
        points = group.get("data")
        if not isinstance(points, list) or not points:
            raise ValueError("Empty daily series")
        previous = ""
        for point in points:
            period, ratio = point.get("period"), point.get("ratio")
            date.fromisoformat(period)
            if not body["startDate"] <= period <= body["endDate"] or period <= previous:
                raise ValueError("Invalid or unordered period")
            if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
                raise ValueError("Invalid ratio type")
            if not math.isfinite(ratio) or not 0 <= ratio <= 100:
                raise ValueError("Ratio outside 0 to 100")
            previous = period
    # Whitelist output fields; never publish arbitrary error bodies or headers.
    return [
        {"title": g["title"], "keywords": g["keywords"],
         "data": [{"period": p["period"], "ratio": p["ratio"]} for p in g["data"]]}
        for g in results
    ]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    now = datetime.now(KST)
    output = ROOT / "public"
    previous = json.loads((output / "latest.json").read_text(encoding="utf-8"))
    status = {
        "schema_version": 1, "attempted_at": now.isoformat(),
        "last_success_at": previous.get("collected_at"), "status": "error",
    }
    client_id = os.environ.get("NAVER_API_CLIENT_ID", "").strip()
    secret = os.environ.get("NAVER_API_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        status.update(status="waiting_for_secrets", error_code="missing_credentials")
        write_json(output / "status.json", status)
        print("NAVER API HUB secrets are missing. Existing data preserved.")
        return 1
    try:
        groups = json.loads((ROOT / "keywords.json").read_text(encoding="utf-8"))
        body = request_body(groups, now.date())
        request = Request(
            ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"X-NCP-APIGW-API-KEY-ID": client_id,
                     "X-NCP-APIGW-API-KEY": secret,
                     "Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=45) as response:
            raw = json.load(response)
        results = validate_response(raw, body)
        through = min(g["data"][-1]["period"] for g in results)
        # Collection success does not imply that the provider's daily series is current.
        state = "ok" if through == body["endDate"] else "source_lag"
        latest = {
            "schema_version": 1, "status": state, "collected_at": now.isoformat(),
            "source": "NAVER API HUB Search Trend", "source_docs": DOCS,
            "timezone": "Asia/Seoul", "collection_interval_hours": 2,
            "data_granularity": "day", "data_through": through,
            "interpretation": "지정한 주제어 묶음의 일별 상대 검색 관심도. 실시간 인기검색어 순위나 절대 검색량이 아닙니다. 서로 다른 요청 기간의 ratio를 직접 비교하지 마세요.",
            "request": body, "results": results,
        }
        write_json(output / "latest.json", latest)
        status.update(status=state, last_success_at=now.isoformat(), data_through=through)
    except HTTPError as error:
        status["error_code"] = f"http_{error.code}"
    except (URLError, TimeoutError, OSError):
        status["error_code"] = "network_error"
    except (ValueError, KeyError, TypeError, AttributeError):
        status["error_code"] = "invalid_payload"
    write_json(output / "status.json", status)
    print("Collection state:", status["status"], status.get("error_code", ""))
    return 0 if status["status"] in ("ok", "source_lag") else 1


if __name__ == "__main__":
    raise SystemExit(main())

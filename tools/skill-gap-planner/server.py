#!/usr/bin/env python3
"""求人票から学習課題を作る、ローカル専用スキルギャップ管理GUI。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import subprocess
import ctypes
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA_FILE = ROOT / "skill-gap-data.json"
PROFILE_FILE = ROOT / "profile.json"
PORT = int(os.environ.get("SKILL_GAP_PORT", "8766"))
OPENAI_URL = "https://api.openai.com/v1/responses"
MAX_BODY = 1_000_000
STATE_LOCK = threading.RLock()
KEYCHAIN_SERVICE = "local.skill-gap-planner.openai"


class StateConflictError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_state() -> dict:
    return {"version": 1, "revision": 0, "jobs": [], "skills": [], "sales_leads": []}


def validate_state(data: dict) -> None:
    if (not isinstance(data, dict) or not isinstance(data.get("jobs"), list)
            or not isinstance(data.get("skills"), list) or not isinstance(data.get("sales_leads"), list)):
        raise ValueError("jobs、skills、sales_leads は配列で指定してください")
    for job in data["jobs"]:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not isinstance(job.get("raw_text"), str):
            raise ValueError("求人データの形式が不正です")
    for skill in data["skills"]:
        if (not isinstance(skill, dict) or not isinstance(skill.get("id"), str)
                or not isinstance(skill.get("name"), str) or skill.get("status") not in {"todo", "learning", "acquired"}):
            raise ValueError("スキルデータの形式が不正です")
    lead_statuses = {"target", "contacted", "meeting", "proposal", "negotiation", "won", "lost"}
    for lead in data["sales_leads"]:
        if (not isinstance(lead, dict) or not isinstance(lead.get("id"), str)
                or not isinstance(lead.get("company"), str) or lead.get("status") not in lead_statuses
                or not isinstance(lead.get("contract_checks", {}), dict)):
            raise ValueError("営業候補データの形式が不正です")


def load_state() -> dict:
    with STATE_LOCK:
        if not DATA_FILE.exists():
            return default_state()
        with DATA_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("保存データの形式が不正です")
        data.setdefault("version", 1)
        data.setdefault("revision", 0)
        data.setdefault("jobs", [])
        data.setdefault("skills", [])
        data.setdefault("sales_leads", [])
        validate_state(data)
        return data


def save_state(data: dict, expected_revision: Optional[int] = None) -> int:
    validate_state(data)
    with STATE_LOCK:
        current_revision = load_state().get("revision", 0) if DATA_FILE.exists() else 0
        if expected_revision is not None and expected_revision != current_revision:
            raise StateConflictError("別の画面でデータが更新されました。最新状態を再読み込みしてください。")
        saved = json.loads(json.dumps(data, ensure_ascii=False))
        saved["version"] = 1
        saved["revision"] = current_revision + 1
        ROOT.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".skill-gap-", suffix=".json", dir=ROOT)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(saved, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_name, DATA_FILE)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise
        return saved["revision"]


def profile_skills() -> list[dict]:
    if not PROFILE_FILE.exists():
        return []
    try:
        with PROFILE_FILE.open(encoding="utf-8") as f:
            profile = json.load(f)
        return [
            {"name": x.get("name", ""), "level": x.get("level", ""), "years": x.get("years", "")}
            for x in profile.get("skills", []) if x.get("name")
        ]
    except (OSError, ValueError, TypeError):
        return []


def get_openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def save_openai_api_key(key: str) -> None:
    key = key.strip()
    if len(key) < 20 or not key.startswith("sk-"):
        raise ValueError("OpenAI APIキーの形式を確認してください")
    service = KEYCHAIN_SERVICE.encode("utf-8")
    account = os.environ.get("USER", "local-user").encode("utf-8")
    password = key.encode("utf-8")
    security = None
    item_ref = ctypes.c_void_p()
    try:
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        security.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
            ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ]
        security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        security.SecKeychainItemModifyAttributesAndData.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
        ]
        security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        security.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
            ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        status = security.SecKeychainFindGenericPassword(
            None, len(service), service, len(account), account,
            None, None, ctypes.byref(item_ref),
        )
        if status == 0:
            status = security.SecKeychainItemModifyAttributesAndData(
                item_ref, None, len(password), ctypes.c_char_p(password),
            )
        elif status == -25300:  # errSecItemNotFound
            status = security.SecKeychainAddGenericPassword(
                None, len(service), service, len(account), account,
                len(password), ctypes.c_char_p(password), None,
            )
        if status != 0:
            raise RuntimeError(f"macOSキーチェーンへ保存できません（OSStatus {status}）")
    except OSError as e:
        raise RuntimeError(f"macOSキーチェーンを利用できません: {e}") from e
    finally:
        if security is not None and item_ref.value:
            core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            core.CFRelease.argtypes = [ctypes.c_void_p]
            core.CFRelease.restype = None
            core.CFRelease(item_ref)


def extract_output_text(response: dict) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    if not chunks:
        raise ValueError("AI応答にテキストが含まれていません")
    return "".join(chunks)


def call_openai(instructions: str, input_text: str, schema_name: str, schema: dict,
                tools: Optional[list] = None, model: Optional[str] = None,
                include: Optional[list] = None, return_raw: bool = False,
                tool_choice: str = "auto"):
    api_key = get_openai_api_key()
    if not api_key:
        raise RuntimeError("OpenAI APIキーが未設定です。画面上部の「AIキー未設定」から設定してください。")
    payload = {
        "model": model or os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        "store": False,
        "instructions": instructions,
        "input": input_text,
        "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    if include:
        payload["include"] = include
    req = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            body = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"OpenAI API エラー ({e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"OpenAI API に接続できません: {e.reason}") from e
    parsed = json.loads(extract_output_text(body))
    return (parsed, body) if return_raw else parsed


def extract_web_source_urls(response: dict) -> set[str]:
    """Web Searchツール結果とURL引用から、実際に参照されたURLだけを得る。"""
    urls = set()

    def collect(value):
        if isinstance(value, dict):
            url = value.get("url")
            if isinstance(url, str):
                parsed = urlparse(url)
                if parsed.scheme in {"http", "https"} and parsed.netloc:
                    urls.add(url)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for item in response.get("output", []):
        if item.get("type") == "web_search_call":
            collect(item)
        elif item.get("type") == "message":
            for content in item.get("content", []):
                collect(content.get("annotations", []))
    return urls


def canonical_job_url(url: str):
    """同一求人URLの安全な表記揺れだけを吸収する比較キー。"""
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        port = parsed.port
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)
        host = parsed.hostname.lower() if default_port or port is None else f"{parsed.hostname.lower()}:{port}"
        path = parsed.path.rstrip("/") or "/"
        query = tuple(sorted(
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if not k.lower().startswith("utm_")
        ))
        return parsed.scheme.lower(), host, path, query
    except ValueError:
        return None


ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "skills"],
    "properties": {
        "summary": {"type": "string"},
        "skills": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "category", "importance", "current_status", "reason", "evidence", "priority", "learning_goal", "recommended_actions", "project", "estimated_hours"],
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "importance": {"type": "string", "enum": ["必須", "歓迎", "周辺"]},
                    "current_status": {"type": "string", "enum": ["未習得", "要確認", "習得済み"]},
                    "reason": {"type": "string"},
                    "evidence": {"type": "string"},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 3},
                    "learning_goal": {"type": "string"},
                    "recommended_actions": {"type": "array", "items": {"type": "string"}},
                    "project": {
                        "type": "object", "additionalProperties": False,
                        "required": ["title", "description", "deliverables", "done_when"],
                        "properties": {
                            "title": {"type": "string"}, "description": {"type": "string"},
                            "deliverables": {"type": "array", "items": {"type": "string"}},
                            "done_when": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "estimated_hours": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            },
        },
    },
}

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reply", "plan"],
    "properties": {
        "reply": {"type": "string"},
        "plan": {
            "type": "object", "additionalProperties": False,
            "required": ["goal", "resources", "project", "estimated_hours", "next_action"],
            "properties": {
                "goal": {"type": "string"},
                "resources": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["type", "title", "url", "reason"],
                    "properties": {"type": {"type": "string"}, "title": {"type": "string"}, "url": {"type": "string"}, "reason": {"type": "string"}},
                }},
                "project": {
                    "type": "object", "additionalProperties": False,
                    "required": ["title", "description", "deliverables", "done_when"],
                    "properties": {
                        "title": {"type": "string"}, "description": {"type": "string"},
                        "deliverables": {"type": "array", "items": {"type": "string"}},
                        "done_when": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "estimated_hours": {"type": "integer", "minimum": 1, "maximum": 500},
                "next_action": {"type": "string"},
            },
        },
    },
}

JOB_SEARCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["search_summary", "jobs"],
    "properties": {
        "search_summary": {"type": "string"},
        "jobs": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "company", "location", "work_style", "employment_type", "salary", "url", "source", "snippet", "requirements", "nice_to_have", "published_at", "matched_conditions"],
            "properties": {
                "title": {"type": "string"}, "company": {"type": "string"},
                "location": {"type": "string"}, "work_style": {"type": "string"},
                "employment_type": {"type": "string"}, "salary": {"type": "string"},
                "url": {"type": "string"}, "source": {"type": "string"},
                "snippet": {"type": "string"}, "published_at": {"type": "string"},
                "requirements": {"type": "array", "items": {"type": "string"}},
                "nice_to_have": {"type": "array", "items": {"type": "string"}},
                "matched_conditions": {"type": "array", "items": {"type": "string"}},
            },
        }},
    },
}


def analyze_job(payload: dict) -> dict:
    text = str(payload.get("text", "")).strip()
    if len(text) < 30:
        raise ValueError("求人文を30文字以上貼り付けてください")
    known = profile_skills()
    existing = payload.get("existing_skills", [])
    context = json.dumps({"求人文": text, "プロフィール登録済みスキル": known, "管理中スキル": existing}, ensure_ascii=False)
    instructions = """あなたは日本のソフトウェアエンジニア向け学習コーチです。求人文から、応募に必要な知識・技術・実務能力を重複なく具体的なスキルへ分解してください。求人文の明記と推測を区別し、evidenceには根拠となる短い記述を入れます。プロフィールに同名または明確に同義の実務スキルがある場合だけ習得済み、それ以外は要確認または未習得とします。各未習得項目には、単なる視聴で終わらず成果物で確認できる小さな制作課題と客観的な完了条件を提案してください。架空のURLは作らず、確実なURLが不明なら空文字にします。日本語で簡潔に答えてください。"""
    return call_openai(instructions, context, "job_skill_analysis", ANALYSIS_SCHEMA)


def refine_plan(payload: dict) -> dict:
    skill = payload.get("skill")
    message = str(payload.get("message", "")).strip()
    if not isinstance(skill, dict) or not message:
        raise ValueError("対象スキルと相談内容が必要です")
    skill_without_chat = {k: v for k, v in skill.items() if k != "chat"}
    context = json.dumps({"対象スキル": skill_without_chat, "直近の会話": payload.get("history", [])[-10:], "本人の相談": message}, ensure_ascii=False)
    instructions = """あなたは実務志向のエンジニア学習コーチです。本人との会話を受け、既存計画を現実的に更新してください。動画・公式ドキュメント・演習・制作物を目的に応じて選び、視聴だけを完了条件にしません。制作課題は求人で求められる能力を証明できる最小規模にし、deliverablesとdone_whenを観測可能にします。実在を確信できない教材名やURLは捏造せず、URLは空文字にします。本人への返答と更新後の計画を日本語で返してください。"""
    return call_openai(instructions, context, "learning_plan", PLAN_SCHEMA)


def search_jobs(payload: dict) -> dict:
    keyword = str(payload.get("keyword", "")).strip()
    if len(keyword) < 2 or len(keyword) > 100:
        raise ValueError("検索キーワードは2〜100文字で入力してください")
    raw_areas = payload.get("areas", [])
    if not isinstance(raw_areas, list):
        raise ValueError("検索地域の形式が不正です")
    areas = [str(x).strip() for x in raw_areas if str(x).strip()][:10]
    contract = str(payload.get("contract", "指定なし")).strip()[:50]
    context = json.dumps({
        "検索日": datetime.now().date().isoformat(),
        "技術キーワード": keyword,
        "地域・働き方（いずれかに一致）": areas or ["指定なし"],
        "契約形態": contract or "指定なし",
        "最大件数": 12,
    }, ensure_ascii=False)
    instructions = """あなたは日本のITエンジニア求人検索アシスタントです。Web検索を必ず使い、現在アクセスできる個別求人・個別案件ページだけを最大12件返してください。検索条件の地域・働き方はいずれかに一致すればよく、matched_conditionsに一致条件を明記します。同じ求人の転載は1件にまとめ、可能なら企業公式の採用ページを優先します。個別ページで確認できた必須要件はrequirements、歓迎要件はnice_to_haveへ原文の意味を変えずに要約します。検索結果に存在しない会社名、募集条件、給与、要件、URLを作ってはいけません。不明項目は空文字または空配列にします。求人一覧ページや検索結果ページしか見つからない場合は個別求人として返しません。日本語で簡潔に記述し、掲載が現在も有効かは利用者が元ページで最終確認できるようにします。"""
    result, raw_response = call_openai(
        instructions, context, "job_search_results", JOB_SEARCH_SCHEMA,
        tools=[{"type": "web_search"}],
        model=os.environ.get("OPENAI_SEARCH_MODEL", "gpt-5.5"),
        include=["web_search_call.action.sources"], return_raw=True,
        tool_choice="required",
    )
    source_urls = {canonical_job_url(url) for url in extract_web_source_urls(raw_response)}
    source_urls.discard(None)
    jobs = []
    for job in result.get("jobs", [])[:12]:
        if canonical_job_url(job.get("url", "")) in source_urls:
            jobs.append(job)
    result["jobs"] = jobs
    result["search_summary"] = f"{result.get('search_summary', '').strip()}（検証済み {len(jobs)}件）".strip()
    result["searched_at"] = datetime.now().date().isoformat()
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_bytes(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code: int, value: dict) -> None:
        self.send_bytes(code, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError("リクエストサイズが不正です")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSONオブジェクトを指定してください")
        return value

    def request_is_local(self) -> bool:
        """DNS rebinding / 別サイトからのlocalhost書き換えを拒否する。"""
        allowed = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin", "")
        return host in allowed and (not origin or origin in {f"http://{x}" for x in allowed})

    def do_GET(self):
        if not self.request_is_local():
            self.send_json(403, {"error": "ローカル画面からのみ利用できます"})
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.send_bytes(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self.send_json(200, {"state": load_state(), "profile_skill_count": len(profile_skills()), "ai_ready": bool(get_openai_api_key())})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.request_is_local():
            self.send_json(403, {"error": "ローカル画面からのみ利用できます"})
            return
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self.send_json(415, {"error": "Content-Type は application/json を指定してください"})
            return
        try:
            payload = self.read_json()
            if self.path == "/api/state":
                revision = payload.get("revision")
                if not isinstance(revision, int):
                    raise ValueError("revision が必要です")
                new_revision = save_state(payload, expected_revision=revision)
                result = {"ok": True, "saved_at": now_iso(), "revision": new_revision}
            elif self.path == "/api/analyze":
                result = analyze_job(payload)
            elif self.path == "/api/chat":
                result = refine_plan(payload)
            elif self.path == "/api/job-search":
                result = search_jobs(payload)
            elif self.path == "/api/settings/openai-key":
                save_openai_api_key(str(payload.get("api_key", "")))
                result = {"ok": True}
            else:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, result)
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json(400, {"error": str(e)})
        except StateConflictError as e:
            self.send_json(409, {"error": str(e)})
        except RuntimeError as e:
            self.send_json(502, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": f"処理に失敗しました: {e}"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Skill Gap Planner を起動しました → {url}")
    print("終了するには Ctrl+C")
    if os.environ.get("NO_BROWSER") != "1":
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

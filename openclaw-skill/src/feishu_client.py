from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.models import HistoryActionItem, QueueJob, SearchResult


class LarkCLIError(RuntimeError):
    pass


class LarkCLIClient:
    """Triple-backend client for Feishu API access.

    Priority:
        1. User access token (OpenClaw injected) — best identity, full data access
        2. lark-cli binary (local dev) — full features, interactive auth
        3. Tenant access token (app credentials) — headless fallback, limited scope
    """

    def __init__(self, executable: str = "lark-cli") -> None:
        self._base_url = "https://open.feishu.cn/open-apis"

        # Priority 1: OpenClaw user access token (env or decrypt script)
        user_token = os.environ.get("LARK_USER_ACCESS_TOKEN")
        if not user_token:
            user_token = self._try_decrypt_openclaw_token()
            if user_token:
                os.environ["LARK_USER_ACCESS_TOKEN"] = user_token
        if user_token:
            self.mode = "user_token"
            self.user_token = user_token
            return

        # Priority 2: lark-cli binary
        self.executable = self._resolve_executable(executable)
        if self.executable:
            self.mode = "cli"
            return

        # Priority 3: tenant access token (app credentials)
        self.app_id = os.environ.get("LARK_APP_ID") or os.environ.get("FS_APP_ID")
        self.app_secret = os.environ.get("LARK_APP_SECRET") or os.environ.get("FS_APP_SECRET")
        if self.app_id and self.app_secret:
            self.mode = "tenant_token"
            self._token: str | None = None
            self._token_expires: float = 0.0
            return

        raise LarkCLIError(
            "No Feishu auth method available. "
            "Please set one of: LARK_USER_ACCESS_TOKEN (OpenClaw), "
            "lark-cli binary (local dev), or LARK_APP_ID + LARK_APP_SECRET (fallback)."
        )

    @staticmethod
    def _try_decrypt_openclaw_token() -> str | None:
        """Attempt to run OpenClaw's decrypt_token.js to obtain user_access_token."""
        script_path = Path("scripts/decrypt_token.js")
        if not script_path.exists():
            return None
        try:
            completed = subprocess.run(
                ["node", str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            if completed.returncode != 0:
                return None
            stdout = completed.stdout.strip()
            if not stdout:
                return None
            # If output looks like JSON, try to extract token
            if stdout.startswith("{"):
                try:
                    parsed = json.loads(stdout)
                    token = parsed.get("accessToken") or parsed.get("access_token") or parsed.get("token")
                    if isinstance(token, str) and token.strip():
                        return token.strip()
                except json.JSONDecodeError:
                    pass
            # Otherwise treat raw stdout as token if it looks reasonable
            if len(stdout) >= 20 and " " not in stdout:
                return stdout
        except Exception:
            pass
        return None

    @property
    def http_mode(self) -> bool:
        return self.mode in ("user_token", "tenant_token")

    @staticmethod
    def _resolve_executable(executable: str) -> str | None:
        candidates = (
            executable,
            f"{executable}.exe",
            "lark-cli.exe",
            "lark-cli.cmd",
            "lark-cli.ps1",
        )
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return None

    def _run_json(self, args: list[str]) -> dict[str, Any]:
        # Some environments (e.g. OpenClaw) default to table output instead of JSON.
        # Try --format json first; if unsupported, fall back to plain args.
        candidates = ([*args, "--format", "json"], args)
        last_exc: Exception | None = None
        for candidate in candidates:
            command = [self.executable, *candidate]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    env=_build_cli_env(),
                )
            except OSError as exc:
                raise LarkCLIError(str(exc)) from exc

            stdout = completed.stdout or ""
            stderr = completed.stderr or ""

            if completed.returncode != 0:
                err_lower = stderr.lower()
                if candidate != args and any(k in err_lower for k in ("unknown flag", "format", "flag")):
                    continue
                error_text = stderr.strip() or stdout.strip()
                if error_text:
                    try:
                        parsed = json.loads(error_text)
                    except json.JSONDecodeError:
                        raise LarkCLIError(error_text) from None
                    raise LarkCLIError(self._format_cli_error(parsed))
                raise LarkCLIError(f"command failed: {' '.join(command)}")

            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                last_exc = LarkCLIError("lark-cli did not return valid JSON output")
                if candidate != args:
                    continue
        if last_exc:
            raise last_exc
        raise LarkCLIError("lark-cli did not return valid JSON output")

    @staticmethod
    def _unwrap_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise LarkCLIError("unexpected non-object JSON payload from lark-cli")
        if payload.get("ok") is False:
            error = payload.get("error") or {}
            message = error.get("message") or str(payload)
            hint = error.get("hint")
            if hint:
                raise LarkCLIError(f"{message}. {hint}")
            raise LarkCLIError(message)
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload

    # ------------------------------------------------------------------
    # HTTP fallback helpers
    # ------------------------------------------------------------------

    def _ensure_token(self) -> str:
        if self.mode == "user_token":
            return self.user_token
        if self.mode == "tenant_token":
            now = time.time()
            if self._token and now < self._token_expires - 60:
                return self._token
            payload = self._http_request(
                "POST",
                "/auth/v3/tenant_access_token/internal",
                body={"app_id": self.app_id, "app_secret": self.app_secret},
                auth=False,
            )
            token = payload.get("tenant_access_token")
            if not isinstance(token, str):
                raise LarkCLIError("unable to obtain tenant_access_token")
            self._token = token
            self._token_expires = now + payload.get("expire", 7200)
            return token
        raise LarkCLIError("_ensure_token called in CLI mode")

    def _http_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        url = self._base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self._ensure_token()}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                raise LarkCLIError(f"HTTP {exc.code}: {raw[:500]}") from exc
            code = parsed.get("code", 0)
            msg = parsed.get("msg", "unknown error")
            raise LarkCLIError(f"Feishu API error {code}: {msg}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LarkCLIError("Feishu API did not return valid JSON") from exc
        code = parsed.get("code", 0)
        if code != 0:
            raise LarkCLIError(f"Feishu API error {code}: {parsed.get('msg', 'unknown')}")
        return parsed.get("data", {})

    @staticmethod
    def _format_cli_error(payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        error = payload.get("error") or {}
        message = error.get("message") or payload.get("message") or str(payload)
        hint = error.get("hint")
        if hint:
            return f"{message}. {hint}"
        return message

    @staticmethod
    def _first_string(payload: Any, keys: tuple[str, ...]) -> str | None:
        if isinstance(payload, dict):
            for key in keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            for value in payload.values():
                found = LarkCLIClient._first_string(value, keys)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = LarkCLIClient._first_string(item, keys)
                if found:
                    return found
        return None

    @staticmethod
    def _first_dict_with_keys(payload: Any, required_keys: tuple[str, ...]) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            if all(key in payload for key in required_keys):
                return payload
            for value in payload.values():
                found = LarkCLIClient._first_dict_with_keys(value, required_keys)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = LarkCLIClient._first_dict_with_keys(item, required_keys)
                if found:
                    return found
        return None

    @staticmethod
    def _iter_dicts(payload: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        stack = [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                results.append(current)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
        return results

    def get_document_content(self, document_id: str) -> dict[str, Any]:
        if self.http_mode:
            meta = self._http_request("GET", f"/docx/v1/documents/{document_id}")
            title = meta.get("document", {}).get("title", "")
            raw = self._http_request("GET", f"/docx/v1/documents/{document_id}/raw_content")
            content = raw.get("content", "")
            if not content:
                raise LarkCLIError("unable to fetch document content via HTTP API")
            return {
                "title": title or _extract_title_from_content(content) or document_id,
                "content": content,
                "url": f"https://jcneyh7qlo8i.feishu.cn/docx/{document_id}",
            }
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "docs",
                    "+fetch",
                    "--as",
                    "user",
                    "--api-version",
                    "v2",
                    "--doc",
                    document_id,
                ]
            )
        )
        document = self._first_dict_with_keys(payload, ("content",)) or payload
        content = self._first_string(document, ("content", "markdown", "text"))
        if not content:
            raise LarkCLIError("unable to extract document content from docs +fetch output")
        title = self._first_string(document, ("title", "document_title", "name")) or _extract_title_from_content(content) or document_id
        url = self._first_string(document, ("url", "document_url", "link"))
        return {"title": title, "content": content, "url": url}

    def find_base_by_name(self, name: str) -> dict[str, Any] | None:
        if self.http_mode:
            try:
                payload = self._http_request("GET", "/bitable/v1/apps", query={"page_size": "50"})
            except LarkCLIError as exc:
                msg = str(exc).lower()
                if any(k in msg for k in ("400", "404", "not support", "not found")):
                    print(f"[warn] Base listing not available in current auth mode: {exc}")
                    return None
                raise
            items = (payload.get("items") or []) if isinstance(payload, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                app_token = item.get("app_token")
                item_name = item.get("name")
                if item_name == name and isinstance(app_token, str):
                    return {"app_token": app_token, "url": item.get("url") or f"https://jcneyh7qlo8i.feishu.cn/base/{app_token}"}
            return None
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "docs",
                    "+search",
                    "--as",
                    "user",
                    "--query",
                    name,
                ]
            )
        )
        for item in self._iter_dicts(payload):
            title = item.get("title")
            docs_type = item.get("docs_type") or item.get("type")
            docs_token = item.get("docs_token") or item.get("token")
            if title == name and docs_type == "bitable" and isinstance(docs_token, str):
                return {
                    "app_token": docs_token,
                    "url": item.get("url") or f"https://jcneyh7qlo8i.feishu.cn/base/{docs_token}",
                }
        return None

    def search_documents(self, query: str, page_size: int = 10) -> list[SearchResult]:
        if self.http_mode:
            try:
                payload = self._http_request(
                    "POST",
                    "/suite/docs-api/search/object",
                    body={"search_key": query, "count": min(page_size, 50), "offset": 0},
                )
            except LarkCLIError as exc:
                # Some auth modes (e.g. user token) may not support this search endpoint
                message = str(exc).lower()
                if any(k in message for k in ("400", "404", "not support", "not found")):
                    print(f"[warn] Document search not available in current auth mode: {exc}")
                    return []
                raise
            results: list[SearchResult] = []
            docs_list = payload.get("docs_entities", []) if isinstance(payload, dict) else []
            for item in docs_list:
                if not isinstance(item, dict):
                    continue
                title = item.get("title")
                docs_type = item.get("docs_type")
                token = item.get("docs_token")
                if not isinstance(title, str) or not isinstance(docs_type, str) or not isinstance(token, str):
                    continue
                if docs_type not in {"docx", "bitable", "sheet", "wiki"}:
                    continue
                url = item.get("url") or _build_docs_url(docs_type, token)
                results.append(SearchResult(token=token, title=title, docs_type=docs_type, url=url))
            return results
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "docs",
                    "+search",
                    "--as",
                    "user",
                    "--query",
                    query,
                    "--page-size",
                    str(page_size),
                ]
            )
        )
        results: list[SearchResult] = []
        for item in self._iter_dicts(payload):
            title = item.get("title")
            docs_type = item.get("docs_type") or item.get("type")
            token = item.get("docs_token") or item.get("token")
            if not isinstance(title, str) or not isinstance(docs_type, str) or not isinstance(token, str):
                continue
            if docs_type not in {"docx", "bitable", "sheet", "wiki"}:
                continue
            url = item.get("url") or _build_docs_url(docs_type, token)
            results.append(SearchResult(token=token, title=title, docs_type=docs_type, url=url))
        deduped: list[SearchResult] = []
        seen: set[tuple[str, str]] = set()
        for result in results:
            key = (result.docs_type, result.token)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped

    def get_base(self, app_token: str) -> dict[str, Any]:
        if self.http_mode:
            payload = self._http_request("GET", f"/bitable/v1/apps/{app_token}")
            app = payload.get("app", {}) if isinstance(payload, dict) else {}
            url = app.get("url") or f"https://jcneyh7qlo8i.feishu.cn/base/{app_token}"
            return {"app_token": app_token, "url": url}
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+base-get",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                ]
            )
        )
        url = self._first_string(payload, ("url", "link")) or f"https://jcneyh7qlo8i.feishu.cn/base/{app_token}"
        return {"app_token": app_token, "url": url}

    def create_base(self, name: str) -> dict[str, Any]:
        if self.http_mode:
            try:
                payload = self._http_request("POST", "/bitable/v1/apps", body={"name": name, "time_zone": "Asia/Shanghai"})
            except LarkCLIError as exc:
                msg = str(exc).lower()
                if any(k in msg for k in ("400", "404", "not support", "not found")):
                    raise LarkCLIError(
                        f"Auto-creating Base is not supported with the current auth token ({exc}). "
                        "Please pre-create a Base in Feishu and pass its token via --base-token."
                    ) from exc
                raise
            app = payload.get("app", {}) if isinstance(payload, dict) else {}
            app_token = app.get("app_token")
            if not isinstance(app_token, str):
                raise LarkCLIError("unable to extract app_token from create_base HTTP response")
            return {"app_token": app_token, "url": app.get("url") or f"https://jcneyh7qlo8i.feishu.cn/base/{app_token}"}
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+base-create",
                    "--as",
                    "user",
                    "--name",
                    name,
                    "--time-zone",
                    "Asia/Shanghai",
                ]
            )
        )
        base_info = self._first_dict_with_keys(payload, ("base_token",)) or self._first_dict_with_keys(payload, ("app_token",))
        if not base_info:
            raise LarkCLIError("unable to extract base token from base +base-create output")
        return {
            "app_token": base_info.get("base_token") or base_info["app_token"],
            "url": base_info.get("url")
            or self._first_string(payload, ("url", "link"))
            or f"https://jcneyh7qlo8i.feishu.cn/base/{base_info.get('base_token') or base_info['app_token']}",
        }

    def ensure_table(self, app_token: str, table_name: str) -> dict[str, Any]:
        existing_table = self._find_table(app_token, table_name)
        if existing_table:
            self._ensure_fields(app_token, existing_table["table_id"])
            return existing_table

        fields = [
            {"field_name": "任务", "type": 1},
            {"field_name": "负责人", "type": 1},
            {"field_name": "截止时间", "type": 5},
            {"field_name": "截止说明", "type": 1},
            {"field_name": "来源会议", "type": 1},
            {"field_name": "背景知识", "type": 1},
            {"field_name": "相关链接", "type": 1},
            {
                "field_name": "状态",
                "type": 3,
                "property": {
                    "options": [
                        {"name": "待开始", "color": 0},
                        {"name": "进行中", "color": 1},
                        {"name": "已完成", "color": 2},
                        {"name": "需确认", "color": 3},
                    ]
                },
            },
        ]
        if self.http_mode:
            payload = self._http_request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables",
                body={"table": {"name": table_name, "fields": fields}},
            )
            table_id = payload.get("table_id") or payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(table_id, str):
                raise LarkCLIError("unable to extract table id from HTTP table-create response")
            self._ensure_fields(app_token, table_id)
            return {"table_id": table_id}

        # lark-cli path (uses friendly field definitions)
        cli_fields = [
            {"name": "任务", "type": "text"},
            {"name": "负责人", "type": "text"},
            {"name": "截止时间", "type": "datetime"},
            {"name": "截止说明", "type": "text"},
            {"name": "来源会议", "type": "text"},
            {"name": "背景知识", "type": "text"},
            {"name": "相关链接", "type": "text"},
            {
                "name": "状态",
                "type": "select",
                "options": [
                    {"name": "待开始"},
                    {"name": "进行中"},
                    {"name": "已完成"},
                    {"name": "需确认"},
                ],
            },
        ]
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+table-create",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                    "--name",
                    table_name,
                    "--fields",
                    json.dumps(cli_fields, ensure_ascii=False),
                ]
            )
        )
        table_info = payload.get("table") if isinstance(payload.get("table"), dict) else None
        if not table_info:
            table_info = self._first_dict_with_keys(payload, ("table_id",))
        if not table_info:
            raise LarkCLIError("unable to extract table id from base +table-create output")
        table_id = table_info.get("table_id") or table_info["id"]
        self._ensure_fields(app_token, table_id)
        return {"table_id": table_id}

    def _find_table(self, app_token: str, table_name: str) -> dict[str, Any] | None:
        if self.http_mode:
            payload = self._http_request("GET", f"/bitable/v1/apps/{app_token}/tables")
            items = (payload.get("items") or []) if isinstance(payload, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                tid = item.get("table_id") or item.get("id")
                if item.get("name") == table_name and isinstance(tid, str):
                    return {"table_id": tid}
            return None
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+table-list",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                ]
            )
        )
        for item in self._iter_dicts(payload):
            table_id = item.get("table_id") or item.get("id")
            if item.get("name") == table_name and isinstance(table_id, str):
                return {"table_id": table_id}
        return None

    def ensure_queue_table(self, app_token: str, table_name: str) -> dict[str, Any]:
        existing_table = self._find_table(app_token, table_name)
        if existing_table:
            self._ensure_queue_fields(app_token, existing_table["table_id"])
            return existing_table

        fields = [
            {"field_name": "文档链接", "type": 1},
            {"field_name": "文档ID", "type": 1},
            {"field_name": "会议主题", "type": 1},
            {
                "field_name": "处理状态",
                "type": 3,
                "property": {
                    "options": [
                        {"name": "待处理", "color": 0},
                        {"name": "处理中", "color": 1},
                        {"name": "已完成", "color": 2},
                        {"name": "失败", "color": 3},
                    ]
                },
            },
            {"field_name": "上次处理时间", "type": 1},
            {"field_name": "结果Base链接", "type": 1},
            {"field_name": "结果分发稿链接", "type": 1},
            {"field_name": "备注", "type": 1},
        ]
        if self.http_mode:
            payload = self._http_request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables",
                body={"table": {"name": table_name, "fields": fields}},
            )
            table_id = payload.get("table_id") or payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(table_id, str):
                raise LarkCLIError("unable to extract queue table id from HTTP table-create response")
            self._ensure_queue_fields(app_token, table_id)
            return {"table_id": table_id}

        cli_fields = [
            {"name": "文档链接", "type": "text"},
            {"name": "文档ID", "type": "text"},
            {"name": "会议主题", "type": "text"},
            {
                "name": "处理状态",
                "type": "select",
                "options": [
                    {"name": "待处理"},
                    {"name": "处理中"},
                    {"name": "已完成"},
                    {"name": "失败"},
                ],
            },
            {"name": "上次处理时间", "type": "text"},
            {"name": "结果Base链接", "type": "text"},
            {"name": "结果分发稿链接", "type": "text"},
            {"name": "备注", "type": "text"},
        ]
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+table-create",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                    "--name",
                    table_name,
                    "--fields",
                    json.dumps(cli_fields, ensure_ascii=False),
                ]
            )
        )
        table_info = payload.get("table") if isinstance(payload.get("table"), dict) else None
        if not table_info:
            table_info = self._first_dict_with_keys(payload, ("table_id",))
        if not table_info:
            raise LarkCLIError("unable to extract queue table id from base +table-create output")
        table_id = table_info.get("table_id") or table_info["id"]
        self._ensure_queue_fields(app_token, table_id)
        return {"table_id": table_id}

    def _ensure_fields(self, app_token: str, table_id: str) -> None:
        if self.http_mode:
            required_fields = {
                "任务": {"field_name": "任务", "type": 1},
                "负责人": {"field_name": "负责人", "type": 1},
                "截止时间": {"field_name": "截止时间", "type": 5},
                "截止说明": {"field_name": "截止说明", "type": 1},
                "来源会议": {"field_name": "来源会议", "type": 1},
                "背景知识": {"field_name": "背景知识", "type": 1},
                "相关链接": {"field_name": "相关链接", "type": 1},
                "状态": {
                    "field_name": "状态",
                    "type": 3,
                    "property": {
                        "options": [
                            {"name": "待开始", "color": 0},
                            {"name": "进行中", "color": 1},
                            {"name": "已完成", "color": 2},
                            {"name": "需确认", "color": 3},
                        ]
                    },
                },
            }
            payload = self._http_request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields")
            items = (payload.get("items") or []) if isinstance(payload, dict) else []
            existing_names = {item.get("field_name") for item in items if isinstance(item, dict)}
            for name, field in required_fields.items():
                if name in existing_names:
                    continue
                self._http_request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", body=field)
            return

        required_fields = {
            "任务": {"name": "任务", "type": "text"},
            "负责人": {"name": "负责人", "type": "text"},
            "截止时间": {"name": "截止时间", "type": "datetime"},
            "截止说明": {"name": "截止说明", "type": "text"},
            "来源会议": {"name": "来源会议", "type": "text"},
            "背景知识": {"name": "背景知识", "type": "text"},
            "相关链接": {"name": "相关链接", "type": "text"},
            "状态": {
                "name": "状态",
                "type": "select",
                "options": [{"name": "待开始"}, {"name": "进行中"}, {"name": "已完成"}, {"name": "需确认"}],
            },
        }
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+field-list",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                    "--table-id",
                    table_id,
                ]
            )
        )
        existing_names = {
            item.get("name") or item.get("field_name")
            for item in self._iter_dicts(payload)
            if isinstance(item, dict)
        }
        for name, field in required_fields.items():
            if name in existing_names:
                continue
            self._unwrap_payload(
                self._run_json(
                    [
                        "base",
                        "+field-create",
                        "--as",
                        "user",
                        "--base-token",
                        app_token,
                        "--table-id",
                        table_id,
                        "--json",
                        json.dumps(field, ensure_ascii=False),
                    ]
                )
            )

    def _ensure_queue_fields(self, app_token: str, table_id: str) -> None:
        if self.http_mode:
            required_fields = {
                "文档链接": {"field_name": "文档链接", "type": 1},
                "文档ID": {"field_name": "文档ID", "type": 1},
                "会议主题": {"field_name": "会议主题", "type": 1},
                "处理状态": {
                    "field_name": "处理状态",
                    "type": 3,
                    "property": {
                        "options": [
                            {"name": "待处理", "color": 0},
                            {"name": "处理中", "color": 1},
                            {"name": "已完成", "color": 2},
                            {"name": "失败", "color": 3},
                        ]
                    },
                },
                "上次处理时间": {"field_name": "上次处理时间", "type": 1},
                "结果Base链接": {"field_name": "结果Base链接", "type": 1},
                "结果分发稿链接": {"field_name": "结果分发稿链接", "type": 1},
                "备注": {"field_name": "备注", "type": 1},
            }
            payload = self._http_request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields")
            items = (payload.get("items") or []) if isinstance(payload, dict) else []
            existing_names = {item.get("field_name") for item in items if isinstance(item, dict)}
            for name, field in required_fields.items():
                if name in existing_names:
                    continue
                self._http_request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", body=field)
            return

        required_fields = {
            "文档链接": {"name": "文档链接", "type": "text"},
            "文档ID": {"name": "文档ID", "type": "text"},
            "会议主题": {"name": "会议主题", "type": "text"},
            "处理状态": {
                "name": "处理状态",
                "type": "select",
                "options": [{"name": "待处理"}, {"name": "处理中"}, {"name": "已完成"}, {"name": "失败"}],
            },
            "上次处理时间": {"name": "上次处理时间", "type": "text"},
            "结果Base链接": {"name": "结果Base链接", "type": "text"},
            "结果分发稿链接": {"name": "结果分发稿链接", "type": "text"},
            "备注": {"name": "备注", "type": "text"},
        }
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+field-list",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                    "--table-id",
                    table_id,
                ]
            )
        )
        existing_names = {
            item.get("name") or item.get("field_name")
            for item in self._iter_dicts(payload)
            if isinstance(item, dict)
        }
        for name, field in required_fields.items():
            if name in existing_names:
                continue
            self._unwrap_payload(
                self._run_json(
                    [
                        "base",
                        "+field-create",
                        "--as",
                        "user",
                        "--base-token",
                        app_token,
                        "--table-id",
                        table_id,
                        "--json",
                        json.dumps(field, ensure_ascii=False),
                    ]
                )
            )

    def list_queue_jobs(self, app_token: str, table_id: str) -> list[QueueJob]:
        fields = [
            "文档链接",
            "文档ID",
            "会议主题",
            "处理状态",
            "上次处理时间",
            "结果Base链接",
            "结果分发稿链接",
            "备注",
        ]
        rows = self.list_table_rows(app_token, table_id, fields)
        jobs: list[QueueJob] = []
        for row in rows:
            jobs.append(
                QueueJob(
                    record_id=row["record_id"],
                    document_url=row.get("文档链接", ""),
                    docx_id=row.get("文档ID", ""),
                    meeting_topic=row.get("会议主题", ""),
                    status=row.get("处理状态", "") or "待处理",
                    last_processed_time=row.get("上次处理时间", ""),
                    result_base_url=row.get("结果Base链接", ""),
                    result_distribution_url=row.get("结果分发稿链接", ""),
                    note=row.get("备注", ""),
                )
            )
        return jobs

    def list_pending_queue_jobs(self, app_token: str, table_id: str) -> list[QueueJob]:
        jobs = self.list_queue_jobs(app_token, table_id)
        return [job for job in jobs if job.status in ("", "待处理", "失败")]

    def mark_queue_job_processing(self, app_token: str, table_id: str, record_id: str) -> None:
        self._update_record_with_retry(
            app_token,
            table_id,
            record_id,
            {
                "处理状态": "处理中",
                "上次处理时间": _now_text(),
                "备注": "",
            },
        )

    def mark_queue_job_done(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        result_base_url: str,
        result_distribution_url: str,
    ) -> None:
        self._update_record_with_retry(
            app_token,
            table_id,
            record_id,
            {
                "处理状态": "已完成",
                "上次处理时间": _now_text(),
                "结果Base链接": result_base_url,
                "结果分发稿链接": result_distribution_url,
                "备注": "",
            },
        )

    def mark_queue_job_failed(self, app_token: str, table_id: str, record_id: str, note: str) -> None:
        self._update_record_with_retry(
            app_token,
            table_id,
            record_id,
            {
                "处理状态": "失败",
                "上次处理时间": _now_text(),
                "备注": note[:500],
            },
        )

    def _update_record_with_retry(self, app_token: str, table_id: str, record_id: str, fields: dict[str, Any]) -> None:
        delays = (0.0, 1.0, 2.0)
        last_error: Exception | None = None
        for delay in delays:
            if delay:
                time.sleep(delay)
            try:
                self.update_record(app_token, table_id, record_id, fields)
                return
            except LarkCLIError as exc:
                last_error = exc
                message = str(exc).lower()
                if "5000" not in message and "limited" not in message:
                    raise
        if last_error:
            raise last_error

    def list_table_rows(self, app_token: str, table_id: str, field_names: list[str], limit: int = 500) -> list[dict[str, str]]:
        if self.http_mode:
            result: list[dict[str, str]] = []
            page_token = ""
            while True:
                query: dict[str, str] = {"page_size": str(min(limit, 500))}
                if page_token:
                    query["page_token"] = page_token
                payload = self._http_request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", query=query)
                items = (payload.get("items") or []) if isinstance(payload, dict) else []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    record_id = item.get("record_id")
                    fields_data = item.get("fields", {})
                    if not isinstance(record_id, str) or not isinstance(fields_data, dict):
                        continue
                    parsed: dict[str, str] = {"record_id": record_id}
                    for name in field_names:
                        parsed[name] = _extract_task_field(fields_data.get(name))
                    result.append(parsed)
                has_more = payload.get("has_more") if isinstance(payload, dict) else False
                page_token = payload.get("page_token", "") if isinstance(payload, dict) else ""
                if not has_more or not page_token:
                    break
            return result

        offset = 0
        result: list[dict[str, str]] = []
        while True:
            payload = self._unwrap_payload(
                self._run_json(
                    [
                        "base",
                        "+record-list",
                        "--as",
                        "user",
                        "--base-token",
                        app_token,
                        "--table-id",
                        table_id,
                        *sum((["--field-id", name] for name in field_names), []),
                        "--limit",
                        str(limit),
                        "--offset",
                        str(offset),
                    ]
                )
            )
            record_ids = payload.get("record_id_list")
            rows = payload.get("data")
            fields = payload.get("fields")
            if not isinstance(record_ids, list) or not isinstance(rows, list) or not isinstance(fields, list):
                break
            for record_id, row in zip(record_ids, rows):
                if not isinstance(record_id, str) or not isinstance(row, list):
                    continue
                parsed = {"record_id": record_id}
                for name, value in zip(fields, row):
                    parsed[str(name)] = _extract_task_field(value)
                result.append(parsed)
            has_more = payload.get("has_more")
            if not has_more:
                break
            offset += limit
        return result

    def search_action_history(self, app_token: str, table_id: str, query: str, limit: int = 5) -> list[HistoryActionItem]:
        rows = self.list_table_rows(
            app_token,
            table_id,
            ["任务", "负责人", "截止说明", "状态", "来源会议", "背景知识"],
        )
        keywords = [part for part in re.split(r"[\s：:，,]+", query) if part]
        scored: list[tuple[int, HistoryActionItem]] = []
        for row in rows:
            task = row.get("任务", "")
            source_meeting = row.get("来源会议", "")
            background = row.get("背景知识", "")
            haystack = " ".join(
                [
                    task,
                    source_meeting,
                    background,
                ]
            )
            score = 0
            for keyword in keywords:
                if not keyword:
                    continue
                if keyword in task:
                    score += 3
                if keyword in source_meeting:
                    score += 2
                if keyword in background:
                    score += 1
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    HistoryActionItem(
                        task=task,
                        owner=row.get("负责人", ""),
                        due_date=row.get("截止说明", ""),
                        status=row.get("状态", ""),
                        source_meeting=source_meeting,
                        background=background,
                    ),
                )
            )
        scored.sort(
            key=lambda item: (
                -item[0],
                0 if item[1].status == "进行中" else 1,
                0 if item[1].due_date else 1,
                item[1].task,
            )
        )
        deduped: list[HistoryActionItem] = []
        seen: set[tuple[str, str]] = set()
        for _, item in scored:
            key = (_normalize_task(item.task), _normalize_task(item.source_meeting))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped

    def create_record(self, app_token: str, table_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        if self.http_mode:
            return self._http_request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                body={"fields": fields},
            )
        field_names = list(fields.keys())
        row = [fields[name] for name in field_names]
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+record-batch-create",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                    "--table-id",
                    table_id,
                    "--json",
                    json.dumps(
                        {
                            "fields": field_names,
                            "rows": [row],
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
        )
        return payload

    def find_record_id_by_task(self, app_token: str, table_id: str, task: str) -> str | None:
        normalized_task = _normalize_task(task)
        if self.http_mode:
            return self._find_record_id_by_task_scan(app_token, table_id, task)
        if len(task) > 50:
            return self._find_record_id_by_task_scan(app_token, table_id, task)
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+record-search",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                    "--table-id",
                    table_id,
                    "--json",
                    json.dumps(
                        {
                            "keyword": task,
                            "search_fields": ["任务"],
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
        )
        record_ids = payload.get("record_id_list")
        rows = payload.get("data")
        if isinstance(record_ids, list) and isinstance(rows, list):
            for record_id, row in zip(record_ids, rows):
                if not isinstance(record_id, str):
                    continue
                task_value = _extract_task_field(row[0] if isinstance(row, list) and row else row)
                if _normalize_task(task_value) == normalized_task:
                    return record_id
        for item in self._iter_dicts(payload):
            record_id = item.get("record_id") or item.get("id")
            if not isinstance(record_id, str):
                continue
            fields_data = item.get("fields")
            if not isinstance(fields_data, dict):
                continue
            task_value = _extract_task_field(fields_data.get("任务"))
            if _normalize_task(task_value) == normalized_task:
                return record_id
        return None

    def _find_record_id_by_task_scan(self, app_token: str, table_id: str, task: str) -> str | None:
        normalized_task = _normalize_task(task)
        if self.http_mode:
            payload = self._http_request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                query={"page_size": "500", "field_names": '["任务"]'},
            )
            items = (payload.get("items") or []) if isinstance(payload, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                record_id = item.get("record_id")
                fields_data = item.get("fields", {})
                if not isinstance(record_id, str) or not isinstance(fields_data, dict):
                    continue
                task_value = _extract_task_field(fields_data.get("任务"))
                if _normalize_task(task_value) == normalized_task:
                    return record_id
            return None

        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+record-list",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                    "--table-id",
                    table_id,
                    "--field-id",
                    "任务",
                    "--limit",
                    "500",
                ]
            )
        )
        record_ids = payload.get("record_id_list")
        rows = payload.get("data")
        if isinstance(record_ids, list) and isinstance(rows, list):
            for record_id, row in zip(record_ids, rows):
                if not isinstance(record_id, str):
                    continue
                task_value = _extract_task_field(row[0] if isinstance(row, list) and row else row)
                if _normalize_task(task_value) == normalized_task:
                    return record_id
        for item in self._iter_dicts(payload):
            record_id = item.get("record_id") or item.get("id")
            if not isinstance(record_id, str):
                continue
            fields_data = item.get("fields")
            if not isinstance(fields_data, dict):
                continue
            task_value = _extract_task_field(fields_data.get("任务"))
            if _normalize_task(task_value) == normalized_task:
                return record_id
        return None

    def update_record(self, app_token: str, table_id: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        if self.http_mode:
            return self._http_request(
                "PUT",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
                body={"fields": fields},
            )
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "base",
                    "+record-batch-update",
                    "--as",
                    "user",
                    "--base-token",
                    app_token,
                    "--table-id",
                    table_id,
                    "--json",
                    json.dumps(
                        {
                            "record_id_list": [record_id],
                            "patch": fields,
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
        )
        return payload

    def import_markdown(self, file_name: str, markdown: str) -> dict[str, Any]:
        if self.http_mode:
            return self._http_import_markdown(file_name, markdown)
        tmp_dir = Path.cwd() / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = tmp_dir / f"distribution-{uuid4().hex}.md"
        temp_path.write_text(markdown, encoding="utf-8")
        try:
            payload = self._unwrap_payload(
                self._run_json(
                    [
                        "drive",
                        "+import",
                        "--as",
                        "user",
                        "--file",
                        str(Path(".") / "tmp" / temp_path.name),
                        "--type",
                        "docx",
                        "--name",
                        _sanitize_import_name(file_name),
                    ]
                )
            )
        finally:
            temp_path.unlink(missing_ok=True)

        url = self._first_string(payload, ("url", "document_url", "link"))
        if not url:
            token = self._first_string(payload, ("token", "document_id", "obj_token"))
            if token:
                url = f"https://jcneyh7qlo8i.feishu.cn/docx/{token}"
        if not url:
            raise LarkCLIError("unable to extract document url from drive +import output")
        return {"url": url}

    def _http_import_markdown(self, file_name: str, markdown: str) -> dict[str, Any]:
        """HTTP fallback: create an empty docx and write markdown as plain-text blocks."""
        create_resp = self._http_request(
            "POST",
            "/docx/v1/documents",
            body={"title": _sanitize_import_name(file_name)},
        )
        document_data = create_resp.get("document", {}) if isinstance(create_resp, dict) else {}
        document_id = document_data.get("document_id")
        if not isinstance(document_id, str):
            raise LarkCLIError("unable to create docx via HTTP API")
        url = create_resp.get("url") or f"https://jcneyh7qlo8i.feishu.cn/docx/{document_id}"

        # Write content as paragraph blocks
        # Batch in chunks to avoid overly large requests
        lines = markdown.splitlines()
        buffer: list[str] = []

        def _flush() -> None:
            if not buffer:
                return
            text = "\n".join(buffer)
            self._http_request(
                "POST",
                f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                body={
                    "children": [
                        {
                            "block_type": 2,
                            "text": {
                                "elements": [{"text_run": {"content": text[:4000]}}],
                                "style": {},
                            },
                        }
                    ]
                },
            )
            buffer.clear()

        for line in lines:
            buffer.append(line)
            if len(buffer) >= 20:
                _flush()
        _flush()
        return {"url": url}

    # ------------------------------------------------------------------
    # Messaging (IM)
    # ------------------------------------------------------------------

    def send_text_message(self, receive_id: str, text: str, receive_type: str = "open_id") -> dict[str, Any]:
        """Send a plain-text message to a user or chat.

        receive_type: open_id | user_id | union_id | email | chat_id
        """
        target_id = receive_id
        # Feishu global union_id starts with "ou_"; message API requires app-scoped open_id.
        if self.http_mode and receive_type == "open_id" and receive_id.startswith("ou_"):
            try:
                payload = self._http_request(
                    "POST",
                    "/contact/v3/users/batch_get_id",
                    body={"union_ids": [receive_id]},
                )
                user_list = payload.get("user_list", []) if isinstance(payload, dict) else []
                if user_list and isinstance(user_list[0], dict):
                    target_id = user_list[0].get("open_id") or receive_id
            except LarkCLIError:
                pass  # fallback to original id
        if self.http_mode:
            return self._http_request(
                "POST",
                "/im/v1/messages",
                query={"receive_id_type": receive_type},
                body={
                    "receive_id": target_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "im",
                    "+messages-send",
                    "--as",
                    "user",
                    "--receive-id",
                    receive_id,
                    "--receive-id-type",
                    receive_type,
                    "--msg-type",
                    "text",
                    "--content",
                    json.dumps({"text": text}, ensure_ascii=False),
                ]
            )
        )
        return payload

    def send_markdown_message(self, receive_id: str, markdown: str, receive_type: str = "open_id") -> dict[str, Any]:
        """Send a markdown message to a user or chat."""
        if self.http_mode:
            return self._http_request(
                "POST",
                "/im/v1/messages",
                query={"receive_id_type": receive_type},
                body={
                    "receive_id": receive_id,
                    "msg_type": "interactive",
                    "content": json.dumps(
                        {
                            "schema": "2.0",
                            "body": {"elements": [{"tag": "markdown", "content": markdown}]},
                        },
                        ensure_ascii=False,
                    ),
                },
            )
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "im",
                    "+messages-send",
                    "--as",
                    "user",
                    "--receive-id",
                    receive_id,
                    "--receive-id-type",
                    receive_type,
                    "--msg-type",
                    "interactive",
                    "--content",
                    json.dumps(
                        {
                            "schema": "2.0",
                            "body": {"elements": [{"tag": "markdown", "content": markdown}]},
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
        )
        return payload

    def search_user(self, query: str) -> list[dict[str, Any]]:
        """Search users by name, email, or phone."""
        if self.http_mode:
            payload = self._http_request(
                "POST",
                "/contact/v3/users/batch_get_id",
                body={"emails": [query], "include_resigned": False},
            )
            users: list[dict[str, Any]] = []
            user_list = payload.get("user_list", []) if isinstance(payload, dict) else []
            for item in user_list:
                if not isinstance(item, dict):
                    continue
                user_id = item.get("user_id")
                if user_id:
                    users.append({"open_id": user_id, "name": query, "email": query})
            if not users:
                # Fallback: try department user list (best-effort)
                pass
            return users
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "contact",
                    "+search-user",
                    "--as",
                    "user",
                    "--query",
                    query,
                ]
            )
        )
        users: list[dict[str, Any]] = []
        for item in self._iter_dicts(payload):
            open_id = item.get("open_id") or item.get("open_id")
            name = item.get("name") or item.get("user_name")
            email = item.get("email") or item.get("enterprise_email")
            if open_id and name:
                users.append({"open_id": open_id, "name": name, "email": email})
        return users

    def search_chat(self, query: str) -> list[dict[str, Any]]:
        """Search group chats by keyword."""
        if self.http_mode:
            payload = self._http_request("GET", "/im/v1/chats", query={"page_size": "50"})
            chats: list[dict[str, Any]] = []
            items = (payload.get("items") or []) if isinstance(payload, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                chat_id = item.get("chat_id")
                name = item.get("name")
                if chat_id and name:
                    chats.append({"chat_id": chat_id, "name": name})
            return chats
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "im",
                    "+chat-search",
                    "--as",
                    "user",
                    "--query",
                    query,
                ]
            )
        )
        chats: list[dict[str, Any]] = []
        for item in self._iter_dicts(payload):
            chat_id = item.get("chat_id") or item.get("id")
            name = item.get("name") or item.get("chat_name")
            if chat_id and name:
                chats.append({"chat_id": chat_id, "name": name})
        return chats

    # ------------------------------------------------------------------
    # Calendar & proactive scanning helpers
    # ------------------------------------------------------------------

    def list_calendar_events(self, start_iso: str, end_iso: str, page_size: int = 50) -> list[dict[str, Any]]:
        """List events from the primary calendar in a time range (ISO 8601)."""
        if self.http_mode:
            payload = self._http_request(
                "GET",
                "/calendar/v4/calendars/primary/events",
                query={
                    "start_time_min": start_iso,
                    "start_time_max": end_iso,
                    "page_size": str(page_size),
                },
            )
            return payload.get("items", []) if isinstance(payload, dict) else []
        payload = self._unwrap_payload(
            self._run_json(
                [
                    "calendar",
                    "+agenda",
                    "--start",
                    start_iso,
                    "--end",
                    end_iso,
                ]
            )
        )
        items: list[dict[str, Any]] = []
        for item in self._iter_dicts(payload):
            if isinstance(item, dict) and item.get("event_id"):
                items.append(item)
        return items

    def search_meeting_minutes(self, query: str = "纪要", page_size: int = 10) -> list[SearchResult]:
        """Search docs that look like meeting minutes."""
        return self.search_documents(query, page_size=page_size)


def _extract_title_from_content(content: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", content, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return html.unescape(match.group(1)).strip() or None


def _build_docs_url(docs_type: str, token: str) -> str:
    host = "jcneyh7qlo8i.feishu.cn"
    if docs_type == "docx":
        return f"https://{host}/docx/{token}"
    if docs_type == "bitable":
        return f"https://{host}/base/{token}"
    if docs_type == "sheet":
        return f"https://{host}/sheets/{token}"
    if docs_type == "wiki":
        return f"https://{host}/wiki/{token}"
    return ""


def _build_cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("LARK_CLI_NO_PROXY", "1")
    return env


def _extract_task_field(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def _normalize_task(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _sanitize_import_name(file_name: str) -> str:
    name = file_name[:-3] if file_name.endswith(".md") else file_name
    name = re.sub(r'[\\/:*?"<>|]+', "-", name).strip()
    return name or "行动项分发稿"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

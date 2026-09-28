"""按页面分叉探索 Android App，并保存可恢复的路径和受阻项。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
from uuid import uuid4

from adbutils import AdbClient
from cryptography.fernet import Fernet

from artemis.clients.ui_automator_client import UIAutomatorClient
from artemis.config import TRACES_PATH
from artemis.drivers.android.adb_driver import AndroidAdbDriver
from artemis.runtime.adb_endpoint import current_adb_endpoint
from artemis.runtime.device_lock import DeviceBusyError, DeviceExecutionLock


_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
_RISKY = re.compile(
    r"支付|付款|充值|购买|下单|删除|注销|退出账号|转账|发送|发布|提交|卸载|清除|"
    r"pay|purchase|buy|delete|remove|logout|transfer|send|publish|submit|uninstall|clear",
    re.I,
)


def _visible_label(element: dict[str, Any]) -> str:
    """输入框内容可能含账号或密码，页面记录只保留控件描述。"""
    if element.get("password") == "true" or "EditText" in element.get("class", ""):
        return re.sub(
            r"\s+", " ", str(element.get("resource-id") or element.get("content-desc") or "输入框")
        )[:100]
    return re.sub(
        r"\s+",
        " ",
        str(
            element.get("text")
            or element.get("content-desc")
            or element.get("resource-id")
            or element.get("class")
            or "未命名入口"
        ),
    )[:100]


def _fingerprint(elements: list[dict[str, Any]], package: str) -> str:
    """用可见结构标识页面，忽略输入框值以免把凭据写入索引。"""
    parts = []
    for element in elements:
        if package and element.get("package") != package:
            continue
        if element.get("bounds"):
            parts.append(
                (
                    element.get("class"),
                    element.get("resource-id"),
                    _visible_label(element),
                    element.get("clickable"),
                    element.get("bounds"),
                )
            )
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:20]


def _structure_fingerprint(elements: list[dict[str, Any]], package: str) -> str:
    """回放可变搜索词时核对控件结构，而非随关键词变化的结果文字。"""
    relevant = [item for item in elements if item.get("bounds") and item.get("package") == package]
    # 列表条目数量和位置会随搜索词变化；用去重后的资源 ID 表示页面布局。
    parts = sorted(
        {
            (
                str(item.get("class") or ""),
                str(item.get("resource-id") or ""),
                str(item.get("clickable") or ""),
            )
            for item in relevant
            if item.get("resource-id")
        }
    )
    if not parts:
        parts = sorted(
            {
                (str(item.get("class") or ""), "", str(item.get("clickable") or ""))
                for item in relevant
            }
        )
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:20]


def _page_matches(
    node: dict[str, Any],
    elements: list[dict[str, Any]],
    package: str,
    credentials: dict[str, str] | None,
) -> bool:
    """页面文字变化时仍接受相同的控件结构，避免把动态首页误判为路径失效。"""
    cleaned = _redact_elements(elements, credentials)
    if _fingerprint(cleaned, package) == node["fingerprint"]:
        return True
    if not any(item.get("package") == package and item.get("resource-id") for item in cleaned):
        return False
    return _structure_fingerprint(cleaned, package) == node.get("structure_fingerprint")


def _invoke_optional_model(model: Any, messages: list[Any], timeout: float) -> Any | None:
    """探索附加建议失败时直接降级，避免模型故障暂停其他设备任务。"""
    from artemis.services.llm import with_fallback

    async def request() -> Any:
        return await model.ainvoke(messages)

    async def unavailable() -> None:
        return None

    return asyncio.run(asyncio.wait_for(with_fallback(request, unavailable), timeout=timeout))


def _action_label(
    element: dict[str, Any], elements: list[dict[str, Any]], bounds: tuple[int, int, int, int]
) -> str:
    """无文字容器优先借用内部文字，仍无描述时明确标记为未知入口。"""
    direct = str(element.get("text") or element.get("content-desc") or "").strip()
    if direct:
        return re.sub(r"\s+", " ", direct)[:100]
    resource_id = str(element.get("resource-id") or "")
    if resource_id:
        return resource_id.rsplit("/", 1)[-1][:100]
    x1, y1, x2, y2 = bounds
    children = []
    for child in elements:
        if child is element or child.get("package") != element.get("package"):
            continue
        match = _BOUNDS.fullmatch(str(child.get("bounds", "")))
        if not match:
            continue
        left, top, right, bottom = map(int, match.groups())
        if x1 <= left < right <= x2 and y1 <= top < bottom <= y2:
            label = str(child.get("text") or child.get("content-desc") or "").strip()
            if (
                label
                and label not in children
                and child.get("password") != "true"
                and "EditText" not in child.get("class", "")
            ):
                children.append(label)
        if len(children) == 2:
            break
    return re.sub(r"\s+", " ", " · ".join(children))[:100] if children else "未命名入口"


def _actions(
    elements: list[dict[str, Any]], package: str, width: int, height: int
) -> list[dict[str, Any]]:
    """同时枚举文字和无文字的可点击控件，保留定位信息与坐标后备。"""
    result = []
    seen = set()
    for element in elements:
        if element.get("clickable") != "true" or element.get("enabled") == "false":
            continue
        if package and element.get("package") != package:
            continue
        match = _BOUNDS.fullmatch(str(element.get("bounds", "")))
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        label = _action_label(element, elements, (x1, y1, x2, y2))
        item = {
            "kind": "input" if "EditText" in element.get("class", "") else "tap",
            "label": label,
            "resource_id": str(element.get("resource-id") or ""),
            "text": ""
            if "EditText" in element.get("class", "")
            else str(element.get("text") or ""),
            "description": str(element.get("content-desc") or ""),
            "class_name": str(element.get("class") or ""),
            "package": str(element.get("package") or ""),
            "x": round((x1 + x2) / (2 * max(width, 1)), 5),
            "y": round((y1 + y2) / (2 * max(height, 1)), 5),
        }
        key = (item["resource_id"], item["text"], item["description"], item["x"], item["y"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    for element in elements:
        if element.get("scrollable") != "true" or element.get("enabled") == "false":
            continue
        if package and element.get("package") != package:
            continue
        match = _BOUNDS.fullmatch(str(element.get("bounds", "")))
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        if y2 - y1 < 100:
            continue
        x = round((x1 + x2) / (2 * max(width, 1)), 5)
        for direction, start, end in (("向下滚动", 0.8, 0.2), ("向上滚动", 0.2, 0.8)):
            result.append(
                {
                    "kind": "swipe",
                    "label": direction,
                    "resource_id": str(element.get("resource-id") or ""),
                    "text": "",
                    "description": "",
                    "class_name": str(element.get("class") or ""),
                    "package": str(element.get("package") or ""),
                    "x": x,
                    "y": round((y1 + (y2 - y1) * start) / max(height, 1), 5),
                    "x2": x,
                    "y2": round((y1 + (y2 - y1) * end) / max(height, 1), 5),
                }
            )
    return result


def _module_hint(label: str, path: list[dict[str, Any]]) -> str:
    """模型不可用时按首层入口给出可编辑的初始模块。"""
    return (path[0]["label"] if path else label).strip()[:40] or "首页"


def _safe_model_label(label: str) -> str:
    """模型分组只需入口含义，邮箱和长号码先从入口名称中抹去。"""
    label = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[账号]", label)
    return re.sub(r"(?<!\d)\+?\d[\d -]{7,}\d(?!\d)", "[号码]", label)


def _redact_elements(
    elements: list[dict[str, Any]], credentials: dict[str, str] | None
) -> list[dict[str, Any]]:
    """把凭据从可能展示它们的控件文字中移除，再生成树和模型输入。"""
    if not credentials:
        return elements
    cleaned = []
    for element in elements:
        copy = dict(element)
        for key in ("text", "content-desc", "accessibilityText"):
            value = str(copy.get(key) or "")
            for secret in credentials.values():
                if secret:
                    value = value.replace(secret, "[账号信息]")
            if key in copy:
                copy[key] = value
        cleaned.append(copy)
    return cleaned


def _hide_secrets(message: str, credentials: dict[str, str] | None) -> str:
    """设备或驱动异常可能带回输入内容，写入任务前先移除凭据。"""
    for secret in (credentials or {}).values():
        if secret:
            message = message.replace(secret, "[账号信息]")
    return message


def _scrub_saved_text(task: dict[str, Any], secrets: tuple[str, ...]) -> dict[str, Any]:
    """仅清理可展示文字，保留包名、路径 ID 和控件定位字段。"""

    def scrub(value: str) -> str:
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[账号信息]")
        return value

    for node in task["nodes"]:
        node["title"] = scrub(node["title"])
        for action in node["path"]:
            for key in ("label", "text", "description"):
                if key in action:
                    action[key] = scrub(action[key])
    for edge in task["edges"]:
        for key in ("label", "text", "description"):
            if key in edge["action"]:
                edge["action"][key] = scrub(edge["action"][key])
        for key in ("module", "reason", "analysis"):
            if edge.get(key):
                edge[key] = scrub(edge[key])
    task["parameters"] = {key: scrub(value) for key, value in task.get("parameters", {}).items()}
    return task


class AppExplorationService:
    """持久化探索树、任务角色与后台执行状态。"""

    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(TRACES_PATH) / "app_explorations"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._cancels: dict[str, threading.Event] = {}
        # 仅记住本服务亲自完成的登录；重启后无法证明当前 App 属于哪个账号。
        self._verified_accounts: dict[tuple[str, str], tuple[str, str]] = {}

    def _path(self, task_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise KeyError("探索任务不存在")
        return self.directory / f"{task_id}.json"

    def _read(self, task_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._path(task_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise KeyError("探索任务不存在") from exc

    def _write(self, task: dict[str, Any]) -> None:
        path = self._path(task["id"])
        temporary = path.with_name(f".{task['id']}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump(task, output, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _ensure_editable(self, task_id: str) -> None:
        """运行线程保有任务快照，编辑前先防止双方覆盖同一棵树。"""
        if task_id in self._cancels:
            raise RuntimeError("探索正在运行，请停止后再修改任务")

    def create(self, name: str, package: str, serial: str, max_depth: int = 4) -> dict[str, Any]:
        """创建任务；深度上限防止循环页面无休止探索。"""
        if not _PACKAGE.fullmatch(package) or not 1 <= max_depth <= 8:
            raise ValueError("包名或探索深度无效")
        task = {
            "id": uuid4().hex,
            "name": name.strip()[:100],
            "package": package,
            "device_serial": serial,
            "max_depth": max_depth,
            "created_at": time.time(),
            "roles": [{"name": "默认", "has_credentials": False}],
            "nodes": [],
            "edges": [],
            "parameters": {},
            "run": None,
            "module_source": "按首层入口自动分组，可手动修改",
        }
        if not task["name"]:
            raise ValueError("请填写任务名称")
        with self._mutex:
            self._write(task)
        return task

    def list_tasks(self) -> list[dict[str, Any]]:
        """忽略单个损坏文件，按创建时间列出可继续的任务。"""
        tasks = []
        for path in self.directory.glob("*.json"):
            try:
                tasks.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(tasks, key=lambda item: item["created_at"], reverse=True)

    def get(self, task_id: str) -> dict[str, Any]:
        """返回任务树和角色状态，不读取凭据文件。"""
        return self._read(task_id)

    def _cipher(self) -> Fernet:
        """密钥仅留在本机权限为 0600 的数据目录，不进入任务 JSON 和文档。"""
        path = self.directory / ".credentials.key"
        try:
            key = path.read_bytes()
        except FileNotFoundError:
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(Fernet.generate_key())
            except FileExistsError:
                pass
            key = path.read_bytes()
        if path.stat().st_mode & 0o077:
            raise RuntimeError("凭据密钥文件权限过宽，请设为仅当前用户可读写")
        return Fernet(key)

    def save_role(self, task_id: str, role: str, username: str, password: str) -> dict[str, Any]:
        """角色凭据单独加密保存；任何查询接口都不返回账号或密码。"""
        role = role.strip()[:50]
        if not role or not username or not password:
            raise ValueError("角色、账号和密码不能为空")
        with self._mutex:
            self._ensure_editable(task_id)
            task = self._read(task_id)
            roles = {item["name"]: item for item in task["roles"]}
            roles[role] = {"name": role, "has_credentials": True}
            task["roles"] = list(roles.values())
            secret_path = (
                self.directory
                / f"{task_id}.{hashlib.sha256(role.encode()).hexdigest()[:16]}.secret"
            )
            temporary = secret_path.with_name(f".{uuid4().hex}.secret.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(
                        self._cipher().encrypt(
                            json.dumps({"username": username, "password": password}).encode()
                        )
                    )
                os.replace(temporary, secret_path)
            finally:
                temporary.unlink(missing_ok=True)
            for edge in task["edges"]:
                if (
                    edge["role"] == role
                    and edge["status"] == "blocked"
                    and edge["action"].get("label") == "登录"
                ):
                    edge["status"] = "pending"
                    edge["reason"] = None
            task = _scrub_saved_text(task, (username, password))
            self._write(task)
            return task

    def _credentials(self, task_id: str, role: str) -> dict[str, str] | None:
        path = self.directory / f"{task_id}.{hashlib.sha256(role.encode()).hexdigest()[:16]}.secret"
        if not path.exists():
            return None
        return json.loads(self._cipher().decrypt(path.read_bytes()).decode())

    def update_module(self, task_id: str, edge_id: str, module: str) -> dict[str, Any]:
        """人工调整分支模块，并同步已有子分支以支持模块回放。"""
        module = module.strip()[:50]
        if not module:
            raise ValueError("模块名称不能为空")
        with self._mutex:
            self._ensure_editable(task_id)
            task = self._read(task_id)
            target = next((item for item in task["edges"] if item["id"] == edge_id), None)
            if target is None:
                raise KeyError("分支不存在")
            target["module"] = module
            target["module_edited"] = True
            root = target.get("to_node")
            descendants = {root} if root else set()
            changed = True
            while changed:
                changed = False
                for edge in task["edges"]:
                    if edge["from_node"] in descendants and edge.get("to_node") not in descendants:
                        edge["module"] = module
                        edge["module_edited"] = True
                        if edge.get("to_node"):
                            descendants.add(edge["to_node"])
                            changed = True
            self._write(task)
            return task

    def resolve_blocker(self, task_id: str, edge_id: str) -> dict[str, Any]:
        """用户补齐外部条件后，将受阻入口重新放回待探索队列。"""
        with self._mutex:
            self._ensure_editable(task_id)
            task = self._read(task_id)
            edge = next((item for item in task["edges"] if item["id"] == edge_id), None)
            if edge is None:
                raise KeyError("分支不存在")
            if edge["status"] != "blocked":
                raise ValueError("只能重试受阻分支")
            edge["status"] = "pending"
            edge["reason"] = None
            edge.pop("analysis", None)
            self._write(task)
            return task

    def set_input(self, task_id: str, edge_id: str, value: str) -> dict[str, Any]:
        """为普通输入框保存可修改参数；密码只能走加密角色凭据。"""
        if not value or len(value) > 300:
            raise ValueError("请输入 1 到 300 个字符的非敏感测试数据")
        with self._mutex:
            self._ensure_editable(task_id)
            task = self._read(task_id)
            edge = next((item for item in task["edges"] if item["id"] == edge_id), None)
            if edge is None or edge["action"].get("kind") != "input":
                raise ValueError("该分支不是可设置参数的输入框")
            parameter = edge["action"]["parameter"]
            task["parameters"][parameter] = value
            if edge["status"] == "blocked" and edge.get("reason") == "需要填写输入参数":
                edge["status"], edge["reason"] = "pending", None
                edge.pop("analysis", None)
            self._write(task)
            return task

    def _driver(self, serial: str) -> tuple[AndroidAdbDriver, UIAutomatorClient]:
        endpoint = current_adb_endpoint()
        ui = UIAutomatorClient(device_id=serial)
        return AndroidAdbDriver(serial, AdbClient(host=endpoint.host, port=endpoint.port), ui), ui

    @staticmethod
    def _locate(action: dict[str, Any], elements: list[dict[str, Any]]) -> dict[str, Any] | None:
        """优先资源 ID、文字和无障碍描述；唯一匹配不到才用记录的坐标。"""
        for field, key in (
            ("resource_id", "resource-id"),
            ("text", "text"),
            ("description", "content-desc"),
        ):
            value = action.get(field)
            if value:
                found = [
                    item
                    for item in elements
                    if item.get(key) == value
                    and item.get("clickable") == "true"
                    and (not action.get("package") or item.get("package") == action["package"])
                ]
                if len(found) == 1:
                    return found[0]
        return None

    def _tap(
        self,
        driver: AndroidAdbDriver,
        ui: UIAutomatorClient,
        action: dict[str, Any],
        parameters: dict[str, str] | None = None,
    ) -> None:
        if action.get("kind") == "swipe":
            screen = ui.get_screen_data()
            if not asyncio.run(
                driver.swipe(
                    round(action["x"] * screen.width),
                    round(action["y"] * screen.height),
                    round(action["x2"] * screen.width),
                    round(action["y2"] * screen.height),
                    400,
                )
            ):
                raise RuntimeError("滚动失败")
            time.sleep(0.5)
            return
        screen = ui.get_screen_data()
        match = self._locate(action, screen.elements)
        bounds = _BOUNDS.fullmatch(str(match.get("bounds", ""))) if match else None
        if bounds:
            x1, y1, x2, y2 = map(int, bounds.groups())
            x, y = (x1 + x2) // 2, (y1 + y2) // 2
        else:
            x, y = round(action["x"] * screen.width), round(action["y"] * screen.height)
        if not asyncio.run(driver.tap(x, y)):
            raise RuntimeError("点击失败")
        if action.get("kind") == "input":
            value = (parameters or {}).get(action.get("parameter", ""), "")
            if not value:
                raise ValueError("需要填写输入参数")
            try:
                ui.clear_text()
                if not ui.set_sensitive_clipboard(value) or not asyncio.run(driver.press_key(279)):
                    raise RuntimeError("输入参数失败")
            finally:
                ui.set_clipboard("")
            asyncio.run(driver.press_key("back"))
        time.sleep(0.5)

    def _login(
        self, driver: AndroidAdbDriver, ui: UIAutomatorClient, credentials: dict[str, str] | None
    ) -> tuple[str | None, bool]:
        """只有明确识别到一个密码框和账号框时才自动填入；其它登录形式交给受阻列表。"""
        screen = ui.get_screen_data()
        edits = [
            item
            for item in screen.elements
            if "EditText" in item.get("class", "") and item.get("enabled") != "false"
        ]
        passwords = [item for item in edits if item.get("password") == "true"]
        if not passwords:
            return None, False
        if credentials is None:
            return "需要账号密码", False
        usernames = [item for item in edits if item not in passwords]
        if len(passwords) != 1 or len(usernames) != 1:
            return "登录表单无法可靠识别", False
        for item, value in (
            (usernames[0], credentials["username"]),
            (passwords[0], credentials["password"]),
        ):
            match = _BOUNDS.fullmatch(str(item.get("bounds", "")))
            if not match:
                return "登录输入框没有坐标", False
            x1, y1, x2, y2 = map(int, match.groups())
            if not asyncio.run(driver.tap((x1 + x2) // 2, (y1 + y2) // 2)):
                return "登录输入框无法点击", False
            try:
                # 凭据只经剪贴板发送；通用 input_text 的异常日志会带原文。
                ui.clear_text()
                if not ui.set_sensitive_clipboard(value) or not asyncio.run(driver.press_key(279)):
                    return "登录输入失败", False
            finally:
                ui.set_clipboard("")
        asyncio.run(driver.press_key("back"))
        screen = ui.get_screen_data()
        submit = next(
            (
                item
                for item in _actions(screen.elements, "", screen.width, screen.height)
                if re.search(r"登录|登入|sign.?in|log.?in", item["label"], re.I)
            ),
            None,
        )
        if submit is None:
            return "登录按钮无法可靠识别", False
        self._tap(driver, ui, submit)
        time.sleep(1)
        after = ui.get_screen_data()
        if any(item.get("password") == "true" for item in after.elements):
            return "登录未完成，请检查凭据或补齐验证步骤", False
        if any(
            re.search(
                r"验证码|验证身份|one.time.code|verification code|\botp\b",
                str(item.get("text") or item.get("content-desc") or ""),
                re.I,
            )
            for item in after.elements
        ):
            return "需要验证码或二次验证", False
        return None, True

    def _prepare_app(
        self,
        driver: AndroidAdbDriver,
        ui: UIAutomatorClient,
        package: str,
        credentials: dict[str, str] | None,
        role: str,
    ) -> str | None:
        """一次运行只准备一次 App；已在前台时沿用当前会话。"""
        if asyncio.run(driver.get_current_package()) != package:
            if not asyncio.run(driver.launch_app(package)):
                raise RuntimeError("无法打开目标 App")
            time.sleep(1)
        blocker, logged_in = self._login(driver, ui, credentials)
        foreground = asyncio.run(driver.get_current_package())
        if foreground and foreground != package:
            return "打开 App 后进入外部应用或系统页面"
        identity = (driver.device_id, package)
        if credentials:
            # 重新打开 App 不清除登录态；只有亲自完成登录才能确认角色。
            credential_id = hashlib.sha256(
                json.dumps(credentials, sort_keys=True).encode()
            ).hexdigest()
            expected = (role, credential_id)
            if logged_in:
                self._verified_accounts[identity] = expected
            elif blocker is None and self._verified_accounts.get(identity) != expected:
                return "无法核对当前登录账号；请先在手机退出账号，再运行该角色"
        elif self._verified_accounts.get(identity):
            return "当前 App 保留了其他角色的登录状态；请先在手机退出账号"
        return blocker

    def _reach_node(
        self,
        task: dict[str, Any],
        role: str,
        parent: dict[str, Any],
        driver: AndroidAdbDriver,
        ui: UIAutomatorClient,
        credentials: dict[str, str] | None,
        cancel: threading.Event,
        current_node_id: str | None,
        *,
        strict: bool = True,
    ) -> None:
        """在当前 App 会话里返回到目标分支的公共祖先，再沿已记录路径前进。"""
        package = task["package"]
        path = parent["path"]
        ancestors = [
            node
            for node in task["nodes"]
            if node["role"] == role
            and node["path"] == path[: node["depth"]]
            and node["depth"] <= len(path)
        ]
        current_node = next((node for node in task["nodes"] if node["id"] == current_node_id), None)
        for attempt in range(task["max_depth"] + 2):
            if cancel.is_set():
                return
            screen = ui.get_screen_data()
            cleaned = _redact_elements(screen.elements, credentials)
            current_fingerprint = _fingerprint(cleaned, package)
            # 精确页面优先；动态文字变化时才退到控件结构匹配。
            on_sibling = (
                attempt == 0
                and current_node is not None
                and current_node["path"] != path[: current_node["depth"]]
                and _page_matches(current_node, screen.elements, package, credentials)
            )
            exact = (
                []
                if on_sibling
                else [node for node in ancestors if node["fingerprint"] == current_fingerprint]
            )
            matched = max(exact, key=lambda node: node["depth"], default=None)
            if matched is None and not on_sibling:
                structural = [
                    node
                    for node in ancestors
                    if _page_matches(node, screen.elements, package, credentials)
                ]
                # 多层页面同构时无法确认所在层级，先返回到可识别页面。
                if len({node["depth"] for node in structural}) == 1:
                    matched = max(structural, key=lambda node: node["depth"])
            if matched is not None:
                self._follow_path(
                    task,
                    role,
                    driver,
                    ui,
                    path,
                    credentials,
                    cancel,
                    start_index=matched["depth"],
                    strict=strict,
                )
                if cancel.is_set():
                    return
                if _page_matches(parent, ui.get_screen_data().elements, package, credentials):
                    return
                raise ValueError("返回分支后页面路径已变化")
            if attempt + 1 < task["max_depth"] + 2:
                if not asyncio.run(driver.press_key("back")):
                    raise RuntimeError("无法返回上一级页面")
                if cancel.wait(0.35):
                    return
        raise ValueError("当前会话无法返回该分支，请检查弹窗或页面状态")

    def _analyze_blocker(
        self,
        task: dict[str, Any],
        edge: dict[str, Any],
        ui: UIAutomatorClient,
        credentials: dict[str, str] | None,
    ) -> str:
        """受阻时只发送脱敏后的入口和少量控件名称，请模型给出排查建议。"""
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from artemis.context import ArtemisContext, DeviceContext
            from artemis.services.llm import get_llm

            screen = ui.get_screen_data()
            elements = _redact_elements(screen.elements, credentials)
            labels = [
                _safe_model_label(_visible_label(item))
                for item in elements
                if item.get("package") == task["package"]
                and item.get("clickable") == "true"
                and item.get("password") != "true"
                and "EditText" not in item.get("class", "")
            ][:12]
            payload = {
                "目标入口": _safe_model_label(_hide_secrets(edge["action"]["label"], credentials)),
                "受阻原因": _safe_model_label(
                    _hide_secrets(edge.get("reason") or "未知", credentials)
                ),
                "当前可点击入口": labels,
            }
            context = ArtemisContext(device=DeviceContext(device_id=task["device_serial"]))
            model = get_llm(context, name="planner")
            response = _invoke_optional_model(
                model,
                [
                    SystemMessage(
                        content=(
                            "分析移动 App 探索受阻原因，给出一条可操作的中文建议，最多120字。"
                            "不要编造已执行动作，不要建议支付、删除或发送内容。"
                        )
                    ),
                    HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
                ],
                timeout=12,
            )
            if response is None:
                return "模型分析暂不可用，请根据受阻原因处理"
            content = response.content
            result = (
                content
                if isinstance(content, str)
                else "".join(
                    str(item.get("text", "")) for item in content if isinstance(item, dict)
                )
            )
            return _hide_secrets(re.sub(r"\s+", " ", result).strip()[:160], credentials)
        except Exception:
            return "模型分析暂不可用，请根据受阻原因处理"

    def _follow_path(
        self,
        task: dict[str, Any],
        role: str,
        driver: AndroidAdbDriver,
        ui: UIAutomatorClient,
        path: list[dict[str, Any]],
        credentials: dict[str, str] | None,
        cancel: threading.Event,
        strict: bool = True,
        start_index: int = 0,
    ) -> None:
        """每次点击前核对已记录的页面，防止动态页面漂移后误点。"""
        for index in range(start_index, len(path)):
            if cancel.is_set():
                return
            action = path[index]
            expected_nodes = [
                node
                for node in task["nodes"]
                if node["role"] == role and node["path"] == path[:index]
            ]
            screen = ui.get_screen_data()
            matches = (
                any(
                    _page_matches(node, screen.elements, task["package"], credentials)
                    for node in expected_nodes
                )
                if strict
                else any(
                    _structure_fingerprint(screen.elements, task["package"])
                    == node.get("structure_fingerprint", node["fingerprint"])
                    for node in expected_nodes
                )
            )
            if expected_nodes and not matches:
                raise ValueError("路径中的页面已变化")
            self._tap(driver, ui, action, task.get("parameters"))

    def _add_node(
        self,
        task: dict[str, Any],
        role: str,
        parent_edge: str | None,
        path: list[dict[str, Any]],
        screen: Any,
        credentials: dict[str, str] | None = None,
        enumerate_actions: bool = True,
    ) -> dict[str, Any]:
        elements = _redact_elements(screen.elements, credentials)
        fingerprint = _fingerprint(elements, task["package"])
        labels = [
            _visible_label(item)
            for item in elements
            if item.get("text") or item.get("content-desc")
        ]
        node = {
            "id": uuid4().hex,
            "role": role,
            "parent_edge": parent_edge,
            "depth": len(path),
            "fingerprint": fingerprint,
            "structure_fingerprint": _structure_fingerprint(elements, task["package"]),
            "title": " · ".join(labels[:3])[:120] or task["package"],
            "path": path,
            "created_at": time.time(),
        }
        task["nodes"].append(node)
        parent_action = next(
            (edge["action"] for edge in task["edges"] if edge["id"] == parent_edge), None
        )
        prior = any(
            item["role"] == role and item["fingerprint"] == fingerprint and item["id"] != node["id"]
            for item in task["nodes"]
        )
        if parent_action and parent_action.get("kind") == "input":
            prior = False  # 输入后仍在同一布局，也要继续枚举提交或搜索按钮。
        if enumerate_actions and not prior and len(path) < task["max_depth"]:
            if any(item.get("password") == "true" for item in elements):
                if path:
                    task["edges"].append(
                        {
                            "id": uuid4().hex,
                            "role": role,
                            "from_node": node["id"],
                            "to_node": None,
                            "action": {"label": "密码或验证步骤", "x": 0, "y": 0},
                            "module": "登录",
                            "status": "blocked",
                            "reason": "需要人工处理密码或验证码",
                        }
                    )
                return node
            for action in _actions(elements, task["package"], screen.width, screen.height):
                if action["kind"] == "input" and any(
                    old.get("kind") == "input"
                    and (
                        old.get("resource_id") == action["resource_id"]
                        if action["resource_id"]
                        else (old.get("x"), old.get("y")) == (action["x"], action["y"])
                    )
                    for old in path
                ):
                    continue
                edge_id = uuid4().hex
                if action["kind"] == "input":
                    action["parameter"] = f"input_{edge_id[:8]}"
                blocked = (
                    bool(_RISKY.search(action["label"]))
                    or action["label"] == "未命名入口"
                    or action["kind"] == "input"
                )
                task["edges"].append(
                    {
                        "id": edge_id,
                        "role": role,
                        "from_node": node["id"],
                        "to_node": None,
                        "action": action,
                        "module": _module_hint(action["label"], path),
                        "status": "blocked" if blocked else "pending",
                        "reason": (
                            "需要填写输入参数"
                            if action["kind"] == "input"
                            else "无文字入口，需要人工确认"
                            if action["label"] == "未命名入口"
                            else "高风险操作，需人工确认"
                        )
                        if blocked
                        else None,
                    }
                )
        return node

    def start(
        self, task_id: str, role: str, mode: str = "explore", module: str | None = None
    ) -> dict[str, Any]:
        """持有设备锁后启动后台探索或已有分支回放。"""
        with self._mutex:
            task = self._read(task_id)
            if role not in {item["name"] for item in task["roles"]}:
                raise ValueError("角色不存在")
            if mode not in {"explore", "replay"}:
                raise ValueError("运行模式无效")
            if (task.get("run") or {}).get("status") == "running" and task_id in self._cancels:
                raise ValueError("任务正在运行")
            lock = DeviceExecutionLock(
                device_id=task["device_serial"],
                description=f"App exploration: {task['name']}",
                ingress="web",
            )
            try:
                lock.acquire(blocking=False)
            except DeviceBusyError as exc:
                raise RuntimeError(str(exc)) from exc
            run = {
                "id": uuid4().hex,
                "role": role,
                "mode": mode,
                "module": module,
                "status": "running",
                "completed": 0,
                "error": None,
                "started_at": time.time(),
            }
            task["run"] = run
            try:
                self._write(task)
                cancel = threading.Event()
                self._cancels[task_id] = cancel
                threading.Thread(
                    target=self._execute,
                    args=(task_id, lock, cancel),
                    daemon=True,
                    name=f"app-explore-{task_id[:8]}",
                ).start()
                return task
            except Exception:
                self._cancels.pop(task_id, None)
                lock.release()
                task["run"]["status"] = "failed"
                self._write(task)
                raise

    def stop(self, task_id: str) -> dict[str, Any]:
        """请求动作循环停止，并处理服务重启留下的旧运行状态。"""
        event = self._cancels.get(task_id)
        if event:
            event.set()
        with self._mutex:
            task = self._read(task_id)
            if event is None and (task.get("run") or {}).get("status") == "running":
                task["run"]["status"] = "stopped"
                task["run"]["error"] = "服务已重启，运行状态已恢复为停止"
                self._write(task)
            return task

    def _execute(self, task_id: str, lock: DeviceExecutionLock, cancel: threading.Event) -> None:
        task: dict[str, Any] | None = None
        credentials: dict[str, str] | None = None
        try:
            with self._mutex:
                task = self._read(task_id)
            role, mode, module = task["run"]["role"], task["run"]["mode"], task["run"]["module"]
            driver, ui = self._driver(task["device_serial"])
            # 驱动的 launch_app 只表示命令已执行，包名存在性需单独核对。
            if (
                not str(driver.device.shell(f"pm path {task['package']}"))
                .strip()
                .startswith("package:")
            ):
                raise ValueError("目标 App 未安装在该设备")
            credentials = self._credentials(task_id, role)
            if mode == "explore":
                self._explore(task, role, driver, ui, credentials, cancel)
            else:
                self._replay(task, role, module, driver, ui, credentials, cancel)
            if cancel.is_set():
                task["run"]["status"] = "stopped"
            elif mode == "explore" and any(
                edge["role"] == role and edge["status"] in {"pending", "blocked"}
                for edge in task["edges"]
            ):
                task["run"]["status"] = "partial"
            else:
                task["run"]["status"] = "completed"
        except Exception as exc:
            if task is not None:
                task["run"]["status"] = "failed"
                task["run"]["error"] = _hide_secrets(str(exc), credentials)
        finally:
            try:
                with self._mutex:
                    if task is not None:
                        self._write(task)
                    self._cancels.pop(task_id, None)
            finally:
                lock.release()

    def _explore(
        self,
        task: dict[str, Any],
        role: str,
        driver: AndroidAdbDriver,
        ui: UIAutomatorClient,
        credentials: dict[str, str] | None,
        cancel: threading.Event,
    ) -> None:
        roots = [node for node in task["nodes"] if node["role"] == role and node["depth"] == 0]
        login_edges = [
            edge
            for edge in task["edges"]
            if edge["role"] == role and edge["action"]["label"] == "登录"
        ]
        # 每次探索只准备一次 App，并确认当前登录角色。
        blocker = self._prepare_app(driver, ui, task["package"], credentials, role)
        screen = ui.get_screen_data()
        fingerprint = _fingerprint(_redact_elements(screen.elements, credentials), task["package"])
        root = next((item for item in roots if item["fingerprint"] == fingerprint), None)
        if root is None:
            # 动态首页的文字变化不应重复创建整棵入口树。
            root = next(
                (
                    item
                    for item in roots
                    if _page_matches(item, screen.elements, task["package"], credentials)
                ),
                None,
            )
        if root is None:
            root = self._add_node(
                task, role, None, [], screen, credentials, enumerate_actions=not bool(blocker)
            )
        if blocker:
            if login_edges:
                for edge in login_edges:
                    edge["status"], edge["reason"] = "blocked", blocker
                login_edge = login_edges[0]
            else:
                login_edge = {
                    "id": uuid4().hex,
                    "role": role,
                    "from_node": root["id"],
                    "to_node": None,
                    "action": {"label": "登录", "x": 0, "y": 0},
                    "module": "登录",
                    "status": "blocked",
                    "reason": blocker,
                }
                task["edges"].append(login_edge)
            login_edge["analysis"] = self._analyze_blocker(task, login_edge, ui, credentials)
        else:
            for edge in login_edges:
                edge["status"], edge["reason"], edge["to_node"] = "explored", None, root["id"]
                edge.pop("analysis", None)
        with self._mutex:
            self._write(task)
        if blocker:
            return
        # 旧版可能为同一任务留下多个首页；只恢复本次匹配首页下的误判分支。
        reachable_nodes = {root["id"]}
        for node in sorted(task["nodes"], key=lambda item: item["depth"]):
            parent_edge = next(
                (edge for edge in task["edges"] if edge["id"] == node.get("parent_edge")),
                None,
            )
            if parent_edge and parent_edge["from_node"] in reachable_nodes:
                reachable_nodes.add(node["id"])
        for edge in task["edges"]:
            if (
                edge["role"] == role
                and edge["from_node"] in reachable_nodes
                and edge["status"] == "blocked"
                and edge.get("reason") in {"页面路径已变化", "路径中的页面已变化"}
            ):
                edge["status"], edge["reason"] = "pending", None
                edge.pop("analysis", None)
        with self._mutex:
            self._write(task)
        # 每轮最多处理 80 个分支，剩余队列可下次继续，避免复杂应用持续占用设备。
        budget = 80
        path_mismatch_count = 0
        analysis_budget = 3
        analysis_cache: dict[str, str] = {}
        current_node_id: str | None = root["id"]
        while budget > 0 and not cancel.is_set():
            pending = [
                item
                for item in task["edges"]
                if item["role"] == role
                and item["from_node"] in reachable_nodes
                and item["status"] == "pending"
            ]
            if not pending:
                break
            # 优先处理当前页面的入口，减少返回导航和弹窗状态变化。
            edge = next(
                (item for item in pending if item["from_node"] == current_node_id),
                pending[0],
            )
            budget -= 1
            parent = next(node for node in task["nodes"] if node["id"] == edge["from_node"])
            try:
                self._reach_node(
                    task, role, parent, driver, ui, credentials, cancel, current_node_id
                )
                if cancel.is_set():
                    return
                before = ui.get_screen_data()
                if not _page_matches(parent, before.elements, task["package"], credentials):
                    raise ValueError("页面路径已变化")
                before_fingerprint = _fingerprint(
                    _redact_elements(before.elements, credentials), task["package"]
                )
                self._tap(driver, ui, edge["action"], task.get("parameters"))
                screen = ui.get_screen_data()
                foreground = asyncio.run(driver.get_current_package())
                if foreground and foreground != task["package"]:
                    edge["status"], edge["reason"] = "blocked", "跳转到应用外部"
                    current_node_id = None
                elif (
                    edge["action"].get("kind") != "input"
                    and _fingerprint(
                        _redact_elements(screen.elements, credentials), task["package"]
                    )
                    == before_fingerprint
                ):
                    edge["status"] = "unchanged"
                    current_node_id = parent["id"]
                else:
                    node = self._add_node(
                        task,
                        role,
                        edge["id"],
                        parent["path"] + [edge["action"]],
                        screen,
                        credentials,
                    )
                    edge["to_node"], edge["status"] = node["id"], "explored"
                    reachable_nodes.add(node["id"])
                    current_node_id = node["id"]
                if edge["status"] != "blocked":
                    edge.pop("analysis", None)
                path_mismatch_count = 0
            except Exception as exc:
                current_node_id = None
                edge["status"], edge["reason"] = (
                    "blocked",
                    _hide_secrets(str(exc), credentials)[:200],
                )
                if str(exc) in {
                    "页面路径已变化",
                    "路径中的页面已变化",
                    "返回分支后页面路径已变化",
                    "当前会话无法返回该分支，请检查弹窗或页面状态",
                }:
                    path_mismatch_count += 1
                else:
                    path_mismatch_count = 0
            if edge["status"] == "blocked":
                reason = edge.get("reason") or "未知"
                edge.pop("analysis", None)
                if reason in analysis_cache:
                    edge["analysis"] = analysis_cache[reason]
                elif analysis_budget > 0:
                    edge["analysis"] = self._analyze_blocker(task, edge, ui, credentials)
                    analysis_cache[reason] = edge["analysis"]
                    analysis_budget -= 1
            task["run"]["completed"] += 1
            with self._mutex:
                self._write(task)
            # 连续失败通常表示登录态或页面布局整体变了，留待处理分支供用户核对。
            if path_mismatch_count >= 3:
                task["run"]["error"] = "连续 3 条分支无法返回，已停止本轮探索"
                break
        if not cancel.is_set():
            self._suggest_modules(task)

    def _suggest_modules(self, task: dict[str, Any]) -> None:
        """模型按首页入口建议模块；失败时保留可编辑的入口名称分组。"""
        root_ids = {node["id"] for node in task["nodes"] if node["depth"] == 0}
        roots = [edge for edge in task["edges"] if edge["from_node"] in root_ids]
        if not roots or not any(not edge.get("module_edited") for edge in roots):
            return
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from artemis.context import ArtemisContext, DeviceContext
            from artemis.services.llm import get_llm

            context = ArtemisContext(device=DeviceContext(device_id=task["device_serial"]))
            model = get_llm(context, name="planner")
            # 仅传入口标签，不传账号、密码、截图或页面原始 XML。
            entries = [
                {"id": edge["id"], "label": _safe_model_label(edge["action"]["label"])}
                for edge in roots
                if not edge.get("module_edited")
            ]
            response = _invoke_optional_model(
                model,
                [
                    SystemMessage(
                        content='将移动 App 首页入口按用户功能分为简短中文模块。只返回 JSON：{"items":[{"id":"原 id","module":"模块名"}]}。'
                    ),
                    HumanMessage(content=json.dumps(entries, ensure_ascii=False)),
                ],
                timeout=25,
            )
            if response is None:
                raise ValueError("模型分组暂不可用")
            content = response.content
            raw = (
                content
                if isinstance(content, str)
                else "".join(
                    str(item.get("text", "")) for item in content if isinstance(item, dict)
                )
            )
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I).strip()
            parsed = json.loads(raw)
            items = parsed.get("items", []) if isinstance(parsed, dict) else parsed
            if not isinstance(items, list):
                raise ValueError("模型未返回模块列表")
            allowed = {entry["id"] for entry in entries}
            suggestions = {
                item["id"]: re.sub(r"\s+", " ", item["module"]).strip()[:50]
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"] in allowed
                and isinstance(item.get("module"), str)
                and item["module"].strip()
            }
            if not suggestions:
                raise ValueError("模型未给出有效模块")
            by_label = {
                edge["action"]["label"]: suggestions[edge["id"]]
                for edge in roots
                if edge["id"] in suggestions
            }
            for edge in task["edges"]:
                if edge.get("module_edited"):
                    continue
                parent = next(node for node in task["nodes"] if node["id"] == edge["from_node"])
                source = parent["path"][0]["label"] if parent["path"] else edge["action"]["label"]
                if source in by_label:
                    edge["module"] = by_label[source]
            task["module_source"] = "模型建议，可手动修改"
            with self._mutex:
                self._write(task)
        except Exception:
            task["module_source"] = "模型分组不可用，按首层入口自动分组，可手动修改"
            with self._mutex:
                self._write(task)

    def _replay(
        self,
        task: dict[str, Any],
        role: str,
        module: str | None,
        driver: AndroidAdbDriver,
        ui: UIAutomatorClient,
        credentials: dict[str, str] | None,
        cancel: threading.Event,
    ) -> None:
        """模块运行只回放该模块已确认的终点路径，整包运行覆盖全部模块。"""
        nodes = [
            item
            for item in task["nodes"]
            if item["role"] == role
            and item["depth"] > 0
            and (
                module is None
                or any(
                    edge["id"] == item["parent_edge"] and edge["module"] == module
                    for edge in task["edges"]
                )
            )
        ]
        if not nodes:
            raise ValueError("该模块还没有可回放的路径")
        # 整次回放共用一个 App 会话，路径之间通过返回键寻找公共祖先。
        blocker = self._prepare_app(driver, ui, task["package"], credentials, role)
        if blocker:
            raise ValueError(blocker)
        failures = []
        current_node_id: str | None = None
        for node in nodes:
            if cancel.is_set():
                return
            try:
                self._reach_node(
                    task, role, node, driver, ui, credentials, cancel, current_node_id, strict=False
                )
                if cancel.is_set():
                    return
                screen = ui.get_screen_data()
                if _structure_fingerprint(screen.elements, task["package"]) != node.get(
                    "structure_fingerprint", node["fingerprint"]
                ):
                    raise ValueError("最终页面与探索记录不一致")
                current_node_id = node["id"]
            except Exception as exc:
                current_node_id = None
                failures.append(f"{node['title']}: {_hide_secrets(str(exc), credentials)}")
            task["run"]["completed"] += 1
            with self._mutex:
                self._write(task)
        if failures:
            raise RuntimeError("；".join(failures[:5]))

    def markdown(self, task_id: str) -> str:
        """导出当前树和未完成项；不读取凭据文件。"""
        task = self._read(task_id)
        lines = [
            f"# {task['name']}",
            "",
            f"- App：`{task['package']}`",
            f"- 设备：`{task['device_serial']}`",
            f"- 探索深度上限：{task['max_depth']}",
            f"- 已发现页面：{len(task['nodes'])}",
            f"- 已探索分支：{sum(edge['status'] == 'explored' for edge in task['edges'])}",
            f"- 待探索分支：{sum(edge['status'] == 'pending' for edge in task['edges'])}",
            f"- 受阻分支：{sum(edge['status'] == 'blocked' for edge in task['edges'])}",
            "",
        ]
        if task.get("run"):
            lines += [f"- 最近运行：{task['run']['mode']} / {task['run']['status']}", ""]
        blocked = [edge for edge in task["edges"] if edge["status"] == "blocked"]
        if blocked:
            lines += ["## 待处理的受阻项", ""]
            parents = {node["id"]: node for node in task["nodes"]}
            for edge in blocked:
                parent = parents.get(edge["from_node"])
                route = (
                    " → ".join(item["label"] for item in parent["path"]) if parent else "打开 App"
                )
                lines.append(
                    f"- [{edge['role']}] {route or '打开 App'} → {edge['action']['label']}：{edge.get('reason') or '原因待补充'}（模块：{edge['module']}）"
                )
                if edge.get("analysis"):
                    lines.append(f"  - 模型建议：{edge['analysis']}")
            lines.append("")
        for role in task["roles"]:
            lines += [f"## 角色：{role['name']}", ""]
            for node in task["nodes"]:
                if node["role"] != role["name"]:
                    continue
                lines += [
                    f"{'#' * min(6, node['depth'] + 3)} {node['title']}",
                    "",
                    f"路径：{' → '.join(item['label'] for item in node['path']) or '打开 App'}",
                    "",
                ]
                for edge in task["edges"]:
                    if edge["from_node"] == node["id"]:
                        parameter = edge["action"].get("parameter")
                        lines.append(
                            f"- {edge['action']['label']}｜模块：{edge['module']}｜{edge['status']}"
                            + (f"｜参数：{parameter}" if parameter else "")
                            + (f"｜原因：{edge['reason']}" if edge.get("reason") else "")
                            + (f"｜模型建议：{edge['analysis']}" if edge.get("analysis") else "")
                        )
                lines.append("")
        return "\n".join(lines)


app_exploration_service = AppExplorationService()

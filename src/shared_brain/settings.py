"""Machine-local settings; model secrets belong to the OS credential store."""

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

import keyring


def default_home() -> Path:
    if os.environ.get("SHARED_BRAIN_HOME"):
        return Path(os.environ["SHARED_BRAIN_HOME"]).expanduser()
    return Path.home() / ".shared-brain"


@dataclass
class Settings:
    vault_path: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    maintenance_enabled: bool = False
    maintenance_time: str = "02:00"
    key_configured: bool = False
    credential_account: str = ""


class SettingsStore:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else default_home()
        self.path = self.home / "settings.json"
        self.account = self.load().credential_account or hashlib.sha256(str(self.home.resolve()).encode()).hexdigest()[:20]

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        values = json.loads(self.path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in values.items() if k in known})

    def update(self, values: dict, api_key: str | None = None) -> Settings:
        current = asdict(self.load())
        allowed = set(current) - {"key_configured", "credential_account"}
        if set(values) - allowed:
            raise ValueError("包含不支持的设置项")
        current.update(values)
        parsed = urlparse(current["base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Base URL 必须是有效的 HTTP(S) 地址，不应包含凭据")
        if not isinstance(current["maintenance_time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", current["maintenance_time"]):
            raise ValueError("维护时间须为 24 小时制 HH:MM")
        if not isinstance(current["maintenance_enabled"], bool):
            raise ValueError("维护开关须为布尔值")
        if not isinstance(current["model"], str) or len(current["model"]) > 200:
            raise ValueError("模型名称须为不超过 200 字符的文本")
        if "vault_path" in values:
            if not isinstance(values["vault_path"], str) or not values["vault_path"].strip():
                raise ValueError("请选择知识库目录")
            path = Path(values["vault_path"]).expanduser().resolve()
            if path.exists() and not path.is_dir():
                raise ValueError("请选择文件夹路径")
            current["vault_path"] = str(path)
        if api_key is not None:
            if api_key.strip():
                keyring.set_password("shared-brain", self.account, api_key.strip())
                current["key_configured"] = True
            elif current["key_configured"]:
                keyring.delete_password("shared-brain", self.account)
                current["key_configured"] = False
        self.home.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return Settings(**current)

    def api_key(self) -> str:
        if not self.load().key_configured:
            return ""
        return keyring.get_password("shared-brain", self.account) or ""

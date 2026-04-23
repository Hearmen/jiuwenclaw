from __future__ import annotations

import io
import zipfile

import pytest

from jiuwenclaw.agentserver.skill_manager import SkillManager

_OPENJIUWEN_ZIP_URL = "https://openjiuwen-market.obs.ap-southeast-1.myhuaweicloud.com/plugins/demo.zip"


# 创建一个 harness 子类来公开受保护的方法（勿命名为 Test*，否则 pytest 会当成测试类收集）
class OpenJiuwenHarnessSkillManager(SkillManager):
    def get_openjiuwen_market_base_url(self):
        return self._get_openjiuwen_market_base_url()

    def set_mock_get_data(self, mock_func):
        self._openjiuwen_http_get_data = mock_func

    def set_mock_download(self, mock_func):
        self._download_zip_and_verify = mock_func

    def call_assert_openjiuwen_download_url_allowed(self, url):
        self._assert_openjiuwen_download_url_allowed(url)

    def call_safe_extract_zip_to_dir(self, zip_path, out_dir):
        self._safe_extract_zip_to_dir(zip_path, out_dir)


def _build_skill_zip_bytes(*, skill_name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{skill_name}/SKILL.md",
            ("---\n" f"name: {skill_name}\n" "description: test skill\n" "version: 1.0.0\n" "---\n" "body\n"),
        )
    return buf.getvalue()


def test_get_openjiuwen_market_base_url_default(monkeypatch):
    monkeypatch.delenv("OPENJIUWEN_MARKET_BASE_URL", raising=False)
    manager = OpenJiuwenHarnessSkillManager(workspace_dir="dummy")
    assert manager.get_openjiuwen_market_base_url() == "https://teamskills.openjiuwen.com"


def test_get_openjiuwen_market_base_url_env_override(monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_MARKET_BASE_URL", "https://example.com/custom/hub/")
    manager = OpenJiuwenHarnessSkillManager(workspace_dir="dummy")
    assert manager.get_openjiuwen_market_base_url() == "https://example.com/custom/hub"


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_info(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENJIUWEN_MARKET_BASE_URL", raising=False)
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))
    payload = await manager.handle_skills_openjiuwen_info({})
    assert payload["success"] is True
    assert payload["market_base_url"] == "https://teamskills.openjiuwen.com"


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_search_maps_response(tmp_path):
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))

    async def _fake_get_data(path, **kwargs):  # noqa: ANN001
        assert path == "/api/v1/plugins"
        return {
            "items": [
                {
                    "asset_id": "demo-skill",
                    "name": "demo-skill",
                    "display_name": "Demo Skill",
                    "short_desc": "desc",
                    "latest_version": "1.2.3",
                    "update_time": 123,
                }
            ]
        }

    manager.set_mock_get_data(_fake_get_data)
    payload = await manager.handle_skills_openjiuwen_search({"q": "demo", "limit": 10})
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["skills"][0]["asset_id"] == "demo-skill"
    assert payload["skills"][0]["display_name"] == "Demo Skill"


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_install_success(tmp_path):
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))
    zip_bytes = _build_skill_zip_bytes(skill_name="demo-skill")

    async def _fake_get_data(path, **kwargs):  # noqa: ANN001
        assert path == "/api/v1/artifacts/demo-skill"
        return {
            "download_url": _OPENJIUWEN_ZIP_URL,
            "checksum_sha256": "",
            "version": "1.0.0",
        }

    async def _fake_download(url, **kwargs):  # noqa: ANN001
        assert url == _OPENJIUWEN_ZIP_URL
        return zip_bytes

    manager.set_mock_get_data(_fake_get_data)
    manager.set_mock_download(_fake_download)

    payload = await manager.handle_skills_openjiuwen_install({"asset_id": "demo-skill"})
    assert payload["success"] is True
    assert payload["skill"]["name"] == "demo-skill"
    assert (tmp_path / "skills" / "demo-skill" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_install_duplicate_without_force(tmp_path):
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))
    zip_bytes = _build_skill_zip_bytes(skill_name="demo-skill")
    (tmp_path / "skills" / "demo-skill").mkdir(parents=True, exist_ok=True)

    async def _fake_get_data(path, **kwargs):  # noqa: ANN001
        return {
            "download_url": _OPENJIUWEN_ZIP_URL,
            "checksum_sha256": "",
            "version": "1.0.0",
        }

    async def _fake_download(url, **kwargs):  # noqa: ANN001
        return zip_bytes

    manager.set_mock_get_data(_fake_get_data)
    manager.set_mock_download(_fake_download)

    payload = await manager.handle_skills_openjiuwen_install({"asset_id": "demo-skill", "force": False})
    assert payload["success"] is False
    assert "已安装" in payload["detail"]


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_install_invalid_zip(tmp_path):
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))

    async def _fake_get_data(path, **kwargs):  # noqa: ANN001
        return {
            "download_url": _OPENJIUWEN_ZIP_URL,
            "checksum_sha256": "",
            "version": "1.0.0",
        }

    async def _fake_download(url, **kwargs):  # noqa: ANN001
        return b"not-a-zip"

    manager.set_mock_get_data(_fake_get_data)
    manager.set_mock_download(_fake_download)

    payload = await manager.handle_skills_openjiuwen_install({"asset_id": "demo-skill"})
    assert payload["success"] is False
    assert payload["detail_key"] == "skills.openjiuwen.errors.installFailed"
    # 未走真实 _download_zip_and_verify 校验时，写入伪 zip 后解压会得到 zipfile 的异常文案
    assert "zip" in payload["detail"].lower()


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_install_download_failure(tmp_path):
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))

    async def _fake_get_data(path, **kwargs):  # noqa: ANN001
        return {
            "download_url": _OPENJIUWEN_ZIP_URL,
            "checksum_sha256": "",
            "version": "1.0.0",
        }

    async def _fake_download(url, **kwargs):  # noqa: ANN001
        raise RuntimeError("download failed")

    manager.set_mock_get_data(_fake_get_data)
    manager.set_mock_download(_fake_download)

    payload = await manager.handle_skills_openjiuwen_install({"asset_id": "demo-skill"})
    assert payload["success"] is False
    assert payload["detail"] == "download failed"
    assert payload["detail_key"] == "skills.openjiuwen.errors.installFailed"


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_search_hide_internal_error(tmp_path):
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))

    async def _fake_get_data(path, **kwargs):  # noqa: ANN001
        raise RuntimeError("internal endpoint detail")

    manager.set_mock_get_data(_fake_get_data)
    payload = await manager.handle_skills_openjiuwen_search({"q": "demo", "limit": 10})
    assert payload["success"] is False
    assert payload["detail"] == "internal endpoint detail"
    assert payload["detail_key"] == "skills.openjiuwen.errors.searchFailed"


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_install_rejects_untrusted_download_host(tmp_path):
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))

    async def _fake_get_data(path, **kwargs):  # noqa: ANN001
        return {
            "download_url": "https://example.com/demo.zip",
            "checksum_sha256": "",
            "version": "1.0.0",
        }

    async def _fake_download(url, **kwargs):  # noqa: ANN001
        raise AssertionError("should not download when host is untrusted")

    manager.set_mock_get_data(_fake_get_data)
    manager.set_mock_download(_fake_download)

    payload = await manager.handle_skills_openjiuwen_install({"asset_id": "demo-skill"})
    assert payload["success"] is False
    assert "host" in payload["detail"] and "白名单" in payload["detail"]
    assert payload["detail_key"] == "skills.openjiuwen.errors.installFailed"


def test_openjiuwen_allowed_download_hosts_support_suffix_rule(monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_ALLOWED_DOWNLOAD_HOSTS", ".myhuaweicloud.com")
    manager = OpenJiuwenHarnessSkillManager(workspace_dir="dummy")
    manager.call_assert_openjiuwen_download_url_allowed(
        "https://openjiuwen-market.obs.ap-southeast-1.myhuaweicloud.com/plugins/demo.zip"
    )


def test_openjiuwen_allowed_download_hosts_support_wildcard_region(monkeypatch):
    monkeypatch.setenv(
        "OPENJIUWEN_ALLOWED_DOWNLOAD_HOSTS",
        "openjiuwen-market.obs.*.myhuaweicloud.com",
    )
    manager = OpenJiuwenHarnessSkillManager(workspace_dir="dummy")
    manager.call_assert_openjiuwen_download_url_allowed(
        "https://openjiuwen-market.obs.ap-east-1.myhuaweicloud.com/plugins/demo.zip"
    )


def test_safe_extract_zip_to_dir_rejects_zip_slip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil.txt", b"x")
    zip_path = tmp_path / "bad.zip"
    zip_path.write_bytes(buf.getvalue())
    out = tmp_path / "out"
    out.mkdir()
    manager = OpenJiuwenHarnessSkillManager(workspace_dir="dummy")
    with pytest.raises(RuntimeError, match="非法路径|越界"):
        manager.call_safe_extract_zip_to_dir(zip_path, out)


def test_safe_extract_zip_to_dir_writes_skill(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "demo-skill/SKILL.md",
            "---\nname: demo-skill\ndescription: x\nversion: 1.0.0\n---\n",
        )
    zip_path = tmp_path / "ok.zip"
    zip_path.write_bytes(buf.getvalue())
    out = tmp_path / "out"
    out.mkdir()
    manager = OpenJiuwenHarnessSkillManager(workspace_dir="dummy")
    manager.call_safe_extract_zip_to_dir(zip_path, out)
    assert (out / "demo-skill" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_handle_skills_openjiuwen_install_rejects_zip_slip(tmp_path):
    manager = OpenJiuwenHarnessSkillManager(workspace_dir=str(tmp_path))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil.txt", b"x")
    bad_zip = buf.getvalue()

    async def _fake_get_data(path, **kwargs):  # noqa: ANN001
        return {
            "download_url": _OPENJIUWEN_ZIP_URL,
            "checksum_sha256": "",
            "version": "1.0.0",
        }

    async def _fake_download(url, **kwargs):  # noqa: ANN001
        return bad_zip

    manager.set_mock_get_data(_fake_get_data)
    manager.set_mock_download(_fake_download)

    payload = await manager.handle_skills_openjiuwen_install({"asset_id": "demo-skill"})
    assert payload["success"] is False
    assert "非法路径" in payload["detail"] or "越界" in payload["detail"]
    assert payload["detail_key"] == "skills.openjiuwen.errors.installFailed"
    assert not (tmp_path / "evil.txt").exists()

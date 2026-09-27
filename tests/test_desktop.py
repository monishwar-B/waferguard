import os

from waferguard.desktop import launcher


def test_desktop_env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for k in ("WG_CONFIG", "WG_DATABASE_URL", "WG_STORAGE_DIR", "WG_MODELS__CHAMPION_DIR", "WG_JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    env = launcher.configure_env()
    assert env["WG_DATABASE_URL"].startswith("sqlite:///") and os.path.exists(env["WG_CONFIG"])
    assert len(env["WG_JWT_SECRET"]) == 64 and launcher.configure_env()["WG_JWT_SECRET"] == env["WG_JWT_SECRET"]
    assert 0 < launcher.free_port() < 65536
    assert not launcher.wait_ready("http://127.0.0.1:9", timeout=0.5)

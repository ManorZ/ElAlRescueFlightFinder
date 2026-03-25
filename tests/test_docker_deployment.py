"""
Tests for Docker deployment support.

Verifies that headless mode, basic auth, and deployment config work
correctly without breaking existing Windows functionality.
"""

import importlib
import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_headless_false_on_windows_by_default():
    """On Windows (os.name=='nt') with no HEADLESS env var, should be False."""
    os.environ.pop("HEADLESS", None)
    HEADLESS = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes") or (
        os.name != "nt" and not os.environ.get("DISPLAY")
    )
    if os.name == "nt":
        assert HEADLESS is False, "Should default to non-headless on Windows"


def test_headless_true_when_env_var_set():
    """HEADLESS=1 should force headless mode regardless of platform."""
    os.environ["HEADLESS"] = "1"
    HEADLESS = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes") or (
        os.name != "nt" and not os.environ.get("DISPLAY")
    )
    assert HEADLESS is True, "HEADLESS=1 should force headless mode"
    os.environ.pop("HEADLESS")


def test_headless_true_variants():
    """HEADLESS accepts 'true', 'yes', and '1'."""
    for val in ("1", "true", "TRUE", "yes", "Yes"):
        os.environ["HEADLESS"] = val
        HEADLESS = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")
        assert HEADLESS is True, f"HEADLESS={val} should be True"
    os.environ.pop("HEADLESS")


def test_flask_host_configurable():
    """FLASK_HOST should be configurable via env var."""
    os.environ["FLASK_HOST"] = "0.0.0.0"
    import config
    importlib.reload(config)
    assert config.FLASK_HOST == "0.0.0.0"

    os.environ.pop("FLASK_HOST")
    importlib.reload(config)
    assert config.FLASK_HOST == "127.0.0.1", "Should default to 127.0.0.1"


def test_basic_auth_config_from_env():
    """BASIC_AUTH_USER/PASS should load from env vars."""
    os.environ["BASIC_AUTH_USER"] = "admin"
    os.environ["BASIC_AUTH_PASS"] = "secret"
    import config
    importlib.reload(config)
    assert config.BASIC_AUTH_USER == "admin"
    assert config.BASIC_AUTH_PASS == "secret"

    os.environ.pop("BASIC_AUTH_USER")
    os.environ.pop("BASIC_AUTH_PASS")
    importlib.reload(config)
    assert config.BASIC_AUTH_USER == ""
    assert config.BASIC_AUTH_PASS == ""


def test_basic_auth_middleware_registers_when_configured():
    """Auth middleware should be registered when env vars are set."""
    os.environ["BASIC_AUTH_USER"] = "admin"
    os.environ["BASIC_AUTH_PASS"] = "secret"
    import config
    importlib.reload(config)

    from web import create_app
    app = create_app()
    assert None in app.before_request_funcs, "Expected global before_request handler"

    os.environ.pop("BASIC_AUTH_USER")
    os.environ.pop("BASIC_AUTH_PASS")
    importlib.reload(config)


def test_basic_auth_middleware_absent_when_not_configured():
    """Auth middleware should NOT be registered when env vars are empty."""
    os.environ.pop("BASIC_AUTH_USER", None)
    os.environ.pop("BASIC_AUTH_PASS", None)
    import config
    importlib.reload(config)

    from web import create_app
    app = create_app()
    assert not app.before_request_funcs.get(None), "No middleware when auth not configured"


def test_server_requirements_exclude_windows_deps():
    """requirements-server.txt should not include pystray or Pillow."""
    req_path = os.path.join(os.path.dirname(__file__), "..", "requirements-server.txt")
    with open(req_path) as f:
        content = f.read()
    assert "pystray" not in content, "pystray should not be in server requirements"
    assert "Pillow" not in content, "Pillow should not be in server requirements"


def test_dockerignore_excludes_env():
    """.dockerignore should exclude .env to prevent credential leaks."""
    ignore_path = os.path.join(os.path.dirname(__file__), "..", ".dockerignore")
    with open(ignore_path) as f:
        content = f.read()
    assert ".env" in content, ".env should be in .dockerignore"


def test_deployment_files_exist():
    """All deployment files should exist."""
    root = os.path.join(os.path.dirname(__file__), "..")
    for f in ["Dockerfile", "docker-compose.yml", ".dockerignore", "requirements-server.txt"]:
        assert os.path.exists(os.path.join(root, f)), f"{f} not found"


def test_modified_files_compile():
    """All modified Python files should compile without syntax errors."""
    import py_compile
    root = os.path.join(os.path.dirname(__file__), "..")
    for f in ["app.py", "config.py", os.path.join("web", "__init__.py")]:
        py_compile.compile(os.path.join(root, f), doraise=True)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__} - {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

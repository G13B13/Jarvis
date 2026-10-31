import base64, os
import pytest
from conftest import HOME
import worker

H = {"X-API-Key": "test-secret"}

@pytest.fixture
def c():
    return worker.app.test_client()

@pytest.fixture
def popen(monkeypatch):
    calls = []
    monkeypatch.setattr(worker.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    return calls

def test_requires_key(c):
    for method, url in [("post", "/execute"), ("get", "/status"), ("post", "/get_file"),
                        ("post", "/save_file"), ("get", "/screenshot")]:
        assert getattr(c, method)(url, json={}).status_code == 403
        assert getattr(c, method)(url, json={}, headers={"X-API-Key": "wrong"}).status_code == 403

def test_arbitrary_app_is_rejected(c, popen):
    r = c.post("/execute", json={"app": "bash", "command": "nope"}, headers=H)
    assert r.status_code == 400 and popen == []
    r = c.post("/execute", json={"app": "bash"}, headers=H)
    assert r.status_code == 400 and popen == []

def test_allowed_command(c, popen):
    assert c.post("/execute", json={"command": "volume_up"}, headers=H).status_code == 200
    assert popen[0][0] == "pactl"

def test_query_is_encoded(c, popen):
    c.post("/execute", json={"command": "search_google", "query": "a&b #c; rm -rf /"}, headers=H)
    url = popen[0][1]
    assert popen[0][0] == "firefox" and len(popen[0]) == 2
    assert "&b" not in url.split("?q=")[1] and ";" not in url

@pytest.mark.parametrize("q,expected", [("50", "50%"), ("999", "100%"), ("-5", "0%"), ("30%", "30%")])
def test_volume_clamped(c, popen, q, expected):
    c.post("/execute", json={"command": "set_volume", "query": q}, headers=H)
    assert popen[0][-1] == expected

def test_volume_rejects_garbage(c, popen):
    r = c.post("/execute", json={"command": "set_volume", "query": "50; reboot"}, headers=H)
    assert r.status_code == 400 and popen == []

def test_bad_body_not_500(c):
    r = c.post("/execute", data="not json", content_type="text/plain", headers=H)
    assert r.status_code == 400

def test_get_file_allowed(c):
    p = f"{HOME}/Documents/a.txt"; open(p, "w").write("hi")
    r = c.post("/get_file", json={"path": "~/Documents/a.txt"}, headers=H)
    assert r.status_code == 200 and base64.b64decode(r.json["data"]) == b"hi"

@pytest.mark.parametrize("path", [
    "~/.ssh/id_ed25519", "/etc/passwd", "~/Documents/../.ssh/id_ed25519",
    "~/Documents/../../../etc/passwd", "~/Documents/.hidden",
])
def test_get_file_blocked(c, path):
    open(f"{HOME}/.ssh/id_ed25519", "w").write("KEY"); open(f"{HOME}/Documents/.hidden", "w").write("x")
    assert c.post("/get_file", json={"path": path}, headers=H).status_code in (403, 404)

def test_get_file_symlink_escape(c):
    open(f"{HOME}/.ssh/id_ed25519", "w").write("KEY")
    link = f"{HOME}/Documents/link"
    if os.path.lexists(link): os.remove(link)
    os.symlink(f"{HOME}/.ssh/id_ed25519", link)
    assert c.post("/get_file", json={"path": "~/Documents/link"}, headers=H).status_code == 403

def test_get_file_sibling_prefix(c):
    os.makedirs(f"{HOME}/Documents-evil", exist_ok=True); open(f"{HOME}/Documents-evil/x", "w").write("x")
    assert c.post("/get_file", json={"path": "~/Documents-evil/x"}, headers=H).status_code == 403

def test_save_file_traversal_and_no_overwrite(c):
    enc = base64.b64encode(b"data").decode()
    r1 = c.post("/save_file", json={"filename": "../../evil.txt", "data": enc}, headers=H)
    assert r1.status_code == 200 and "evil.txt" in r1.json["message"]
    assert os.path.exists(f"{HOME}/Downloads/evil.txt")
    r2 = c.post("/save_file", json={"filename": "evil.txt", "data": enc}, headers=H)
    assert "evil_1.txt" in r2.json["message"]

def test_save_file_bad_base64(c):
    assert c.post("/save_file", json={"filename": "x", "data": "!!!"}, headers=H).status_code == 400

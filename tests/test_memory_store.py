import json, os, stat, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import memory_store as ms

def test_roundtrip_int_keys(tmp_path):
    p = str(tmp_path / "m.json")
    hist = {123: [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]}
    ms.save(p, hist, "likes tea", 1234.5)
    out = ms.load(p)
    assert out["history"] == hist and out["daily_memory"] == "likes tea" and out["summarized_at"] == 1234.5

def test_missing_and_corrupt_files_give_defaults(tmp_path):
    assert ms.load(str(tmp_path / "nope.json"))["history"] == {}
    bad = tmp_path / "bad.json"; bad.write_text("{not json")
    assert ms.load(str(bad)) == {"history": {}, "daily_memory": "", "summarized_at": 0.0}
    bad.write_text("[1,2,3]")
    assert ms.load(str(bad))["history"] == {}

def test_garbage_entries_are_dropped(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"history": {"7": [{"role": "system", "content": "x"}, {"role": "user", "content": 5},
                                                {"role": "user", "content": "ok"}], "abc": [], "8": "str"},
                             "daily_memory": 99, "summarized_at": "x"}))
    out = ms.load(str(p))
    assert out["history"] == {7: [{"role": "user", "content": "ok"}]}
    assert out["daily_memory"] == "" and out["summarized_at"] == 0.0

def test_history_capped(tmp_path):
    p = str(tmp_path / "m.json")
    msgs = [{"role": "user", "content": str(i)} for i in range(50)]
    ms.save(p, {1: msgs}, "", 0)
    assert [m["content"] for m in ms.load(p)["history"][1]] == [str(i) for i in range(40, 50)]

def test_private_permissions_and_no_temp_leftovers(tmp_path):
    p = str(tmp_path / "m.json")
    ms.save(p, {}, "", 0)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []

def test_failed_save_keeps_old_file_and_cleans_up(tmp_path):
    p = str(tmp_path / "m.json")
    ms.save(p, {}, "keep me", 1)
    class Unserializable: pass
    try:
        ms.save(p, {}, Unserializable(), 2)
    except TypeError:
        pass
    assert ms.load(p)["daily_memory"] == "keep me"
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []

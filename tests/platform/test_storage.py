from app.platform.storage import LocalStorage


def test_local_storage_saves_and_returns_path(tmp_path):
    s = LocalStorage(base_dir=str(tmp_path))
    url = s.save("logos/x.png", b"bytes", "image/png")
    assert url.endswith("logos/x.png")
    assert (tmp_path / "logos" / "x.png").read_bytes() == b"bytes"

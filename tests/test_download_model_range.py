"""T2: unduhan model harus mulai ulang bila server mengabaikan header Range."""
from __future__ import annotations

import io
import urllib.request

import download_model


def test_range_ignored_restarts_instead_of_corrupting(tmp_path, monkeypatch):
    payload = bytes(range(20))
    monkeypatch.setattr(download_model, "CHUNK", 8)

    class _Resp(io.BytesIO):
        def __init__(self, body: bytes, length: int, status: int):
            super().__init__(body)
            self.headers = {"Content-Length": str(length)}
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            self.close()

    def fake_urlopen(req, timeout=60):
        rng = req.get_header("Range")
        if not rng:
            return _Resp(b"", len(payload), 200)
        # Server nakal: membalas 200 dengan seluruh body, mengabaikan Range.
        return _Resp(payload, len(payload), 200)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "model.safetensors"
    part = tmp_path / "model.safetensors.part"
    part.write_bytes(b"Z" * 5)          # sisa unduhan lama yang tidak sinkron

    download_model.fetch("https://example.invalid/model.safetensors",
                         str(out), tries=5)

    assert out.read_bytes() == payload  # dulu: 25 byte (5 + 20) = korup
    assert not part.exists()

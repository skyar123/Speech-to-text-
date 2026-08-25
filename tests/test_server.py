import pytest

from ttskit import audio
from ttskit import server as server_mod

from .conftest import FakeEngine

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture
def client(monkeypatch):
    """A server wired to the offline fake engine."""
    engine = FakeEngine()
    monkeypatch.setattr(server_mod, "get_engine", lambda name=None, **kw: engine)
    app = server_mod.create_app(engine_name="fake", api_key="")
    with fastapi_testclient.TestClient(app) as test_client:
        test_client.engine = engine
        yield test_client


def test_speech_returns_mp3(client):
    response = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "Hello from the server.", "voice": "nova"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert audio.first_frame(response.content) is not None


def test_the_unprefixed_path_works_too(client):
    response = client.post("/audio/speech", json={"input": "Short."})
    assert response.status_code == 200


def test_empty_input_is_an_openai_shaped_error(client):
    response = client.post("/v1/audio/speech", json={"input": "   "})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "input"
    assert body["error"]["type"] == "invalid_request_error"


def test_unknown_response_format_is_rejected(client):
    response = client.post("/v1/audio/speech", json={"input": "hi", "response_format": "midi"})
    assert response.status_code == 400
    assert response.json()["error"]["param"] == "response_format"


def test_out_of_range_speed_is_rejected(client):
    response = client.post("/v1/audio/speech", json={"input": "hi", "speed": 9.0})
    assert response.status_code == 422


def test_speed_becomes_a_rate_string():
    assert server_mod.speed_to_rate(1.0) == "+0%"
    assert server_mod.speed_to_rate(1.25) == "+25%"
    assert server_mod.speed_to_rate(0.5) == "-50%"


def test_long_input_goes_through_the_chunking_pipeline(client):
    long_text = " ".join(["This is a sentence of quite ordinary length."] * 80)
    response = client.post("/v1/audio/speech", json={"input": long_text, "stream": False})
    assert response.status_code == 200
    assert len(client.engine.calls) > 1, "long input should be split into chunks"


def test_models_endpoint_lists_openai_ids(client):
    ids = [item["id"] for item in client.get("/v1/models").json()["data"]]
    assert "tts-1" in ids and "gpt-4o-mini-tts" in ids


def test_voices_endpoint_reports_aliases(client):
    body = client.get("/v1/voices").json()
    assert body["openai_aliases"]["alloy"]
    assert len(body["voices"]) == 2


def test_voices_endpoint_filters_by_locale(client):
    body = client.get("/v1/voices?locale=en-GB").json()
    assert [v["name"] for v in body["voices"]] == ["fake-en-GB-Two"]


def test_healthz_reports_the_environment(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["auth_required"] is False
    assert "mp3" in body["formats"]


def test_playground_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "ttskit" in response.text


def test_api_key_is_enforced_when_configured(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(server_mod, "get_engine", lambda name=None, **kw: engine)
    app = server_mod.create_app(engine_name="fake", api_key="secret-key")
    with fastapi_testclient.TestClient(app) as client:
        assert client.post("/v1/audio/speech", json={"input": "hi"}).status_code == 401
        ok = client.post(
            "/v1/audio/speech",
            json={"input": "hi"},
            headers={"Authorization": "Bearer secret-key"},
        )
        assert ok.status_code == 200

"""Episode service, authentication, configuration and the HTTP surface."""

import anyio
import httpx2
import pytest

from backend import app as app_module
from backend import service as service_module
from backend.auth import Authenticator
from backend.config import ConfigError, Settings, parse_api_keys
from backend.service import EpisodeNotFoundError, EpisodeService, InvalidActionError
from conftest import ALICE, API_KEYS, WINNING_PATH, bearer, sse_events

# ----- service ----------------------------------------------------------------


def test_episodes_have_unique_ids_and_independent_state():
    service = EpisodeService()
    first = service.start_episode("alice")["episode_id"]
    second = service.start_episode("alice")["episode_id"]
    assert first != second
    service.move_agent("alice", first, "right")
    assert service.get_observation("alice", first)["step_count"] == 1
    assert service.get_observation("alice", second)["step_count"] == 0


@pytest.mark.parametrize(
    "operation",
    ["reset_episode", "get_observation", "inspect_environment", "get_episode_status", "move_agent"],
)
def test_another_agent_cannot_access_an_episode(operation):
    service = EpisodeService()
    episode_id = service.start_episode("alice")["episode_id"]
    service.move_agent("alice", episode_id, "right")
    extra = ["down"] if operation == "move_agent" else []

    with pytest.raises(EpisodeNotFoundError):
        getattr(service, operation)("bob", episode_id, *extra)

    assert service.get_observation("alice", episode_id)["step_count"] == 1
    assert service.snapshot("bob")["episodes"] == []
    # The rejected call is visible to the caller only.
    assert [e["ok"] for e in service.snapshot("bob")["log"]] == [False]
    assert all(e["ok"] for e in service.snapshot("alice")["log"])


def test_activity_log_records_calls_arguments_changes_and_rewards():
    service = EpisodeService()
    episode_id = service.start_episode("alice")["episode_id"]
    service.move_agent("alice", episode_id, "right")
    service.get_observation("alice", episode_id)

    start, move, observe = service.snapshot("alice")["log"]
    assert start["tool"] == "start_episode"
    assert start["episode_id"] == episode_id
    assert {c["field"] for c in start["changes"]} == {
        "position",
        "step_count",
        "cumulative_reward",
        "status",
    }
    assert move["arguments"] == {"episode_id": episode_id, "direction": "right"}
    assert move["reward"] == -1
    assert move["result"]["outcome"] == "moved"
    assert {"field": "position", "before": {"row": 0, "col": 0}, "after": {"row": 0, "col": 1}} in (
        move["changes"]
    )
    assert {"field": "cumulative_reward", "before": 0, "after": -1} in move["changes"]
    assert observe["changes"] == []
    assert observe["reward"] is None
    assert start["seq"] < move["seq"] < observe["seq"]


def test_reset_is_logged_as_a_state_change():
    service = EpisodeService()
    episode_id = service.start_episode("alice")["episode_id"]
    service.move_agent("alice", episode_id, "right")
    observation = service.reset_episode("alice", episode_id)
    assert observation["episode_id"] == episode_id
    changes = service.snapshot("alice")["log"][-1]["changes"]
    assert {"field": "step_count", "before": 1, "after": 0} in changes


def test_failed_calls_are_logged_and_change_nothing():
    service = EpisodeService()
    episode_id = service.start_episode("alice")["episode_id"]
    with pytest.raises(InvalidActionError):
        service.move_agent("alice", episode_id, "north")
    entry = service.snapshot("alice")["log"][-1]
    assert not entry["ok"]
    assert "north" in entry["error"]
    assert entry["changes"] == []
    assert service.get_observation("alice", episode_id)["step_count"] == 0


def test_long_arguments_are_clipped_in_the_log():
    service = EpisodeService()
    with pytest.raises(EpisodeNotFoundError):
        service.get_observation("alice", "x" * 10_000)
    entry = service.snapshot("alice")["log"][-1]
    assert len(entry["arguments"]["episode_id"]) < 300
    assert len(entry["error"]) < 400


def test_results_include_the_trajectory():
    service = EpisodeService()
    episode_id = service.start_episode("alice")["episode_id"]
    for direction in WINNING_PATH:
        service.move_agent("alice", episode_id, direction)
    results = service.get_episode_status("alice", episode_id)
    assert results["status"] == "succeeded"
    assert results["done"] and results["reached_goal"]
    assert results["cumulative_reward"] == 9
    assert [t["action"] for t in results["trajectory"]] == WINNING_PATH
    assert sum(t["reward"] for t in results["trajectory"]) == 9


def test_oldest_episode_is_evicted_at_the_limit(monkeypatch):
    monkeypatch.setattr(service_module, "MAX_EPISODES_PER_AGENT", 2)
    service = EpisodeService()
    with service.subscribe("alice") as queue:
        ids = [service.start_episode("alice")["episode_id"] for _ in range(3)]
        events = [queue.get_nowait() for _ in range(3)]
    assert [e["episode_id"] for e in service.snapshot("alice")["episodes"]] == ids[1:]
    assert events[-1]["removed_episode_ids"] == [ids[0]]


def test_subscribers_only_receive_their_own_agents_events():
    service = EpisodeService()
    with service.subscribe("alice") as alice, service.subscribe("bob") as bob:
        service.start_episode("alice")
        assert alice.qsize() == 1
        assert bob.empty()


def test_slow_subscriber_is_told_to_reconnect(monkeypatch):
    monkeypatch.setattr(service_module, "SUBSCRIBER_QUEUE_SIZE", 2)
    service = EpisodeService()
    with service.subscribe("alice") as queue:
        for _ in range(4):
            service.start_episode("alice")
        assert queue.get_nowait() is None
        assert queue.empty()


@pytest.mark.anyio
async def test_idle_stream_sends_keep_alives(monkeypatch):
    monkeypatch.setattr(app_module, "KEEPALIVE_SECONDS", 0.01)
    stream = app_module.event_stream(EpisodeService(), "alice")
    assert '"type": "snapshot"' in await anext(stream)
    assert await anext(stream) == ": keep-alive\n\n"
    await stream.aclose()


# ----- authentication and configuration ---------------------------------------


def test_authenticator_maps_tokens_to_agents():
    authenticator = Authenticator(API_KEYS)
    assert authenticator.authenticate(f"Bearer {ALICE}") == "alice"
    assert authenticator.authenticate(f"bearer {ALICE}") == "alice"
    for header in [None, "", ALICE, f"Basic {ALICE}", "Bearer ", "Bearer wrong", "Bearer é"]:
        assert authenticator.authenticate(header) is None


def test_settings_from_environment():
    settings = Settings.from_env(
        {
            "ROUTESIM_API_KEYS": "claude:aaaaaaaaaaaaaaaaaaaa, opencode:bbbbbbbbbbbbbbbbbbbb",
            "ROUTESIM_HOST": "0.0.0.0",
            "ROUTESIM_PORT": "9000",
        }
    )
    assert list(settings.api_keys) == ["claude", "opencode"]
    assert (settings.host, settings.port) == ("0.0.0.0", 9000)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no-separator-token-value",
        "claude:short",
        "bad name:aaaaaaaaaaaaaaaaaaaa",
        "a:aaaaaaaaaaaaaaaaaaaa,a:bbbbbbbbbbbbbbbbbbbb",
        "a:aaaaaaaaaaaaaaaaaaaa,b:aaaaaaaaaaaaaaaaaaaa",
    ],
)
def test_unsafe_api_key_configuration_is_rejected(raw):
    with pytest.raises(ConfigError):
        parse_api_keys(raw)


def test_configuration_errors_do_not_echo_secrets():
    with pytest.raises(ConfigError) as caught:
        parse_api_keys("supersecretvalue-without-name")
    assert "supersecret" not in str(caught.value)


# ----- HTTP -------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_needs_no_token(live_server):
    async with httpx2.AsyncClient(base_url=live_server.url) as http:
        response = await http.get("/api/health")
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [{}, bearer("wrong-token-wrong-token")])
async def test_stream_and_mcp_require_a_valid_token(live_server, headers):
    async with httpx2.AsyncClient(base_url=live_server.url, headers=headers) as http:
        for response in [
            await http.get("/api/stream"),
            await http.get("/mcp"),
            await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        ]:
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.anyio
async def test_stream_starts_with_a_snapshot_then_sends_live_events(live_server):
    existing = live_server.service.start_episode("alice")["episode_id"]
    async with httpx2.AsyncClient(base_url=live_server.url, timeout=5) as http:
        async with http.stream("GET", "/api/stream", headers=bearer(ALICE)) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            events = sse_events(response)
            snapshot = await anext(events)
    assert snapshot["type"] == "snapshot"
    assert snapshot["agent"] == "alice"
    [episode] = snapshot["episodes"]
    assert episode["episode_id"] == existing
    assert episode["layout"]["grid_size"] == 7
    assert episode["position"] == {"row": 0, "col": 0}
    assert episode["path"] == [{"row": 0, "col": 0}]


@pytest.mark.anyio
async def test_disconnected_dashboard_releases_its_subscription(live_server, monkeypatch):
    monkeypatch.setattr(app_module, "KEEPALIVE_SECONDS", 0.05)
    subscribers = live_server.service._space("alice").subscribers
    async with httpx2.AsyncClient(base_url=live_server.url, timeout=5) as http:
        async with http.stream("GET", "/api/stream", headers=bearer(ALICE)) as response:
            await anext(sse_events(response))
            assert len(subscribers) == 1
    with anyio.fail_after(5):
        while subscribers:
            await anyio.sleep(0.02)


@pytest.mark.anyio
async def test_http_api_offers_no_way_to_change_the_environment(live_server):
    episode_id = live_server.service.start_episode("alice")["episode_id"]
    async with httpx2.AsyncClient(base_url=live_server.url, headers=bearer(ALICE)) as http:
        for path in ["/api/stream", "/api/episodes", f"/api/episodes/{episode_id}/move"]:
            response = await http.post(path, json={"direction": "right"})
            assert response.status_code in (404, 405)
    assert live_server.service.snapshot("alice")["episodes"][0]["step_count"] == 0


@pytest.mark.anyio
async def test_built_dashboard_is_served_without_hiding_the_api(tmp_path):
    (tmp_path / "index.html").write_text("<title>RouteSim</title>", encoding="utf-8")
    app = app_module.create_app(Settings(api_keys=API_KEYS), frontend_dist=tmp_path)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
        page = await http.get("/")
        assert "RouteSim" in page.text
        assert page.headers["cache-control"] == "no-cache"
        unchanged = await http.get("/", headers={"If-None-Match": page.headers["etag"]})
        assert unchanged.status_code == 304
        assert (await http.get("/api/health")).status_code == 200
        assert (await http.get("/mcp")).status_code == 401

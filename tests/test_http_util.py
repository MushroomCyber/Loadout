"""The path every downloaded executable arrives over.

Retry, backoff, the circuit breaker and the offline switch were 30% covered
-- on the code that decides whether a binary lands on someone's machine.
These exercise the decisions, not requests itself: the transport is faked so
the policy around it is what is actually under test.
"""

from __future__ import annotations

import pytest

from loadout import http_util


@pytest.fixture(autouse=True)
def _clean_host_state(monkeypatch):
    """Circuit state is module-global and per host; a test that opens a
    breaker must not decide the next test's outcome."""
    http_util._HOSTS.clear()
    monkeypatch.setattr(http_util.time, "sleep", lambda _s: None)
    monkeypatch.setattr(http_util, "allowed_by_robots", lambda _url: True)
    yield
    http_util._HOSTS.clear()


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _Transport:
    """Stands in for `requests`, counting what was asked of it."""

    class RequestException(Exception):
        pass

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0) if self.outcomes else _Response(200)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install(monkeypatch, transport):
    monkeypatch.setattr(http_util, "requests", transport)
    return transport


class TestWhatIsRetried:
    def test_a_server_error_is_retried_and_the_recovery_returned(self, monkeypatch):
        transport = _install(
            monkeypatch, _Transport(_Response(503), _Response(503), _Response(200))
        )
        response = http_util.polite_get("https://example.com/x")

        assert response is not None
        assert response.status_code == 200
        assert len(transport.calls) == 3

    def test_rate_limiting_is_retried_rather_than_treated_as_an_answer(
        self, monkeypatch
    ):
        """429 is "ask again later", and returning it would surface to the
        caller as a release with no assets."""
        transport = _install(monkeypatch, _Transport(_Response(429), _Response(200)))
        response = http_util.polite_get("https://api.github.com/x")

        assert response is not None
        assert response.status_code == 200
        assert len(transport.calls) == 2

    def test_a_404_is_an_answer_not_a_failure(self, monkeypatch):
        """Retrying a definite "no" three more times just makes the user
        wait for the same result."""
        transport = _install(monkeypatch, _Transport(_Response(404)))
        response = http_util.polite_get("https://example.com/missing")

        assert response is not None
        assert response.status_code == 404
        assert len(transport.calls) == 1

    def test_giving_up_returns_none_rather_than_the_last_error_page(
        self, monkeypatch
    ):
        transport = _install(
            monkeypatch, _Transport(*[_Response(500) for _ in range(4)])
        )
        assert http_util.polite_get("https://example.com/x") is None
        assert len(transport.calls) == 4

    def test_a_connection_error_is_retried_like_a_server_error(self, monkeypatch):
        transport = _Transport()
        transport.outcomes = [
            _Transport.RequestException("connection reset"),
            _Response(200),
        ]
        _install(monkeypatch, transport)
        response = http_util.polite_get("https://example.com/x")

        assert response is not None
        assert len(transport.calls) == 2


class TestWhatIsRefusedOutright:
    def test_offline_mode_makes_no_request_at_all(self, monkeypatch):
        transport = _install(monkeypatch, _Transport(_Response(200)))
        monkeypatch.setenv("LOADOUT_OFFLINE", "1")

        assert http_util.polite_get("https://example.com/x") is None
        assert transport.calls == []

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x"])
    def test_a_non_http_scheme_is_refused(self, monkeypatch, url):
        """This function's callers hand it URLs out of catalog entries and
        API responses; a file:// there must not read a local file."""
        transport = _install(monkeypatch, _Transport(_Response(200)))

        assert http_util.polite_get(url) is None
        assert transport.calls == []

    def test_robots_disallow_stops_the_request(self, monkeypatch):
        transport = _install(monkeypatch, _Transport(_Response(200)))
        monkeypatch.setattr(http_util, "allowed_by_robots", lambda _url: False)

        assert http_util.polite_get("https://example.com/x") is None
        assert transport.calls == []


class TestTheCircuitBreaker:
    def test_a_host_that_keeps_failing_stops_being_asked(self, monkeypatch):
        transport = _install(monkeypatch, _Transport())
        transport.outcomes = [_Response(500)] * 100

        for _ in range(http_util.CIRCUIT_FAIL_THRESHOLD):
            assert http_util.polite_get("https://down.example.com/x") is None
        before = len(transport.calls)

        assert http_util.polite_get("https://down.example.com/x") is None
        assert len(transport.calls) == before

    def test_one_bad_host_does_not_close_the_door_on_another(self, monkeypatch):
        transport = _install(monkeypatch, _Transport())
        transport.outcomes = [_Response(500)] * 100

        for _ in range(http_util.CIRCUIT_FAIL_THRESHOLD):
            http_util.polite_get("https://down.example.com/x")

        transport.outcomes = [_Response(200)]
        response = http_util.polite_get("https://up.example.com/x")
        assert response is not None
        assert response.status_code == 200

    def test_a_success_clears_the_failure_count(self, monkeypatch):
        transport = _install(monkeypatch, _Transport())
        transport.outcomes = [_Response(500)] * 4 + [_Response(200)]

        assert http_util.polite_get("https://flaky.example.com/x") is None
        assert http_util.polite_get("https://flaky.example.com/x") is not None
        assert http_util._host_state("flaky.example.com").fails == 0


class TestHeaders:
    def test_every_request_identifies_the_project(self, monkeypatch):
        transport = _install(monkeypatch, _Transport(_Response(200)))
        http_util.polite_get("https://example.com/x")

        agent = transport.calls[0]["headers"]["User-Agent"]
        assert agent.startswith("loadout/")
        assert "github.com/MushroomCyber/Loadout" in agent

    def test_a_github_token_raises_the_rate_limit_where_it_applies(
        self, monkeypatch
    ):
        transport = _install(monkeypatch, _Transport(_Response(200)))
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        http_util.polite_get("https://api.github.com/repos/x/y")

        assert transport.calls[0]["headers"]["Authorization"] == "Bearer ghp_secret"

    def test_the_token_is_not_sent_to_anyone_else(self, monkeypatch):
        """It is a credential; it goes to the host it authenticates to and
        nowhere the catalog happens to point a download at."""
        transport = _install(monkeypatch, _Transport(_Response(200)))
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        http_util.polite_get("https://objects.githubusercontent.com/asset")

        assert "Authorization" not in transport.calls[0]["headers"]

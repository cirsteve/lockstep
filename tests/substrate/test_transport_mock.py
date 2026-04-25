"""Acceptance tests for MockTransportAdapter."""

from __future__ import annotations

import pytest

from lockstep.substrate.transport import MockTransportAdapter


@pytest.fixture(autouse=True)
def _isolate_transport():
    MockTransportAdapter.reset()
    yield
    MockTransportAdapter.reset()


def test_two_adapters_send_and_receive_in_process():
    a = MockTransportAdapter("alice")
    b = MockTransportAdapter("bob")

    received: list[tuple[str, bytes]] = []
    b.subscribe(lambda src, msg: received.append((src, msg)))

    a.send("bob", b"hello bob")
    assert received == [("alice", b"hello bob")]


def test_peer_id_is_stable_across_calls():
    a = MockTransportAdapter("stable")
    assert a.peer_id() == "stable"
    assert a.peer_id() == "stable"

    auto = MockTransportAdapter()
    pid = auto.peer_id()
    assert pid == auto.peer_id()
    assert pid.startswith("peer_")

"""Tests for ENABLED_ANALYSTS single-analyst gating in Config."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from config import Config


@pytest.fixture
def gated_eva(monkeypatch):
    monkeypatch.setenv("ENABLED_ANALYSTS", "eva")
    # Shadow analysts are polled too — clear them so this fixture tests the
    # execution gate in isolation (see tests/test_shadow_mode.py).
    monkeypatch.setenv("SHADOW_ANALYSTS", "")
    return Config()


@pytest.fixture
def ungated(monkeypatch):
    monkeypatch.setenv("ENABLED_ANALYSTS", "")
    monkeypatch.setenv("SHADOW_ANALYSTS", "")
    return Config()


def test_gating_limits_watched_channels_to_eva(gated_eva):
    if not gated_eva.discord_channel_eva:
        pytest.skip("No Eva channel configured")
    assert gated_eva.watched_channels == [gated_eva.discord_channel_eva]
    assert gated_eva.discord_only_channels == [gated_eva.discord_channel_eva]
    assert set(gated_eva.channel_to_analyst.values()) == {"eva"}


def test_gating_excludes_other_configured_channels(gated_eva):
    for ch in (gated_eva.discord_channel_grizzlies,
               gated_eva.discord_channel_waxui,
               gated_eva.discord_channel_ecs,
               gated_eva.discord_channel_zabes):
        if ch:
            assert ch not in gated_eva.watched_channels
            assert ch not in gated_eva.channel_to_analyst


def test_empty_gate_enables_all_configured_analysts(ungated):
    configured = [ch for ch, _ in ungated._discord_channel_analyst_pairs if ch]
    assert set(ungated.discord_only_channels) == set(configured)


def test_gate_is_case_and_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv("ENABLED_ANALYSTS", " Eva , ECS ")
    cfg = Config()
    assert cfg.enabled_analysts == {"eva", "ecs"}

"""Tests for Robotary server."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add robotary to path
ROBOTARY_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROBOTARY_DIR.parent
sys.path.insert(0, str(ROBOTARY_DIR))


def test_skill_catalog_loads():
    """Skill catalog should discover at least 7 skills from SKILL.md files."""
    from server import build_skill_catalog

    catalog = build_skill_catalog()
    assert len(catalog) >= 7
    # Core skills must be present
    for skill in ["pharmgx-reporter", "nutrigx_advisor", "genome-compare",
                  "gwas-prs", "clinpgx", "gwas-lookup", "profile-report"]:
        assert skill in catalog, f"{skill} not in catalog"
        assert len(catalog[skill]) > 0  # has a description


def test_skill_catalog_descriptions_are_strings():
    from server import build_skill_catalog

    catalog = build_skill_catalog()
    for name, desc in catalog.items():
        assert isinstance(desc, str)
        assert len(desc) <= 200


@pytest.mark.asyncio
async def test_route_query_returns_skill_and_params():
    """Route function should return skill name, confidence, and params."""
    mock_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "skill": "pharmgx-reporter",
                    "confidence": 0.94,
                    "reasoning": "User asks about drug interactions",
                    "params": {},
                })
            }
        }]
    }

    with patch("server.llm_chat", new_callable=AsyncMock, return_value=mock_response):
        from server import route_query
        result = await route_query("What drugs should I watch out for?")
        assert result["skill"] == "pharmgx-reporter"
        assert result["confidence"] >= 0.0
        assert "params" in result


@pytest.mark.asyncio
async def test_route_query_extracts_params():
    """Route function should extract skill-specific params from query."""
    mock_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "skill": "gwas-prs",
                    "confidence": 0.91,
                    "reasoning": "User asks about diabetes risk",
                    "params": {"trait": "type 2 diabetes"},
                })
            }
        }]
    }

    with patch("server.llm_chat", new_callable=AsyncMock, return_value=mock_response):
        from server import route_query
        result = await route_query("What's my risk for type 2 diabetes?")
        assert result["skill"] == "gwas-prs"
        assert result["params"]["trait"] == "type 2 diabetes"


@pytest.mark.asyncio
async def test_route_query_no_match():
    """Route function should return null skill when no match."""
    mock_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "skill": None,
                    "confidence": 0.0,
                    "reasoning": "Not a bioinformatics question",
                    "params": {},
                })
            }
        }]
    }

    with patch("server.llm_chat", new_callable=AsyncMock, return_value=mock_response):
        from server import route_query
        result = await route_query("What's the weather today?")
        assert result["skill"] is None


def test_build_skill_command_genome_skill():
    """Genome-based skills should use --input with Manuel Corpas genome."""
    from server import build_skill_command, GENOME_PATH

    cmd, tmpdir = build_skill_command("pharmgx-reporter", {})
    assert sys.executable in cmd[0] or "python" in cmd[0]
    assert "--input" in cmd
    assert str(GENOME_PATH) in cmd
    assert "--output" in cmd
    assert tmpdir is not None


def test_build_skill_command_demo_skill():
    """API-based skills should use --demo flag."""
    from server import build_skill_command

    cmd, tmpdir = build_skill_command("clinpgx", {"gene": "CYP2D6"})
    assert "--gene" in cmd
    assert "CYP2D6" in cmd
    assert "--output" in cmd


def test_build_skill_command_gwas_prs_with_trait():
    """gwas-prs should include --trait from params."""
    from server import build_skill_command

    cmd, tmpdir = build_skill_command("gwas-prs", {"trait": "type 2 diabetes"})
    assert "--input" in cmd
    assert "--trait" in cmd
    assert "type 2 diabetes" in cmd


def test_build_skill_command_gwas_lookup_with_rsid():
    """gwas-lookup should include --rsid from params."""
    from server import build_skill_command

    cmd, tmpdir = build_skill_command("gwas-lookup", {"rsid": "rs3798220"})
    assert "--rsid" in cmd
    assert "rs3798220" in cmd


def test_build_skill_command_profile_report():
    """profile-report should use --demo."""
    from server import build_skill_command

    cmd, tmpdir = build_skill_command("profile-report", {})
    assert "--demo" in cmd


def test_build_interpret_messages():
    """Interpret messages should include query, skill name, report, and disclaimer."""
    from server import build_interpret_messages

    messages = build_interpret_messages(
        query="What drugs interact with CYP2D6?",
        skill="pharmgx-reporter",
        report="## PharmGx Report\nCYP2D6: *1/*4 — Intermediate Metabolizer",
    )
    system_msg = messages[0]["content"]
    assert "robot terry" in system_msg.lower()
    assert "not a medical device" in system_msg.lower() or "not medical advice" in system_msg.lower()
    user_msg = messages[1]["content"]
    assert "CYP2D6" in user_msg
    assert "pharmgx-reporter" in user_msg


def test_build_interpret_messages_truncates_long_report():
    """Reports longer than 8000 chars should be truncated."""
    from server import build_interpret_messages

    long_report = "x" * 10000
    messages = build_interpret_messages("test", "test-skill", long_report)
    user_msg = messages[1]["content"]
    assert len(user_msg) < 10000
    assert "truncated" in user_msg.lower()


def test_websocket_pipeline():
    """WebSocket endpoint should accept connections and stream brain events through all 4 stages."""
    from starlette.testclient import TestClient

    from server import app

    mock_route = {
        "skill": "pharmgx-reporter",
        "confidence": 0.94,
        "reasoning": "Drug interactions",
        "params": {},
    }
    mock_result = {
        "success": True,
        "report": "## Report\nCYP2D6: Normal Metabolizer",
        "result_json": None,
        "stdout": "",
        "stderr": "",
    }

    with patch("server.route_query", new_callable=AsyncMock, return_value=mock_route), \
         patch("server.run_skill", new_callable=AsyncMock, return_value=mock_result), \
         patch("server.stream_interpret", new_callable=AsyncMock):

        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "message", "content": "What drugs should I worry about?"})

            # Collect all messages until we get a chat done
            messages = []
            for _ in range(20):  # safety limit
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "chat" and msg.get("done"):
                    break

            brain_msgs = [m for m in messages if m["type"] == "brain"]
            assert len(brain_msgs) >= 4  # route, load, run, interpret stages
            assert brain_msgs[0]["stage"] == "route"

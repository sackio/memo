"""Claude Code transcript log queries. [001/FR-033]

The safety rules matter as much as the results here: this module shells out to
grep against multi-MB transcripts on a host where the shell snapshot shims
`grep` to a tree-scanner that once ate 17 GB of RAM.
"""
import json

import pytest

from memo import log_queries


@pytest.fixture
def fake_projects(tmp_path):
    """A transcript tree shaped like ~/.claude/projects."""
    proj = tmp_path / log_queries.mangle_cwd("/home/ben/code/memo")
    proj.mkdir(parents=True)
    t = proj / "abc-123.jsonl"
    lines = []
    for i in range(50):
        lines.append(json.dumps({
            "type": "user" if i % 2 else "assistant",
            "timestamp": 1_800_000_000 + i,
            "message": {"role": "user" if i % 2 else "assistant",
                        "content": f"line {i} ordinary chatter"},
        }))
    lines[20] = json.dumps({
        "type": "assistant", "timestamp": 1_800_000_020,
        "message": {"role": "assistant",
                    "content": "the barn control-plane is 192.168.1.243"},
    })
    t.write_text("\n".join(lines) + "\n")
    return tmp_path


def test_mangle_cwd():
    assert log_queries.mangle_cwd("/home/ben/code/memo") == "-home-ben-code-memo"


@pytest.mark.asyncio
async def test_finds_a_match_with_line_range(fake_projects):
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="abc-123",
                                pattern="control-plane",
                                projects_dir=fake_projects)
    assert r["match_count"] == 1
    m = r["matches"][0]
    assert m["matched_line"] == 21          # 1-indexed
    assert m["line_start"] < m["matched_line"] < m["line_end"]
    assert "192.168.1.243" in m["matched_text"]


@pytest.mark.asyncio
async def test_returns_a_provenance_template(fake_projects):
    """The point of this tool: a memo can cite the exact lines it came from."""
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="abc-123", pattern="control-plane",
                                host="server4", projects_dir=fake_projects)
    ref = r["provenance_template"]["claude_log_ref"]
    assert ref["host"] == "server4"
    assert ref["session_uuid"] == "abc-123"
    assert isinstance(ref["line_range_start"], int)


@pytest.mark.asyncio
async def test_no_matches_is_not_an_error(fake_projects):
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="abc-123", pattern="zzz-nothing",
                                projects_dir=fake_projects)
    assert r["match_count"] == 0
    assert r["matches"] == []


@pytest.mark.asyncio
async def test_max_matches_is_enforced(fake_projects):
    """Unbounded matches on a 40 MB transcript is a memory problem, not a UX one."""
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="abc-123", pattern="ordinary",
                                max_matches=5, projects_dir=fake_projects)
    assert r["match_count"] == 5
    assert r["truncated"] is True


@pytest.mark.asyncio
async def test_max_matches_hard_cap(fake_projects):
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="abc-123", pattern="line",
                                max_matches=10_000, projects_dir=fake_projects)
    assert r["max_matches"] == log_queries.HARD_MAX_MATCHES


@pytest.mark.asyncio
async def test_missing_transcript_is_a_clean_error(fake_projects):
    with pytest.raises(log_queries.LogQueryError):
        await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="does-not-exist", pattern="x",
                                projects_dir=fake_projects)


@pytest.mark.asyncio
async def test_empty_pattern_rejected(fake_projects):
    with pytest.raises(log_queries.LogQueryError):
        await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="abc-123", pattern="  ",
                                projects_dir=fake_projects)


@pytest.mark.asyncio
async def test_invalid_regex_rejected(fake_projects):
    with pytest.raises(log_queries.LogQueryError):
        await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="abc-123", pattern="[unclosed",
                                projects_dir=fake_projects)


@pytest.mark.asyncio
async def test_overlong_pattern_rejected(fake_projects):
    """Long bounded-alternation patterns are what made the shimmed scanner
    walk all of $HOME and take the office host down on 2026-07-22."""
    with pytest.raises(log_queries.LogQueryError, match="too long"):
        await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="abc-123",
                                pattern="x" * 500, projects_dir=fake_projects)


@pytest.mark.asyncio
async def test_list_sessions(fake_projects):
    out = await log_queries.list_sessions("/home/ben/code/memo",
                                          projects_dir=fake_projects)
    assert len(out) == 1
    assert out[0]["session_uuid"] == "abc-123"
    assert out[0]["size_bytes"] > 0


@pytest.mark.asyncio
async def test_list_sessions_unknown_project(fake_projects):
    assert await log_queries.list_sessions("/nope", projects_dir=fake_projects) == []

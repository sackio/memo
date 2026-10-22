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


# ── Claude Code 2.1.300's API-request records ──────────────────────────────
#
# 2.1.300 writes `api-request-shape`, `api-request-blob` and `api-request` into
# every transcript: the system prompt, tool schemas and request parameters, the
# same bytes in every session on the fleet. Every other transcript reader here
# allow-lists user/assistant and drops them for free; a raw grep does not, so
# these pin the exclusion.


@pytest.fixture
def api_request_projects(tmp_path):
    """A transcript where the pattern is in the system prompt AND in one message.

    200 `api-request` records ahead of the conversation is the realistic worst
    case, not a contrived one: one is written per request, and a pattern like a
    tool name or anything quoted from CLAUDE.md appears in all of them.
    """
    proj = tmp_path / log_queries.mangle_cwd("/home/ben/code/memo")
    proj.mkdir(parents=True)
    lines = [
        json.dumps({"type": "api-request-blob",
                    "blob": "CLAUDE.md qwen3-embedding " + "x" * 87_000}),
        json.dumps({"type": "api-request-shape",
                    "tools": [{"name": "memo_store", "desc": "qwen3-embedding"}]}),
    ]
    lines += [json.dumps({"type": "api-request",
                          "params": {"note": "qwen3-embedding"}})
              for _ in range(200)]
    lines.append(json.dumps({
        "type": "user", "timestamp": "2026-10-15T12:05:00Z",
        "message": {"role": "user",
                    "content": "did we move qwen3-embedding to 2560 dims?"}}))
    lines.append(json.dumps({
        "type": "assistant", "timestamp": "2026-10-15T12:05:10Z",
        "message": {"role": "assistant", "content": [
            {"type": "text",
             "text": 'a quoted literal "type":"api-request" and qwen3-embedding'}]}}))
    (proj / "api-1.jsonl").write_text("\n".join(lines) + "\n")
    return tmp_path


@pytest.mark.asyncio
async def test_api_request_records_never_reach_the_caller(api_request_projects):
    """The whole point: 202 machinery hits must not crowd out the 2 real ones.

    Before the exclusion this returned 20 matches, all of them lines 1-20 —
    i.e. a provenance citation pointing at the system prompt.
    """
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="api-1",
                                pattern="qwen3-embedding",
                                max_matches=20,
                                projects_dir=api_request_projects)
    assert [m["matched_line"] for m in r["matches"]] == [203, 204]
    assert {m["role"] for m in r["matches"]} == {"user", "assistant"}
    assert r["truncated"] is False


@pytest.mark.asyncio
async def test_a_message_quoting_the_type_string_is_kept(api_request_projects):
    """Line 204 says `"type":"api-request"` inside its text. It is conversation.

    The exclusion is bounded to the head of the line for exactly this: a seat
    discussing these records must not delete itself from its own transcript.
    """
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="api-1",
                                pattern="quoted literal",
                                max_matches=5,
                                projects_dir=api_request_projects)
    assert r["match_count"] == 1
    assert r["matches"][0]["role"] == "assistant"


@pytest.mark.asyncio
async def test_excerpt_is_not_swallowed_by_an_adjacent_blob(tmp_path):
    """An 87 KB blob two lines away must not consume the 2000-char excerpt.

    Otherwise the citation renders without the line it cites — the one thing
    the excerpt exists to show.
    """
    proj = tmp_path / log_queries.mangle_cwd("/home/ben/code/memo")
    proj.mkdir(parents=True)
    (proj / "api-2.jsonl").write_text("\n".join([
        json.dumps({"type": "api-request-shape", "tools": []}),
        json.dumps({"type": "api-request-blob", "blob": "z" * 87_000}),
        json.dumps({"type": "user", "timestamp": "2026-10-15T12:05:00Z",
                    "message": {"role": "user", "content": "the needle is here"}}),
    ]) + "\n")
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="api-2", pattern="needle",
                                max_matches=5, projects_dir=tmp_path)
    assert r["match_count"] == 1
    excerpt = r["matches"][0]["excerpt"]
    assert "the needle is here" in excerpt
    assert "record elided" in excerpt
    assert "zzzz" not in excerpt
    # The RANGE still spans what is on disk; only the rendering is elided.
    assert (r["matches"][0]["line_start"], r["matches"][0]["line_end"]) == (1, 5)


@pytest.mark.asyncio
async def test_truncated_is_measured_not_inferred(api_request_projects):
    """`truncated` used to mean "the window happened to fill", which is an
    inference. The pipeline fetches one extra line so it is now a measurement.
    """
    r = await log_queries.query(project_dir="/home/ben/code/memo",
                                session_uuid="api-1",
                                pattern="qwen3-embedding",
                                max_matches=1,
                                projects_dir=api_request_projects)
    assert r["match_count"] == 1
    assert r["truncated"] is True

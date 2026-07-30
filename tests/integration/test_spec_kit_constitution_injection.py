"""spec-kit constitution reaches additionalContext. [001/FR-016]

Claude Code does NOT auto-load `.specify/memory/constitution.md` (Agent G's
finding) — which is the entire reason memo injects it.
"""
import pytest

from memo.injection import set as inj

BODY = "# Memo Constitution\n\nPrinciple I: provenance or it did not happen.\n"


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "proj" / "nested" / "deep"
    d.mkdir(parents=True)
    c = tmp_path / "proj" / ".specify" / "memory"
    c.mkdir(parents=True)
    (c / "constitution.md").write_text(BODY)
    return tmp_path / "proj", d


def test_found_at_project_root(project):
    root, _ = project
    path, content = inj.find_spec_kit_constitution(str(root))
    assert path and content == BODY


def test_found_by_walking_up_from_a_subdir(project):
    """A session started deep in the tree still gets its project's constitution."""
    _, deep = project
    path, content = inj.find_spec_kit_constitution(str(deep))
    assert content == BODY


def test_absent_when_no_constitution(tmp_path):
    assert inj.find_spec_kit_constitution(str(tmp_path)) == (None, None)


def test_absent_for_missing_cwd():
    assert inj.find_spec_kit_constitution(None) == (None, None)


def test_walk_is_bounded(tmp_path):
    """Bounded so a deep cwd cannot walk to / and read something unrelated."""
    deep = tmp_path
    for i in range(inj.CONSTITUTION_MAX_WALK + 4):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    c = tmp_path / ".specify" / "memory"
    c.mkdir(parents=True)
    (c / "constitution.md").write_text(BODY)
    assert inj.find_spec_kit_constitution(str(deep)) == (None, None)


@pytest.mark.asyncio
async def test_constitution_appears_in_the_injection_set_and_render(project):
    root, _ = project
    payload = await inj.build(session_id="dojo", agent_family="dojo",
                              cwd=str(root), use_cache=False)
    assert payload["spec_kit_constitution_content"] == BODY
    text = inj.render(payload)
    assert "## Constitution (spec-kit)" in text
    assert "provenance or it did not happen" in text

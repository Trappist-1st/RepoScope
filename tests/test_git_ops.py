from app.ingestion.git_ops import ensure_repo, stable_repo_id
from tests.conftest import SAMPLE_REPO


def test_ensure_local_repo():
    checkout = ensure_repo(str(SAMPLE_REPO))
    assert checkout.local_path == SAMPLE_REPO.resolve()
    assert checkout.repo_id == stable_repo_id(str(SAMPLE_REPO.resolve()))
    assert len(checkout.repo_id) == 16


def test_stable_repo_id_is_deterministic():
    a = stable_repo_id("https://github.com/foo/bar.git")
    b = stable_repo_id("https://github.com/foo/bar")
    assert a == b

from tools import audit_git


def test_private_paths_are_forbidden():
    assert audit_git.forbidden("config.yaml")
    assert audit_git.forbidden(".local/secrets/byr.enc")
    assert audit_git.forbidden("secrets/master.key")
    assert audit_git.forbidden("data/insight.sqlite3")
    assert audit_git.forbidden("notes.log")
    assert not audit_git.forbidden("core/secrets.py")
    assert not audit_git.forbidden("docs/DEPLOY.md")


def test_secret_module_is_not_treated_as_a_secret_file():
    # A source file describing secrets is legitimate; only its runtime data is private.
    assert not audit_git.forbidden("core/secrets.py")
    assert not audit_git.forbidden("tests/test_secrets.py")

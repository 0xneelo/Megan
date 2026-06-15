from megan.monitor.registry import is_read_only
from megan.monitor.ssh import _parse_alias


def test_read_only_allows_inspection():
    assert is_read_only("tmux capture-pane -p -t agent")
    assert is_read_only("git -C ~/proj status --short")
    assert is_read_only("tail -n 50 ~/proj/.agent/log")


def test_read_only_blocks_mutations():
    assert not is_read_only("git push origin main")
    assert not is_read_only("rm -rf /tmp/x")
    assert not is_read_only("systemctl restart app")
    assert not is_read_only("echo hi > file")
    assert not is_read_only("pip install requests")


def test_parse_alias_variants():
    assert _parse_alias("user@1.2.3.4") == ("1.2.3.4", "user", 22)
    assert _parse_alias("user@host:2222") == ("host", "user", 2222)
    assert _parse_alias("host") == ("host", None, 22)

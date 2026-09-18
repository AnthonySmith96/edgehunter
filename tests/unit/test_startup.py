from edgehunter.ops import cli


def test_startup_correlates_launch_when_windows_redirector_has_different_pid(tmp_path, monkeypatch):
    launched = {}

    class Redirector:
        pid = 100

        def __init__(self, command, **kwargs):
            launched.update(kwargs["env"])

        def poll(self):
            return None

    reads = []

    def status(state):
        reads.append(state)
        return {"pid": 101, "state": "OBSERVING", "launch_id": (
            "previous-launch" if len(reads) == 1 else launched["EDGEHUNTER_LAUNCH_ID"])}

    monkeypatch.setattr(cli.subprocess, "Popen", Redirector)
    monkeypatch.setattr(cli, "read_status", status)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    args = cli.parser().parse_args(["--root", str(tmp_path), "start"])
    result = cli.start_background(args)
    assert result["pid"] == 101 and result["state"] == "OBSERVING"
    assert len(reads) == 2

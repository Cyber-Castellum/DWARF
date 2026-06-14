from profile_manager.config import DeploymentConfig
from profile_manager.remote import render_ssh_command


def test_render_ssh_command_resolves_container_key_path_on_host(tmp_path, monkeypatch):
    host_home = tmp_path / "nigel"
    host_key = host_home / ".ssh" / "cardano-box"
    host_key.parent.mkdir(parents=True)
    host_key.write_text("private key placeholder", encoding="utf-8")
    monkeypatch.setenv("HOME", str(host_home))

    config = DeploymentConfig.from_dict({
        "ssh_user": "nigel",
        "host": "192.168.30.16",
        "ssh_key_path": "/home/dwarf/.ssh/cardano-box",
    })

    argv = render_ssh_command(config, "true")

    assert argv[argv.index("-i") + 1] == str(host_key)

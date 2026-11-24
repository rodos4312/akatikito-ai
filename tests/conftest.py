"""Local tests must never reach a billable provider or another external service."""
import ipaddress
import socket

import pytest


@pytest.fixture(autouse=True)
def local_network_only(monkeypatch):
    connect,connect_ex=socket.socket.connect,socket.socket.connect_ex

    def check(address):
        if isinstance(address,tuple):
            host=str(address[0])
            if host.casefold()=="localhost":
                return
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return
            except ValueError:
                pass
            raise RuntimeError("External network disabled in local tests; use a scripted model or loopback fixture.")

    def guarded(self,address):
        check(address)
        return connect(self,address)

    def guarded_ex(self,address):
        check(address)
        return connect_ex(self,address)

    monkeypatch.setattr(socket.socket,"connect",guarded)
    monkeypatch.setattr(socket.socket,"connect_ex",guarded_ex)


@pytest.fixture(autouse=True)
def isolate_app_data(monkeypatch, tmp_path_factory):
    """Ensure tests NEVER read or write the owner's real data/ directory."""
    from akatikito import agenda as agenda_mod
    from akatikito import doctor as doctor_mod
    from akatikito import memory as memory_mod
    from akatikito import settings as settings_mod

    data_dir = tmp_path_factory.mktemp("akatikito_data")
    mem_dir = data_dir / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)

    settings_file = data_dir / "settings.json"
    agenda_file = data_dir / "agenda.json"

    monkeypatch.setenv("AKATIKITO_SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("AKATIKITO_MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(settings_mod, "DEFAULT_PATH", settings_file)
    monkeypatch.setattr(doctor_mod, "DEFAULT_PATH", settings_file)
    monkeypatch.setattr(agenda_mod, "DEFAULT_PATH", agenda_file)
    monkeypatch.setattr(memory_mod, "DIR", mem_dir)

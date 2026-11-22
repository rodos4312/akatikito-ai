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

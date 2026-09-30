"""Ensure running the suite cannot read real connector configuration or call ERP."""
import socket
import dotenv
import pytest


def test_dotenv_does_not_load_real_or_temporary_secrets(tmp_path,monkeypatch):
    monkeypatch.delenv('INTELLIGENCE_TEST_SECRET',raising=False)
    path=tmp_path/'test.env';path.write_text('INTELLIGENCE_TEST_SECRET=synthetic\n')
    assert dotenv.load_dotenv(path) is False
    import os
    assert 'INTELLIGENCE_TEST_SECRET' not in os.environ


@pytest.mark.parametrize('operation',['dns','connect','connect_ex','sendto'])
def test_external_network_is_blocked_before_io(operation):
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM if operation=='sendto' else socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError,match='Tests prohibit external'):
            if operation=='dns':socket.getaddrinfo('example.invalid',443)
            elif operation=='sendto':sock.sendto(b'synthetic',('192.0.2.1',53))
            else:getattr(sock,operation)(('192.0.2.1',443))

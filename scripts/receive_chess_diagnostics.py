#!/usr/bin/env python3
"""Temporary Tailscale-only delivery of a diagnostic and receipt of one report."""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_REPORT_BYTES = 40_000_000


def allowed_bind(value):
    address = ipaddress.ip_address(value)
    if not (address.is_loopback or address in ipaddress.ip_network('100.64.0.0/10')):
        raise ValueError('Bind must be loopback or a Tailscale IPv4 address')
    return value


def render_script(base_url):
    collector = (ROOT / 'scripts/windows/collect_chess_diagnostics.py').read_bytes()
    template = (ROOT / 'scripts/windows/run_chess_diagnostics.ps1').read_text(encoding='utf-8')
    return template.replace('__RECEIVER__', base_url).replace(
        '__COLLECTOR_BASE64__', base64.b64encode(collector).decode('ascii')).encode('utf-8')


def make_handler(peer, token, script, destination):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(60)

        def log_message(self, *args):
            pass  # Do not log the capability URL or request content.

        def respond(self, code, content, content_type='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)

        def authorized(self, suffix):
            return self.client_address[0] == peer and self.path == '/' + token + suffix

        def do_GET(self):
            if not self.authorized('/collect.ps1'):
                self.respond(404, b'{}')
                return
            progress = destination.parent / 'progress.json'
            progress.write_text(json.dumps({'stage': 'script_requested', 'at_epoch': time.time()}), encoding='utf-8')
            os.chmod(progress, 0o600)
            self.respond(200, script, 'text/plain; charset=utf-8')

        def do_POST(self):
            if not self.authorized('/report'):
                self.respond(404, b'{}')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_REPORT_BYTES:
                    self.respond(413, b'{}')
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError('incomplete body')
                report = json.loads(body)
                if not isinstance(report, dict) or report.get('schema_version') != 'chess-diagnostic-bundle/2':
                    raise ValueError('unexpected report')
                # The payload is evidence, not executable code or instructions.
                with destination.open('x', encoding='utf-8') as stream:
                    os.chmod(destination, 0o600)
                    json.dump(report, stream, ensure_ascii=False, indent=2)
            except FileExistsError:
                self.respond(409, b'{}')
                return
            except (ValueError, UnicodeError, TimeoutError):
                self.respond(400, b'{}')
                return
            self.server.report_received = True
            self.respond(200, b'{"status":"received"}')
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', required=True, type=allowed_bind)
    parser.add_argument('--peer', required=True, type=allowed_bind)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--ttl-seconds', type=int, default=3600)
    args = parser.parse_args()
    if not 60 <= args.ttl_seconds <= 3600:
        parser.error('TTL must be between 60 and 3600 seconds')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(args.output_dir, 0o700)
    token = secrets.token_urlsafe(24)
    # Construct the handler after the OS assigns an available private port.
    server = HTTPServer((args.bind, 0), BaseHTTPRequestHandler)
    base_url = f'http://{args.bind}:{server.server_port}/{token}'
    destination = args.output_dir / 'report.json'
    server.RequestHandlerClass = make_handler(args.peer, token, render_script(base_url), destination)
    server.timeout = 1
    server.report_received = False
    session = {'script_url': base_url + '/collect.ps1', 'report_path': str(destination.resolve()),
               'expires_at_epoch': time.time() + args.ttl_seconds, 'allowed_peer': args.peer,
               'receiver_pid': os.getpid()}
    session_path = args.output_dir / 'session.json'
    session_path.write_text(json.dumps(session, indent=2), encoding='utf-8')
    os.chmod(session_path, 0o600)
    print(json.dumps(session), flush=True)
    try:
        while time.time() < session['expires_at_epoch'] and not server.report_received:
            server.handle_request()
    finally:
        server.server_close()
    print(json.dumps({'status': 'received' if server.report_received else 'expired',
                      'report_path': str(destination.resolve())}), flush=True)


if __name__ == '__main__':
    main()

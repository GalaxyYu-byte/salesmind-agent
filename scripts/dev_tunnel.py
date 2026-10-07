"""用忽略的 .env.ssh 为本机 MySQL/Redis 建立 SSH 隧道。"""

import os
import select
import socketserver
import threading
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    configuration = {**dotenv_values(ROOT / ".env.ssh"), **os.environ}
    host = configuration.get("SALESMIND_SSH_HOST")
    username = configuration.get("SALESMIND_SSH_USER")
    password = configuration.get("SALESMIND_SSH_PASSWORD")
    if not host or not username or not password:
        print("SSH configuration missing: set host/user/password in .env.ssh.")
        return 1

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts = ROOT / ".env.ssh-known-hosts"
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    # 仅使用已核对的主机密钥；首次连接可先使用交互式 ssh 确认主机。
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    servers = []
    try:
        client.connect(
            host,
            username=username,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=10,
            auth_timeout=10,
            banner_timeout=10,
        )
        transport = client.get_transport()
        transport.set_keepalive(30)

        class ForwardServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        def make_handler(port):
            class ForwardHandler(socketserver.BaseRequestHandler):
                def handle(self):
                    try:
                        channel = transport.open_channel(
                            "direct-tcpip",
                            ("127.0.0.1", port),
                            self.client_address,
                            timeout=5,
                        )
                    except (paramiko.SSHException, OSError, EOFError):
                        return
                    if channel is None:
                        return
                    try:
                        while transport.is_active():
                            readable, _, _ = select.select(
                                [self.request, channel], [], [], 1
                            )
                            if self.request in readable:
                                data = self.request.recv(65536)
                                if not data:
                                    break
                                channel.sendall(data)
                            if channel in readable:
                                data = channel.recv(65536)
                                if not data:
                                    break
                                self.request.sendall(data)
                    except OSError:
                        pass
                    finally:
                        channel.close()

            return ForwardHandler

        for port in (3307, 6380):
            server = ForwardServer(("127.0.0.1", port), make_handler(port))
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print(
            "SSH tunnel ready: localhost:3307 (MySQL), localhost:6380 (Redis).",
            flush=True,
        )
        while transport.is_active():
            threading.Event().wait(1)
        print("SSH connection closed.")
        return 1
    except KeyboardInterrupt:
        return 0
    except (paramiko.SSHException, OSError, EOFError, ValueError) as error:
        # SSH 异常类型足够定位认证/主机/端口问题，不输出含凭据的异常消息。
        print(f"SSH tunnel failed: {type(error).__name__}.")
        return 1
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

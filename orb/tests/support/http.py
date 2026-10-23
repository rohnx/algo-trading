"""A bytes-only socket for exercising the real HTTP handler without a server."""

import io

from orb.orb_monitor import MonitorHTTPHandler


class Connection:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = io.BytesIO()

    def makefile(self, *_args, **_kwargs):
        return self.request

    def sendall(self, value):
        self.response.write(value)


def request(hub, content):
    connection = Connection(content)
    MonitorHTTPHandler(connection, ("127.0.0.1", 1234), None, hub=hub)
    headers, body = connection.response.getvalue().split(b"\r\n\r\n", 1)
    return int(headers.split()[1]), body

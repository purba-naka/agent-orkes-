import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

Resolver = Callable[[str, int], Awaitable[list[str]]]
_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}


class NetworkPolicyError(ValueError):
    pass


async def resolve_addresses(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(record[4][0]).split("%", 1)[0] for record in records})


class NetworkPolicy:
    def __init__(
        self,
        local_allowlist: set[str] | frozenset[str] | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.local_allowlist = frozenset(
            host.rstrip(".").lower() for host in (local_allowlist or set())
        )
        self.resolver = resolver or resolve_addresses

    async def validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise NetworkPolicyError("Only absolute HTTP and HTTPS URLs are permitted")
        if parsed.username is not None or parsed.password is not None:
            raise NetworkPolicyError("URL credentials are not permitted")

        host = parsed.hostname.rstrip(".").lower()
        if host in self.local_allowlist:
            return

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = await self.resolver(host, port)
        except (OSError, socket.gaierror) as exc:
            raise NetworkPolicyError("Target hostname could not be resolved") from exc
        if not addresses:
            raise NetworkPolicyError("Target hostname did not resolve to an address")

        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise NetworkPolicyError("Target resolved to an invalid address") from exc
            if (
                ip in _METADATA_ADDRESSES
                or ip.is_loopback
                or ip.is_private
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
                or ip.is_reserved
            ):
                raise NetworkPolicyError("Target resolves to a prohibited network address")

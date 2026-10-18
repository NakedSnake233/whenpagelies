"""Read the project's site configuration; optionally check all host ports."""
import re
import socket
import sys
from pathlib import Path
import yaml

try:
    site = sys.argv[1]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", site):
        raise ValueError("invalid site name")
    cfg = yaml.safe_load((Path(__file__).parent / "sites.yaml").read_text())["sites"][site]
    if cfg["container"] != f"osd-{site}":
        raise ValueError("container must be named osd-<site>")
    ports = []
    for mapping in cfg["ports"]:
        if not re.fullmatch(r"\d+:\d+", mapping):
            raise ValueError(f"unsupported port mapping: {mapping}")
        host, target = map(int, mapping.split(":"))
        if not (0 < host < 65536 and 0 < target < 65536):
            raise ValueError("invalid port number")
        ports.append((host, target))
    main = next(host for host, target in ports if target != 8877)
    envctrl = next(host for host, target in ports if target == 8877)
    if len(sys.argv) > 2 and sys.argv[2] == "check-ports":
        sockets = []
        for host, _ in ports:
            for family, address in [(socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")]:
                if family == socket.AF_INET6 and not socket.has_ipv6:
                    continue
                sock = socket.socket(family, socket.SOCK_STREAM)
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                try:
                    sock.bind((address, host))
                except OSError as exc:
                    raise ValueError(f"host port {host} is unavailable ({exc}); aborting osd-{site}") from None
                sockets.append(sock)
    else:
        print(cfg["image"])
        print(main)
        print(envctrl)
        print(*cfg["ports"], sep="\n")
except (KeyError, ValueError, StopIteration, IndexError) as exc:
    sys.exit(f"Site configuration error: {exc}")

"""Packet capture backends.

Three backends share one interface:

``RawSocketCapture``
    Live capture using only the standard library - ``AF_PACKET`` on Linux,
    a promiscuous raw socket on Windows. Requires elevated privileges.
``ScapyCapture``
    Live capture via scapy/libpcap when installed, which pushes filtering into
    the kernel BPF engine and so survives much higher packet rates.
``ReplayCapture``
    Offline replay of pre-built records. Requires no privileges and is used by
    the tests and the ``simulate`` command. Anything it feeds is flagged
    ``simulated`` all the way to the dashboard, so a demo can never be mistaken
    for a live reading.

Two safety properties hold across the live backends:

* **Header-only.** Frames are received into a buffer of ``snaplen`` bytes, so
  payload beyond the transport header is truncated by the kernel and never
  enters the process. Byte volume comes from the IP header's length field.
* **Least privilege.** The socket is opened first and privileges are dropped
  immediately afterwards where the platform supports it, so the long-running
  capture loop does not run as root.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Callable, Iterable, Iterator, Sequence

from .config import Settings
from .decode import decode_frame
from .errors import CaptureError
from .packets import PacketRecord
from .validation import IPAddress, build_capture_filter

log = logging.getLogger(__name__)

PacketHandler = Callable[[PacketRecord], None]

ETH_P_ALL = 0x0003
SIO_RCVALL = 0x98000001
RCVALL_ON = 1
RCVALL_OFF = 0


class CaptureBackend:
    """Base class for capture backends."""

    name = "base"
    simulated = False
    requires_privileges = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.packets_seen = 0
        self.error: str | None = None

    # -- lifecycle -------------------------------------------------------
    def start(self, target: IPAddress, protocols: Sequence[str], handler: PacketHandler) -> None:
        if self._thread is not None:
            raise CaptureError("capture already started")
        self._stop.clear()
        self._prepare(target, protocols)
        self._thread = threading.Thread(
            target=self._run_guarded, args=(target, protocols, handler),
            name=f"capture-{target}", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)
        self._teardown()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def describe(self) -> dict:
        return {
            "backend": self.name,
            "simulated": self.simulated,
            "packets_seen": self.packets_seen,
            "running": self.running,
            "error": self.error,
        }

    # -- hooks -----------------------------------------------------------
    def _prepare(self, target: IPAddress, protocols: Sequence[str]) -> None:
        """Acquire resources before the worker thread starts."""

    def _teardown(self) -> None:
        """Release resources after the worker thread stops."""

    def _run_guarded(self, target: IPAddress, protocols: Sequence[str],
                     handler: PacketHandler) -> None:
        try:
            self._run(target, protocols, handler)
        except Exception as exc:  # noqa: BLE001 - surfaced through describe()
            self.error = f"{type(exc).__name__}: {exc}"
            log.error("capture for %s stopped: %s", target, self.error)

    def _run(self, target: IPAddress, protocols: Sequence[str],
             handler: PacketHandler) -> None:
        raise NotImplementedError

    # -- shared helpers --------------------------------------------------
    def _emit(self, record: PacketRecord | None, target: IPAddress,
              protocols: Sequence[str], handler: PacketHandler) -> None:
        """Filter to the target and hand the record on with direction set."""
        if record is None:
            return
        target_text = str(target)
        if record.dst_ip == target_text:
            inbound = True
        elif record.src_ip == target_text:
            inbound = False
        else:
            return
        if record.protocol not in protocols and record.protocol != "other":
            return
        self.packets_seen += 1
        if inbound != record.inbound:
            record = PacketRecord(
                ts=record.ts, src_ip=record.src_ip, dst_ip=record.dst_ip,
                protocol=record.protocol, length=record.length, src_port=record.src_port,
                dst_port=record.dst_port, tcp_flags=record.tcp_flags, ttl=record.ttl,
                inbound=inbound,
            )
        handler(record)


class ReplayCapture(CaptureBackend):
    """Replays pre-built records. No privileges, no network access.

    Used by the test-suite and by ``simulate``. Everything it produces is
    marked ``simulated`` so a demonstration cannot be mistaken for live data.
    """

    name = "replay"
    simulated = True

    def __init__(self, settings: Settings, records: Iterable[PacketRecord],
                 realtime: bool = True, speed: float = 1.0) -> None:
        super().__init__(settings)
        self._records = records
        self._realtime = realtime
        self._speed = max(0.01, float(speed))

    def _run(self, target: IPAddress, protocols: Sequence[str],
             handler: PacketHandler) -> None:
        iterator: Iterator[PacketRecord] = iter(self._records)
        started = time.time()
        origin: float | None = None
        for record in iterator:
            if self._stop.is_set():
                return
            if origin is None:
                origin = record.ts
            if self._realtime:
                # Rewrite timestamps onto the wall clock so the dashboard shows
                # a live-looking timeline, and pace playback to match.
                offset = (record.ts - origin) / self._speed
                deadline = started + offset
                while not self._stop.is_set():
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    self._stop.wait(min(remaining, 0.25))
                if self._stop.is_set():
                    return
                record = PacketRecord(
                    ts=started + offset, src_ip=record.src_ip, dst_ip=record.dst_ip,
                    protocol=record.protocol, length=record.length, src_port=record.src_port,
                    dst_port=record.dst_port, tcp_flags=record.tcp_flags, ttl=record.ttl,
                    inbound=record.inbound,
                )
            self._emit(record, target, protocols, handler)


class RawSocketCapture(CaptureBackend):
    """Live capture with the standard library only."""

    name = "rawsocket"
    requires_privileges = True

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._sock: socket.socket | None = None
        self._has_link_layer = True

    def _prepare(self, target: IPAddress, protocols: Sequence[str]) -> None:
        self._sock = self._open_socket(target)
        # The socket is the only thing that needed elevation. Drop privileges
        # before the loop runs so a bug in decoding is not a root-level bug.
        drop_privileges(self.settings.drop_privileges_user)

    def _open_socket(self, target: IPAddress) -> socket.socket:
        snaplen = self.settings.snaplen
        try:
            if hasattr(socket, "AF_PACKET"):
                sock = socket.socket(
                    socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL)
                )
                if self.settings.interface:
                    sock.bind((self.settings.interface, 0))
                self._has_link_layer = True
            elif os.name == "nt":
                if target.version != 4:
                    raise CaptureError(
                        "the Windows raw-socket backend supports IPv4 only; install "
                        "npcap and scapy for IPv6 capture"
                    )
                host = self._local_address(target)
                sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                sock.bind((host, 0))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
                sock.ioctl(SIO_RCVALL, RCVALL_ON)
                self._has_link_layer = False
            else:
                raise CaptureError(
                    "no standard-library capture path on this platform; install scapy "
                    "(and libpcap) to capture here"
                )
        except PermissionError as exc:
            raise CaptureError(
                "packet capture needs elevated privileges: run as root/Administrator, "
                "or on Linux grant only the capability needed with: "
                "setcap cap_net_raw,cap_net_admin=eip $(which python3)"
            ) from exc
        except OSError as exc:
            raise CaptureError(f"could not open capture socket: {exc}") from exc

        sock.settimeout(0.5)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, max(1 << 21, snaplen * 4096))
        except OSError:
            pass
        return sock

    @staticmethod
    def _local_address(target: IPAddress) -> str:
        """Pick the local address whose interface should be listened on."""
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No packets are sent: connect() on UDP only selects a route.
            probe.connect((str(target), 9))
            return probe.getsockname()[0]
        except OSError:
            return "0.0.0.0"
        finally:
            probe.close()

    def _run(self, target: IPAddress, protocols: Sequence[str],
             handler: PacketHandler) -> None:
        sock = self._sock
        if sock is None:
            raise CaptureError("capture socket was not opened")
        snaplen = self.settings.snaplen
        while not self._stop.is_set():
            try:
                # Truncating recv: payload past the headers never reaches us.
                buf = sock.recv(snaplen)
            except socket.timeout:
                continue
            except OSError as exc:
                if self._stop.is_set():
                    return
                raise CaptureError(f"capture socket failed: {exc}") from exc
            record = decode_frame(buf, time.time(), has_link_layer=self._has_link_layer)
            self._emit(record, target, protocols, handler)

    def _teardown(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            if os.name == "nt" and not self._has_link_layer:
                sock.ioctl(SIO_RCVALL, RCVALL_OFF)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


class ScapyCapture(CaptureBackend):
    """Live capture via scapy, which compiles the filter into kernel BPF."""

    name = "scapy"
    requires_privileges = True

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._sniffer = None

    @staticmethod
    def available() -> bool:
        try:
            import scapy.all  # noqa: F401
        except Exception:  # noqa: BLE001 - any import problem means unavailable
            return False
        return True

    def _run(self, target: IPAddress, protocols: Sequence[str],
             handler: PacketHandler) -> None:
        from scapy.all import AsyncSniffer  # type: ignore import-not-found

        bpf = build_capture_filter(target, protocols)

        def on_packet(pkt) -> None:  # pragma: no cover - needs live capture
            record = decode_frame(bytes(pkt), time.time(), has_link_layer=True)
            self._emit(record, target, protocols, handler)

        kwargs = {
            "filter": bpf,
            "prn": on_packet,
            "store": False,
            # Header-only: scapy passes the snaplen through to libpcap.
            "snaplen": self.settings.snaplen,
        }
        if self.settings.interface:
            kwargs["iface"] = self.settings.interface
        sniffer = AsyncSniffer(**kwargs)
        self._sniffer = sniffer
        sniffer.start()
        drop_privileges(self.settings.drop_privileges_user)
        while not self._stop.is_set():
            self._stop.wait(0.25)
        try:
            sniffer.stop()
        except Exception:  # noqa: BLE001 - scapy raises if already stopped
            pass


def drop_privileges(username: str) -> bool:
    """Drop to ``username`` on POSIX after privileged setup. Returns success.

    Group privileges are dropped before user privileges, and the supplementary
    group list is reset first - doing these in the wrong order leaves the
    process able to regain access it should have lost.
    """
    if not username or os.name != "posix":
        return False
    if os.getuid() != 0:  # type: ignore[attr-defined]
        return False
    import grp
    import pwd

    entry = pwd.getpwnam(username)
    try:
        os.setgroups([g.gr_gid for g in grp.getgrall() if username in g.gr_mem])
    except OSError:
        os.setgroups([entry.pw_gid])
    os.setgid(entry.pw_gid)
    os.setuid(entry.pw_uid)
    if os.getuid() == 0:  # type: ignore[attr-defined]
        raise CaptureError("failed to drop privileges")
    log.info("dropped privileges to %s", username)
    return True


def build_backend(settings: Settings) -> CaptureBackend:
    """Choose a capture backend according to configuration and platform."""
    choice = (settings.capture_backend or "auto").lower()
    if choice == "none":
        raise CaptureError("capture is disabled (DDOS_CAPTURE_BACKEND=none)")
    if choice == "offline":
        raise CaptureError(
            "the offline backend replays supplied records; start it through the "
            "simulate command rather than as a live monitor"
        )
    if choice == "live":
        return ScapyCapture(settings) if ScapyCapture.available() else RawSocketCapture(settings)
    if choice == "auto":
        if ScapyCapture.available():
            return ScapyCapture(settings)
        return RawSocketCapture(settings)
    raise CaptureError(f"unknown capture backend: {settings.capture_backend}")


def capture_preflight(settings: Settings) -> dict:
    """Report whether live capture is likely to work, without starting it."""
    info: dict[str, object] = {
        "platform": os.name,
        "scapy_available": ScapyCapture.available(),
        "snaplen": settings.snaplen,
        "interface": settings.interface or "(default)",
    }
    if os.name == "posix":
        info["euid"] = os.geteuid()  # type: ignore[attr-defined]
        info["privileged"] = os.geteuid() == 0  # type: ignore[attr-defined]
    else:
        info["privileged"] = _windows_is_admin()
    info["link_layer_capture"] = hasattr(socket, "AF_PACKET")
    return info


def _windows_is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - absence of the API means "assume not"
        return False

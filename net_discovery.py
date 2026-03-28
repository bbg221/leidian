"""
局域网 UDP 发现协议（与具体游戏解耦，可复用到其他 pygame / 非 pygame 项目）。

约定（JSON 经 UTF-8 编码后 UDP 发送）：
- type=discover   : 客户端/对等端广播「我在找局」，含 id、port（游戏实际通信端口）、game（游戏标识字符串）。
- type=host_waiting : 主机广播「可加入」，同上；收到 discover 时可单播回复 host_waiting。

角色判定（仅当用户选中的是 discover 来源的对等端时）：
- 比较双方 id，小者当主机、大者当客户端。
若选中的是 host_waiting 条目：一律作为客户端连该 IP 的 game_port（避免与主机竞选逻辑冲突）。

其他游戏复用时：改 game_id 字符串与 game_port；发现端口可用默认或自定义。
"""

from __future__ import annotations

import json
import random
import socket
import time
from dataclasses import dataclass
from typing import Callable, Literal

# 默认发现端口；游戏业务端口由调用方传入
DEFAULT_DISCOVERY_PORT = 28991
UDP_PACKET_MAX = 65535

TYPE_DISCOVER = "discover"
TYPE_HOST_WAITING = "host_waiting"

LogFn = Callable[[str], None] | None


def udp_send_json(sock: socket.socket, addr: tuple[str, int], payload: dict[str, object]) -> None:
    """UDP 发送 JSON；失败静默（避免断网时打断游戏循环）。"""
    try:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        sock.sendto(data, addr)
    except OSError:
        pass


def udp_recv_all_json(
    sock: socket.socket,
    max_size: int | None = None,
) -> list[tuple[dict[str, object], tuple[str, int]]]:
    """非阻塞收包，直到读空；只保留能解析为 dict 的 JSON。"""
    cap = max_size if max_size is not None else UDP_PACKET_MAX
    out: list[tuple[dict[str, object], tuple[str, int]]] = []
    while True:
        try:
            raw, addr = sock.recvfrom(cap)
        except BlockingIOError:
            break
        except OSError:
            break
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            out.append((obj, addr))
    return out


def build_discover_message(game_id: str, my_id: int, game_port: int) -> dict[str, object]:
    return {"type": TYPE_DISCOVER, "id": my_id, "port": game_port, "game": game_id}


def build_host_waiting_message(game_id: str, host_id: int, game_port: int) -> dict[str, object]:
    return {"type": TYPE_HOST_WAITING, "id": host_id, "port": game_port, "game": game_id}


def random_session_id() -> int:
    """用于 discover / host 侧的稳定随机 id。"""
    return random.randint(100_000, 999_999_999)


def normalize_peer_entry(
    msg: dict[str, object],
    addr: tuple[str, int],
    *,
    my_discover_id: int,
    default_game_port: int,
    game_id: str,
) -> tuple[int, str, int, str] | None:
    """
    从一条 discover / host_waiting 消息解析出 (peer_id, ip, game_port, msg_type)。
    与 my_discover_id 相同的 discover 包忽略（自己回声）。
    """
    mtype = str(msg.get("type", ""))
    if mtype not in (TYPE_DISCOVER, TYPE_HOST_WAITING):
        return None
    if msg.get("game") != game_id:
        return None
    try:
        rid = int(msg.get("id", 0))
        gp = int(msg.get("port", default_game_port))
    except (TypeError, ValueError):
        return None
    if rid == my_discover_id and mtype == TYPE_DISCOVER:
        return None
    if rid == 0:
        rid = abs(hash(addr[0])) % 1_000_000_000 + 1_000_000_000
    gp = max(1, min(65535, gp))
    return rid, addr[0], gp, mtype


def decide_pairing_role(
    selected_source_type: str,
    my_id: int,
    peer_id: int,
) -> Literal["host", "client"]:
    """
    选中会话后的角色。
    host_waiting：固定连对方，本机为 client。
    discover：比 id 小的一方为 host。
    """
    if selected_source_type == TYPE_HOST_WAITING:
        return "client"
    if my_id < peer_id:
        return "host"
    return "client"


@dataclass
class HostAdvertiser:
    """
    主机在「等待客户端加入」时：
    - 周期性广播 host_waiting；
    - 收到 discover 后向来源单播 host_waiting，方便 NAT/广播受限环境。
    """

    game_id: str
    host_id: int
    game_port: int
    discovery_port: int = DEFAULT_DISCOVERY_PORT
    log: LogFn = None

    def __post_init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind(("", self.discovery_port))
        except OSError:
            pass
        self._sock.setblocking(False)
        self._last_broadcast = 0.0

    def _lg(self, msg: str) -> None:
        if self.log:
            self.log(msg)

    def tick(self, *, client_connected: bool, interval_s: float = 0.5) -> None:
        if client_connected:
            return
        now = time.time()
        if now - self._last_broadcast < interval_s:
            return
        payload = build_host_waiting_message(self.game_id, self.host_id, self.game_port)
        udp_send_json(self._sock, ("255.255.255.255", self.discovery_port), payload)
        self._last_broadcast = now
        self._lg(f"[HostAdvertiser] broadcast host_waiting id={self.host_id} port={self.game_port}")

    def poll_and_reply_discover(self) -> None:
        """未连上客户端时，回复 discover 单播。"""
        for dmsg, daddr in udp_recv_all_json(self._sock):
            if dmsg.get("type") != TYPE_DISCOVER:
                continue
            if dmsg.get("game") != self.game_id:
                continue
            self._lg(f"[HostAdvertiser] recv discover from={daddr[0]}:{daddr[1]} -> reply")
            udp_send_json(
                self._sock,
                daddr,
                build_host_waiting_message(self.game_id, self.host_id, self.game_port),
            )

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

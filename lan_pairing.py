"""
雷电专用：基于 net_discovery 的 Pygame 配对大厅（会话列表 + 超时当主机）。

其他游戏可只复用 net_discovery.py，自行写 UI 或调用本模块同款逻辑：
- 循环里 merge 消息到表、广播 discover、渲染列表即可。
"""

from __future__ import annotations

import random
import socket
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import pygame

from net_discovery import (
    DEFAULT_DISCOVERY_PORT,
    TYPE_DISCOVER,
    TYPE_HOST_WAITING,
    build_discover_message,
    decide_pairing_role,
    normalize_peer_entry,
    udp_recv_all_json,
    udp_send_json,
)

if TYPE_CHECKING:
    LogFn = Callable[[str], None] | None


def run_pairing_lobby(
    *,
    width: int,
    height: int,
    vu: Callable[[float], int],
    compute_window_size: Callable[[int, int], tuple[int, int]],
    game_id: str,
    game_port: int,
    discovery_port: int = DEFAULT_DISCOVERY_PORT,
    timeout_s: float = 120.0,
    log: "LogFn" = None,
    on_become_host: Callable[[], None],
    on_become_client: Callable[[str, int], None],
    caption: str = "局域网 · 自动配对",
) -> None:
    """
    阻塞运行直到：用户选中会话、超时当主机、或关闭窗口退出。

    on_become_host: 无参，进入主机逻辑（应自行 pygame.init 若需要）。
    on_become_client: (host_ip, game_port) 连游戏端口，不是发现端口。
    """
    def _lg(msg: str) -> None:
        if log:
            log(msg)

    pygame.init()
    win_w, win_h = compute_window_size(width, height)
    screen = pygame.display.set_mode((win_w, win_h))
    canvas = pygame.Surface((width, height))
    pygame.display.set_caption(caption)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("microsoftyahei", max(14, vu(22)))
    font_small = pygame.font.SysFont("microsoftyahei", max(12, vu(18)))

    my_id = random.randint(100_000, 999_999_999)
    _lg(f"[PAIR] start my_id={my_id} game_port={game_port} discovery_port={discovery_port}")

    hello = build_discover_message(game_id, my_id, game_port)
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_sock.bind(("", discovery_port))
    udp_sock.setblocking(False)

    start = time.time()
    last_broadcast = 0.0
    # 选中项：连接用对方 IP + 游戏端口；source_type 用于 decide_pairing_role
    selected_ip: str | None = None
    selected_peer_id: int | None = None
    selected_source_type = TYPE_DISCOVER
    selected_game_port = game_port
    # peer_id -> (ip, game_port, last_seen, msg_type)
    discovered: dict[int, tuple[str, int, float, str]] = {}
    select_idx = 0
    running = True

    try:
        while running and time.time() - start < timeout_s:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_UP, pygame.K_w):
                        select_idx -= 1
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        select_idx += 1
                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        alive_list = _alive_list(discovered, time.time(), ttl=12.0)
                        if alive_list:
                            select_idx = max(0, min(select_idx, len(alive_list) - 1))
                            rid, ip, gp, _, mtype = alive_list[select_idx]
                            selected_ip = ip
                            selected_peer_id = rid
                            selected_source_type = mtype
                            selected_game_port = gp
                            _lg(f"[PAIR] select rid={rid} type={mtype} ip={ip} gp={gp}")
                            running = False

            now = time.time()
            if now - last_broadcast >= 0.5:
                udp_send_json(udp_sock, ("255.255.255.255", discovery_port), hello)
                last_broadcast = now

            for msg, addr in udp_recv_all_json(udp_sock):
                ent = normalize_peer_entry(
                    msg,
                    addr,
                    my_discover_id=my_id,
                    default_game_port=game_port,
                    game_id=game_id,
                )
                if ent is None:
                    continue
                rid, ip, gp, mtype = ent
                discovered[rid] = (ip, gp, time.time(), mtype)
                _lg(f"[PAIR] recv {mtype} from={ip} rid={rid} gp={gp}")

            alive_list = _alive_list(discovered, time.time(), ttl=12.0)
            if alive_list:
                select_idx = max(0, min(select_idx, len(alive_list) - 1))
            else:
                select_idx = 0

            remain = max(0, int(timeout_s - (time.time() - start)))
            _draw_lobby(
                canvas,
                font,
                font_small,
                vu,
                width,
                alive_list,
                select_idx,
                remain,
            )
            pygame.transform.smoothscale(canvas, (win_w, win_h), screen)
            pygame.display.flip()
            clock.tick(30)
    finally:
        udp_sock.close()
        pygame.quit()

    if not running and selected_ip is None:
        return

    if selected_ip is None or selected_peer_id is None:
        if running:
            _lg("[PAIR] timeout -> host")
            on_become_host()
        return

    role = decide_pairing_role(selected_source_type, my_id, selected_peer_id)
    if role == "host":
        _lg(f"[PAIR] role=HOST peer_id={selected_peer_id}")
        on_become_host()
    else:
        _lg(f"[PAIR] role=CLIENT -> {selected_ip}:{selected_game_port}")
        on_become_client(selected_ip, selected_game_port)


def _alive_list(
    discovered: dict[int, tuple[str, int, float, str]],
    now: float,
    *,
    ttl: float,
) -> list[tuple[int, str, int, float, str]]:
    # ttl 过短易因偶发丢包、主机短暂停播发现包而清空列表；略长更稳
    rows = [
        (rid, ip, gp, seen, mtype)
        for rid, (ip, gp, seen, mtype) in discovered.items()
        if now - seen <= ttl
    ]
    rows.sort(key=lambda x: x[3], reverse=True)
    return rows


def _draw_lobby(
    canvas: pygame.Surface,
    font: pygame.font.Font,
    font_small: pygame.font.Font,
    vu: Callable[[float], int],
    width: int,
    alive_list: list[tuple[int, str, int, float, str]],
    select_idx: int,
    remain: int,
) -> None:
    canvas.fill((12, 16, 30))
    title = font.render("局域网会话列表（自动发现中）", True, (220, 235, 255))
    tip1 = font_small.render("另一台选「局域网双人」进入本界面", True, (185, 200, 220))
    tip2 = font_small.render("↑↓ 选择，回车连接；超时则本机当主机等待", True, (255, 220, 140))
    tip3 = font_small.render(f"倒计时: {remain}s  |  会话数: {len(alive_list)}", True, (170, 180, 200))
    canvas.blit(title, (vu(20), vu(44)))
    canvas.blit(tip1, (vu(20), vu(78)))
    canvas.blit(tip2, (vu(20), vu(104)))
    canvas.blit(tip3, (vu(20), vu(130)))

    list_top = vu(176)
    row_h = vu(34)
    max_rows = 10
    if not alive_list:
        empty = font_small.render("暂无可连接会话，正在搜索…", True, (200, 205, 220))
        canvas.blit(empty, (vu(26), list_top))
        return
    for i, (rid, ip, gp, seen, mtype) in enumerate(alive_list[:max_rows]):
        y = list_top + i * row_h
        selected = i == select_idx
        row_rect = pygame.Rect(vu(20), y - vu(4), width - vu(40), row_h - vu(2))
        if selected:
            pygame.draw.rect(canvas, (42, 66, 110), row_rect, border_radius=6)
        age_ms = int((time.time() - seen) * 1000)
        hint = "可直连主机" if mtype == TYPE_HOST_WAITING else "自动竞选"
        txt = f"[{i + 1}] {ip}:{gp}   id={rid}   {hint}   ≈{age_ms}ms"
        color = (240, 245, 255) if selected else (205, 215, 235)
        canvas.blit(font_small.render(txt, True, color), (vu(28), y))

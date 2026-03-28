"""
雷电风格纵版射击 — 使用 pygame。
共 50 关；逻辑分辨率 480×720，窗口按显示器自动缩放。敌机在 assets/Enemies，激光在 assets/Lasers。
激光、僚机弹、呼叫支援（闪电风暴）等见代码常量。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import socket
import sys
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import pygame


def _app_root() -> Path:
    """开发目录或 PyInstaller 解压目录（含 assets）。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


# --- 常量 ---
# 游戏逻辑分辨率（固定）；实际窗口由 compute_window_size() 按显示器适配缩放
WIDTH, HEIGHT = 480, 720
FPS = 60
MAX_LEVEL = 50
LAN_PORT = 28990
LAN_DISCOVERY_PORT = 28991
LAN_PACKET_SIZE = 65535
LAN_LOG_FILE = "lan_debug.log"

# 相对 480×720 设计尺寸的线性比例，用于速度/精灵上限/部分 UI
_VIEW_S = WIDTH / 480.0


def _vu(n: float) -> int:
    return max(1, int(round(n * _VIEW_S)))


def _vf(n: float) -> float:
    return n * _VIEW_S


ASSET_DIR = _app_root() / "assets"

PLAYER_SPEED = _vu(6)
BULLET_SPEED = _vu(12)
DRONE_MISSILE_SPEED = _vf(6.0)
DRONE_MISSILE_TURN = 0.11
ENEMY_SPEED_MIN = _vu(2)
ENEMY_SPEED_MAX = _vu(5)
# 相对第 1 关：第 n 关普通/特殊敌机血量 × 1.2^(n-1)
ENEMY_HP_PER_LEVEL_MULT = 1.2
# 刷怪：首关间隔最长，随关卡缩短；高分略加快（有上限）
ENEMY_SPAWN_MS_START = 1480
ENEMY_SPAWN_MS_PER_LEVEL = 26
ENEMY_SPAWN_MS_FLOOR = 168
ENEMY_SPAWN_SCORE_DIV = 150
ENEMY_SPAWN_SCORE_CAP_MS = 110
# 每 tick 刷几架：每 ENEMY_SPAWN_BATCH_EVERY 关升一档，每档 +ENEMY_SPAWN_COUNT_STEP 架
ENEMY_SPAWN_BATCH_EVERY = 9
ENEMY_SPAWN_COUNT_START = 1
ENEMY_SPAWN_COUNT_STEP = 2
ENEMY_SPAWN_COUNT_CAP = 10
# 有僚机：在同级基础上再加（仍随 tier 略增）；刷怪间隔倍率
WINGMAN_SPAWN_EXTRA_BASE = 3
WINGMAN_SPAWN_EXTRA_PER_TIER = 1
ENEMY_SPAWN_ABSOLUTE_CAP = 15
WINGMAN_SPAWN_INTERVAL_MUL = 0.52
BULLET_COOLDOWN_MS = 120
SUPPORT_COOLDOWN_MS = 520
LIGHTNING_FLASH_MS = 480
EXPLOSION_DURATION_MS = 260
SUPPORT_BONUS_PER_NEW_LEVEL = 2
# Boss 基础血量上，每架僚机再乘 (1 + 此系数)，最多计 BOSS_HP_WINGMAN_CAP 架
BOSS_HP_PER_WINGMAN_MULT = 0.12
BOSS_HP_WINGMAN_CAP = 36
SPECIAL_SPAWN_CHANCE = 0.14
SPECIAL_ENEMY_SPEED_MUL = 0.68
# 特殊机血量：低基础值；上限 = 5 × 单发最低伤害（扇形 2），与 load_gun_laser_profiles 保持一致
SPECIAL_MAX_BULLET_HITS = 5
MIN_GUN_BULLET_DAMAGE = 2
SPECIAL_ENEMY_HP_CAP = SPECIAL_MAX_BULLET_HITS * MIN_GUN_BULLET_DAMAGE
DRONE_MISSILE_DAMAGE = 9

# 机体贴图基准刻意压低，在大分辨率下仍显小；速度/UI 仍跟 _VIEW_S
PLAYER_MAX_W, PLAYER_MAX_H = _vu(30), _vu(30)
ENEMY_MAX_W, ENEMY_MAX_H = _vu(27), _vu(27)
BOSS_MAX_W, BOSS_MAX_H = _vu(70), _vu(70)
BULLET_MAX_W, BULLET_MAX_H = _vu(10), _vu(48)
LASER_MAX_W, LASER_MAX_H = _vu(17), _vu(58)
MISSILE_MAX_W, MISSILE_MAX_H = _vu(11), _vu(52)

# 过关前击毁数：第 1 关基准，之后每关比前一关多 WAVE_KILLS_STEP 架
WAVE_KILLS_LEVEL1 = 10
WAVE_KILLS_STEP = 10


class GunMode(Enum):
    SINGLE = auto()
    DOUBLE = auto()
    TRIPLE = auto()
    SPREAD = auto()


class PickupKind(Enum):
    GUN_SINGLE = auto()
    GUN_DOUBLE = auto()
    GUN_TRIPLE = auto()
    GUN_SPREAD = auto()
    WINGMAN = auto()


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def enemy_hp_level_multiplier(level: int) -> float:
    """每过一关血量 +20%（相对第 1 关复合增长）。"""
    lv = max(1, level)
    return ENEMY_HP_PER_LEVEL_MULT ** (lv - 1)


def compute_window_size(logic_w: int, logic_h: int) -> tuple[int, int]:
    """按当前显示器可用区域计算窗口大小，保持与逻辑分辨率相同纵横比，留边距。"""
    info = pygame.display.Info()
    sw = max(480, info.current_w or 1280)
    sh = max(360, info.current_h or 720)
    margin_x, margin_y = 56, 112
    max_w = max(240, sw - margin_x)
    max_h = max(320, sh - margin_y)
    scale = min(max_w / logic_w, max_h / logic_h)
    scale = min(scale, 1.35)
    scale = max(scale, 0.42)
    return int(round(logic_w * scale)), int(round(logic_h * scale))


def load_scaled(name: str, max_w: int, max_h: int) -> pygame.Surface:
    path = ASSET_DIR / name
    img = pygame.image.load(path).convert_alpha()
    w, h = img.get_size()
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        img = pygame.transform.smoothscale(img, (nw, nh))
    return img


def load_scaled_path(path: Path, max_w: int, max_h: int) -> pygame.Surface | None:
    if not path.is_file():
        return None
    img = pygame.image.load(path).convert_alpha()
    w, h = img.get_size()
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        img = pygame.transform.smoothscale(img, (nw, nh))
    return img


def discover_enemies_pack() -> tuple[Path, Path] | None:
    """返回 (assets/Enemies, assets)，用于 enemy*.png 与 assets 根目录的 ufo*.png。"""
    enemies = ASSET_DIR / "Enemies"
    if enemies.is_dir() and any(enemies.glob("*.png")):
        return enemies, ASSET_DIR
    return None


def _enemy_png_sort_key(path: Path) -> tuple[int, str]:
    """先红绿蓝黑再其它，前期关卡更易出现轻敌机。"""
    n = path.name.lower()
    if "red" in n:
        tier = 0
    elif "green" in n:
        tier = 1
    elif "blue" in n:
        tier = 2
    elif "black" in n:
        tier = 3
    else:
        tier = 4
    return (tier, n)


def discover_lasers_dir() -> Path | None:
    lasers = ASSET_DIR / "Lasers"
    if lasers.is_dir() and any(lasers.glob("laser*.png")):
        return lasers
    return None


@dataclass(frozen=True)
class EnemyArchetype:
    name: str
    surf: pygame.Surface
    speed_lo: float
    speed_hi: float
    vx_mul: float
    base_hp: int
    base_score: int
    wobble_amp: float
    pattern: str


def _try_load_enemy_graphic(
    enemies_dir: Path | None,
    png_root: Path | None,
    in_enemies: str,
    alt_enemies: str,
    in_root: str | None,
    alt_root: str | None,
    mw: int,
    mh: int,
) -> pygame.Surface | None:
    for folder, names in (
        (enemies_dir, (in_enemies, alt_enemies)),
        (png_root, tuple(x for x in (in_root, alt_root) if x)),
    ):
        if folder is None:
            continue
        for name in names:
            s = load_scaled_path(folder / name, mw, mh)
            if s is not None:
                return s
    return None


def _enemy_specs_table() -> list[dict[str, object]]:
    """无 Enemies 目录时的兜底表：红/绿/蓝/黑各 5 型 + UFO。"""
    out: list[dict[str, object]] = []
    for zh, prefix, hp0, hp_step, sc0, vx0, s_lo, s_hi, wb0 in [
        ("红", "enemyRed", 14, 4, 10, 1.0, 0.84, 1.12, 1.0),
        ("绿", "enemyGreen", 17, 4, 12, 1.06, 0.9, 1.2, 1.05),
        ("蓝", "enemyBlue", 20, 4, 14, 1.15, 0.94, 1.32, 1.12),
        ("黑", "enemyBlack", 42, 11, 18, 0.7, 0.46, 0.76, 0.62),
    ]:
        for i in range(1, 6):
            pat = "tank" if prefix == "enemyBlack" else ("dart" if i >= 3 else "standard")
            out.append(
                {
                    "name": f"{zh}{i}",
                    "in_e": f"{prefix}{i}.png",
                    "alt_e": f"{prefix}{((i % 5) + 1)}.png",
                    "root": None,
                    "alt_r": None,
                    "s_lo": s_lo,
                    "s_hi": s_hi,
                    "vx": vx0 * (1.0 + 0.03 * (i - 1)),
                    "hp": hp0 + hp_step * (i - 1),
                    "score": sc0 + i * 2,
                    "wb": wb0,
                    "pat": pat,
                }
            )
    for name, fn, alt, hp, sc in [
        ("飞红", "ufoRed.png", "ufoYellow.png", 18, 22),
        ("飞黄", "ufoYellow.png", "ufoRed.png", 16, 20),
        ("飞绿", "ufoGreen.png", "ufoBlue.png", 19, 23),
        ("飞蓝", "ufoBlue.png", "ufoGreen.png", 17, 21),
    ]:
        out.append(
            {
                "name": name,
                "in_e": fn,
                "alt_e": alt,
                "root": fn,
                "alt_r": alt,
                "s_lo": 1.08,
                "s_hi": 1.46,
                "vx": 1.42,
                "hp": hp,
                "score": sc,
                "wb": 1.32,
                "pat": "ufo",
            }
        )
    return out


def _build_enemy_archetypes_from_table(mw: int, mh: int) -> list[EnemyArchetype]:
    pack = discover_enemies_pack()
    enemies_dir = pack[0] if pack else None
    png_root = pack[1] if pack else None
    specs = _enemy_specs_table()
    archetypes: list[EnemyArchetype] = []
    fallback: pygame.Surface | None = None
    for sp in specs:
        surf = _try_load_enemy_graphic(
            enemies_dir,
            png_root,
            str(sp["in_e"]),
            str(sp["alt_e"]),
            str(sp["root"]) if sp["root"] else None,
            str(sp["alt_r"]) if sp["alt_r"] else None,
            mw,
            mh,
        )
        if surf is None:
            surf = fallback
        if surf is None:
            surf = load_scaled("enemy_red.png", mw, mh)
        if fallback is None:
            fallback = surf
        archetypes.append(
            EnemyArchetype(
                name=str(sp["name"]),
                surf=surf,
                speed_lo=float(sp["s_lo"]),
                speed_hi=float(sp["s_hi"]),
                vx_mul=float(sp["vx"]),
                base_hp=int(sp["hp"]),
                base_score=int(sp["score"]),
                wobble_amp=float(sp["wb"]),
                pattern=str(sp["pat"]),
            )
        )
    return archetypes


def build_enemy_archetypes(mw: int, mh: int) -> list[EnemyArchetype]:
    """扫描 assets/Enemies 下全部 PNG，一图一机型；支持 50 关逐步解锁。"""
    pack = discover_enemies_pack()
    if not pack:
        return _build_enemy_archetypes_from_table(mw, mh)
    enemies_dir, png_root = pack
    paths = sorted(enemies_dir.glob("*.png"), key=_enemy_png_sort_key)
    if not paths:
        return _build_enemy_archetypes_from_table(mw, mh)

    archetypes: list[EnemyArchetype] = []
    fallback: pygame.Surface | None = None

    for idx, p in enumerate(paths):
        surf = load_scaled_path(p, mw, mh)
        if surf is None:
            continue
        if fallback is None:
            fallback = surf
        stem = p.stem.lower().replace(" ", "")
        if "black" in stem:
            pat, hp_b, sc_b = "tank", 38, 10
            slo, shi, vx, wb = 0.46, 0.78, 0.74, 0.62
        elif "ufo" in stem:
            pat, hp_b, sc_b = "ufo", 4, 6
            slo, shi, vx, wb = 1.08, 1.44, 1.38, 1.32
        elif "othercolor" in stem or stem.startswith("enemyother"):
            pat, hp_b, sc_b = "dart", 14, 5
            slo, shi, vx, wb = 0.93, 1.26, 1.18, 1.06
        elif "blue" in stem:
            pat, hp_b, sc_b = "dart", 10, 4
            slo, shi, vx, wb = 0.94, 1.32, 1.16, 1.08
        elif "green" in stem:
            dig = "".join(filter(str.isdigit, stem))
            gi = int(dig) if dig else 1
            pat = "dart" if gi >= 3 else "standard"
            hp_b, sc_b = 8, 3
            slo, shi, vx, wb = 0.9, 1.2, 1.04 + gi * 0.02, 1.02
        elif "red" in stem:
            pat, hp_b, sc_b = "standard", 0, 0
            slo, shi, vx, wb = 0.85, 1.1, 1.0, 1.0
        else:
            pat, hp_b, sc_b = "standard", 6, 3
            slo, shi, vx, wb = 0.88, 1.18, 1.06, 1.0

        base_hp = 14 + (idx * 6) % 28 + hp_b
        base_sc = 11 + (idx % 10) + sc_b
        nick = p.stem
        if len(nick) > 14:
            nick = nick[:13] + "…"

        archetypes.append(
            EnemyArchetype(
                name=nick,
                surf=surf,
                speed_lo=slo,
                speed_hi=shi,
                vx_mul=vx,
                base_hp=base_hp,
                base_score=base_sc,
                wobble_amp=wb,
                pattern=pat,
            )
        )

    existing = {a.name for a in archetypes}
    if png_root:
        for ufo_file, uname in [
            ("ufoRed.png", "U红"),
            ("ufoYellow.png", "U黄"),
            ("ufoGreen.png", "U绿"),
            ("ufoBlue.png", "U蓝"),
        ]:
            if uname in existing:
                continue
            up = png_root / ufo_file
            s = load_scaled_path(up, mw, mh)
            if s is None:
                continue
            archetypes.append(
                EnemyArchetype(
                    name=uname,
                    surf=s,
                    speed_lo=1.08,
                    speed_hi=1.44,
                    vx_mul=1.38,
                    base_hp=18,
                    base_score=22,
                    wobble_amp=1.32,
                    pattern="ufo",
                )
            )
            existing.add(uname)

    if not archetypes:
        return _build_enemy_archetypes_from_table(mw, mh)
    return archetypes


def load_boss_variants(enemies_dir: Path | None, png_root: Path | None, bw: int, bh: int) -> list[pygame.Surface]:
    names = [
        "enemyBlack5.png",
        "enemyRed1.png",
        "enemyBlue1.png",
        "enemyGreen4.png",
        "enemyBlack1.png",
    ]
    out: list[pygame.Surface] = []
    for n in names:
        s = _try_load_enemy_graphic(enemies_dir, png_root, n, n, None, None, bw, bh)
        if s is not None:
            out.append(s)
    return out


def pick_spawn_archetype(active: list[EnemyArchetype]) -> EnemyArchetype:
    if not active:
        raise RuntimeError("no archetypes")
    if len(active) == 1:
        return active[0]
    weights = [1.0] * (len(active) - 1) + [1.7]
    return random.choices(active, weights=weights, k=1)[0]


def archetypes_for_level(all_arch: list[EnemyArchetype], level: int) -> list[EnemyArchetype]:
    if not all_arch:
        return []
    raw = math.ceil(level * len(all_arch) / MAX_LEVEL)
    n = max(2, min(len(all_arch), raw))
    return all_arch[:n]


def level_enemy_label(active: list[EnemyArchetype]) -> str:
    if len(active) <= 5:
        return "/".join(a.name for a in active)
    return f"{len(active)}机型"


def load_gun_laser_profiles() -> dict[GunMode, tuple[pygame.Surface, int, float]]:
    """每种主炮：贴图、单发伤害、速度倍率。贴图来自 PNG/Lasers。"""
    lasers_dir = discover_lasers_dir()
    fb = load_scaled("bullet.png", BULLET_MAX_W, BULLET_MAX_H)
    configs: list[tuple[GunMode, str, str, int, float]] = [
        (GunMode.SINGLE, "laserBlue13.png", "laserBlue01.png", 5, 1.0),
        (GunMode.DOUBLE, "laserBlue07.png", "laserBlue05.png", 4, 1.02),
        (GunMode.TRIPLE, "laserGreen12.png", "laserGreen08.png", 4, 1.0),
        (GunMode.SPREAD, "laserRed06.png", "laserRed11.png", 2, 1.06),
    ]
    out: dict[GunMode, tuple[pygame.Surface, int, float]] = {}
    for mode, primary, alternate, dmg, spd in configs:
        surf = None
        if lasers_dir is not None:
            surf = load_scaled_path(lasers_dir / primary, LASER_MAX_W, LASER_MAX_H)
            if surf is None:
                surf = load_scaled_path(lasers_dir / alternate, LASER_MAX_W, LASER_MAX_H)
        if surf is None:
            surf = fb
        out[mode] = (surf, dmg, spd)
    return out


def gun_mode_label(mode: GunMode) -> str:
    return {
        GunMode.SINGLE: "单发",
        GunMode.DOUBLE: "双管",
        GunMode.TRIPLE: "三管",
        GunMode.SPREAD: "扇形",
    }[mode]


class Assets:
    __slots__ = (
        "player",
        "enemy_archetypes",
        "gun_lasers",
        "missile",
        "bg_tile",
        "boss_skin_fallback",
        "boss_variants",
        "wing_missile",
    )

    def __init__(self) -> None:
        self.player = load_scaled("player.png", PLAYER_MAX_W, PLAYER_MAX_H)
        self.enemy_archetypes = build_enemy_archetypes(ENEMY_MAX_W, ENEMY_MAX_H)
        pack = discover_enemies_pack()
        ed, pr = (pack[0], pack[1]) if pack else (None, None)
        self.boss_variants = load_boss_variants(ed, pr, BOSS_MAX_W, BOSS_MAX_H)
        self.boss_skin_fallback = load_scaled("enemy_green.png", BOSS_MAX_W, BOSS_MAX_H)
        self.gun_lasers = load_gun_laser_profiles()
        self.missile = load_scaled("missile.png", MISSILE_MAX_W, MISSILE_MAX_H)
        self.wing_missile = pygame.transform.smoothscale(
            self.missile, (max(1, MISSILE_MAX_W - 4), max(1, MISSILE_MAX_H - 6))
        )
        raw_bg = pygame.image.load(ASSET_DIR / "bg_tile.png").convert()
        bw, bh = raw_bg.get_size()
        nh = max(1, int(bh * WIDTH / bw))
        self.bg_tile = pygame.transform.smoothscale(raw_bg, (WIDTH, nh))

    def boss_for_level(self, level: int) -> pygame.Surface:
        if not self.boss_variants:
            return self.boss_skin_fallback
        return self.boss_variants[(level - 1) % len(self.boss_variants)]


def make_pickup_surface(kind: PickupKind) -> pygame.Surface:
    sz = _vu(24)
    cx = sz // 2
    r = max(4, cx - 1)
    s = pygame.Surface((sz, sz), pygame.SRCALPHA)
    colors = {
        PickupKind.GUN_SINGLE: ((90, 200, 255), "1"),
        PickupKind.GUN_DOUBLE: ((255, 220, 100), "2"),
        PickupKind.GUN_TRIPLE: ((180, 255, 120), "3"),
        PickupKind.GUN_SPREAD: ((255, 140, 220), "扇"),
        PickupKind.WINGMAN: ((160, 140, 255), "僚"),
    }
    fill, ch = colors[kind]
    pygame.draw.circle(s, (*fill, 230), (cx, cx), r)
    pygame.draw.circle(s, (255, 255, 255, 200), (cx, cx), r, max(1, _vu(2)))
    f = pygame.font.SysFont("microsoftyahei", max(10, _vu(16)), bold=True)
    t = f.render(ch, True, (20, 20, 30))
    s.blit(t, t.get_rect(center=(cx, cx)))
    return s


class AngledBullet:
    __slots__ = ("rect", "vx", "vy", "surf", "damage", "owner", "mode")

    def __init__(
        self,
        x: float,
        y: float,
        surf: pygame.Surface,
        vx: float,
        vy: float,
        damage: int,
        owner: int = 0,
        mode: GunMode = GunMode.SINGLE,
    ) -> None:
        self.surf = surf
        self.rect = surf.get_rect(center=(int(x), int(y)))
        self.vx = vx
        self.vy = vy
        self.damage = damage
        self.owner = owner
        self.mode = mode

    def update(self) -> None:
        self.rect.x += int(self.vx)
        self.rect.y += int(self.vy)


def spawn_player_bullets(
    bullets: list[AngledBullet],
    assets: Assets,
    cx: float,
    cy: float,
    mode: GunMode,
    owner: int = 0,
    power_mul: float = 1.0,
) -> None:
    surf, dmg, spd_mul = assets.gun_lasers[mode]
    spd = float(BULLET_SPEED) * spd_mul

    final_dmg = max(1, int(round(dmg * max(0.2, power_mul))))

    def add(x: float, y: float, vx: float, vy: float) -> None:
        bullets.append(AngledBullet(x, y, surf, vx, vy, final_dmg, owner=owner, mode=mode))

    if mode == GunMode.SINGLE:
        add(cx, cy, 0.0, -spd)
    elif mode == GunMode.DOUBLE:
        ox = _vf(16)
        add(cx - ox, cy, 0.0, -spd)
        add(cx + ox, cy, 0.0, -spd)
    elif mode == GunMode.TRIPLE:
        add(cx, cy, 0.0, -spd)
        ox = _vf(12)
        sd = _vf(1.4)
        add(cx - ox, cy, -sd, -spd * 0.98)
        add(cx + ox, cy, sd, -spd * 0.98)
    elif mode == GunMode.SPREAD:
        for deg in (-28, -14, 0, 14, 28):
            r = math.radians(deg)
            add(cx, cy, math.sin(r) * spd * 0.98, -math.cos(r) * spd * 0.98)


class DroneMissile:
    __slots__ = ("x", "y", "vx", "vy", "alive", "base", "angle", "damage", "owner")

    def __init__(
        self,
        x: float,
        y: float,
        vx: float,
        vy: float,
        base: pygame.Surface,
        damage: int = DRONE_MISSILE_DAMAGE,
        owner: int = 0,
    ) -> None:
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.alive = True
        self.base = base
        self.damage = damage
        self.owner = owner
        self.angle = math.degrees(math.atan2(vx, -vy))

    def update(self, targets: list[object]) -> None:
        target = None
        best_d = float("inf")
        for e in targets:
            if not getattr(e, "alive", True):
                continue
            cx, cy = e.center()  # type: ignore[attr-defined]
            d2 = (cx - self.x) ** 2 + (cy - self.y) ** 2
            if d2 < best_d:
                best_d = d2
                target = (cx, cy)

        if target:
            tx, ty = target
            ang = math.atan2(ty - self.y, tx - self.x)
            cur = math.atan2(self.vy, self.vx) if abs(self.vx) + abs(self.vy) > 0.01 else -math.pi / 2
            diff = (ang - cur + math.pi * 3) % (math.pi * 2) - math.pi
            cur += clamp(diff, -DRONE_MISSILE_TURN, DRONE_MISSILE_TURN)
            self.vx = math.cos(cur) * DRONE_MISSILE_SPEED
            self.vy = math.sin(cur) * DRONE_MISSILE_SPEED
        else:
            self.vy = min(self.vy - _vf(0.15), -DRONE_MISSILE_SPEED)

        self.x += self.vx
        self.y += self.vy
        self.angle = math.degrees(math.atan2(self.vx, -self.vy))

    def hit_rect(self) -> pygame.Rect:
        rot = pygame.transform.rotate(self.base, self.angle)
        return rot.get_rect(center=(int(self.x), int(self.y)))


def spawn_drone_pair(
    player_rect: pygame.Rect,
    assets: Assets,
    phase: float,
    drones: list[DroneMissile],
    owner: int = 0,
) -> None:
    cx, top = player_rect.centerx, player_rect.top + _vu(6)
    off = _vu(24)
    lx, ly = cx - off, top
    rx, ry = cx + off, top
    fan = _vf(26) * math.sin(phase)
    speed = _vf(5.2)

    def emit(x: float, y: float, deg: float) -> None:
        r = math.radians(deg)
        vx = math.sin(r) * speed
        vy = -math.cos(r) * speed
        drones.append(DroneMissile(x, y, vx, vy, assets.wing_missile, owner=owner))

    emit(lx, ly, -18 - fan)
    emit(rx, ry, 18 + fan)


class EnemyBullet:
    __slots__ = ("rect", "vy", "surf")

    def __init__(self, x: float, y: float, surf: pygame.Surface, speed: float) -> None:
        self.surf = surf
        self.rect = surf.get_rect(midtop=(int(x), int(y)))
        self.vy = speed

    def update(self) -> None:
        self.rect.y += int(self.vy)


class Pickup:
    __slots__ = ("rect", "vy", "vx", "fx", "fy", "kind", "surf", "alive")

    def __init__(self, x: float, y: float, kind: PickupKind) -> None:
        self.kind = kind
        self.surf = make_pickup_surface(kind)
        self.rect = self.surf.get_rect(center=(int(x), int(y)))
        if kind == PickupKind.WINGMAN:
            self.fx = float(x)
            self.fy = float(y)
            self.vx = random.uniform(-_vf(1.7), _vf(1.7))
            if abs(self.vx) < _vf(0.4):
                self.vx = _vf(1.25) * random.choice([-1.0, 1.0])
            self.vy = random.uniform(-_vf(1.0), _vf(1.0))
            if abs(self.vy) < _vf(0.35):
                self.vy = _vf(0.85) * random.choice([-1.0, 1.0])
        else:
            self.fx = 0.0
            self.fy = 0.0
            self.vx = 0.0
            self.vy = _vf(2.2) + random.random() * _vf(0.8)
        self.alive = True

    def update(self) -> None:
        if self.kind == PickupKind.WINGMAN:
            self.fx += self.vx
            self.fy += self.vy
            hw, hh = self.rect.width * 0.5, self.rect.height * 0.5
            pad_x = _vu(6)
            pad_top = _vu(44)
            pad_bot = _vu(36)
            lo_x, hi_x = hw + pad_x, WIDTH - hw - pad_x
            lo_y, hi_y = hh + pad_top, HEIGHT - hh - pad_bot
            if self.fx < lo_x:
                self.fx = lo_x
                self.vx = abs(self.vx) + _vf(0.02)
            elif self.fx > hi_x:
                self.fx = hi_x
                self.vx = -abs(self.vx) - _vf(0.02)
            if self.fy < lo_y:
                self.fy = lo_y
                self.vy = abs(self.vy) + _vf(0.02)
            elif self.fy > hi_y:
                self.fy = hi_y
                self.vy = -abs(self.vy) - _vf(0.02)
            self.rect.center = (int(self.fx), int(self.fy))
        else:
            self.rect.y += int(self.vy)


class Enemy:
    __slots__ = (
        "rect",
        "vy",
        "vx",
        "hp",
        "alive",
        "wobble",
        "surf",
        "is_special",
        "pattern",
        "base_score",
        "wobble_amp",
    )

    def __init__(
        self,
        x: float,
        y: float,
        arch: EnemyArchetype,
        special: bool = False,
        level: int = 1,
    ) -> None:
        self.surf = arch.surf
        self.rect = arch.surf.get_rect(midtop=(int(x), int(y)))
        self.pattern = arch.pattern
        self.base_score = arch.base_score
        self.wobble_amp = arch.wobble_amp
        vy_lo = ENEMY_SPEED_MIN * arch.speed_lo
        vy_hi = ENEMY_SPEED_MAX * arch.speed_hi
        self.vy = random.uniform(vy_lo, vy_hi)
        self.vx = random.choice([-1.0, 1.0]) * random.uniform(0.8, 2.2) * arch.vx_mul
        self.is_special = special
        if special:
            self.vy *= SPECIAL_ENEMY_SPEED_MUL
            self.vx *= SPECIAL_ENEMY_SPEED_MUL
        mul = enemy_hp_level_multiplier(level)
        if special:
            base = max(5, int(6 + arch.base_hp * 0.12))
            self.hp = max(1, min(int(round(base * mul)), SPECIAL_ENEMY_HP_CAP))
        else:
            self.hp = max(1, int(round(arch.base_hp * mul)))
        self.alive = True
        self.wobble = random.random() * math.pi * 2

    def center(self) -> tuple[float, float]:
        return float(self.rect.centerx), float(self.rect.centery)

    def update(self) -> None:
        self.wobble += 0.05
        wa = self.wobble_amp
        if self.pattern == "ufo":
            self.rect.x += int(self.vx + math.sin(self.wobble * 2.2) * 2.8 * wa)
            self.rect.y += int(self.vy + math.cos(self.wobble * 1.8) * 1.35 * wa)
        elif self.pattern == "tank":
            self.rect.x += int(self.vx * 0.92 + math.sin(self.wobble * 0.75) * 0.55 * wa)
            self.rect.y += int(self.vy)
        elif self.pattern == "dart":
            self.rect.x += int(self.vx + math.sin(self.wobble) * 1.05 * wa)
            self.rect.y += int(self.vy * 1.04)
        else:
            self.rect.x += int(self.vx + math.sin(self.wobble) * 0.8 * wa)
            self.rect.y += int(self.vy)
        if self.rect.left < 0 or self.rect.right > WIDTH:
            self.vx *= -1


class Boss:
    __slots__ = ("rect", "surf", "hp", "max_hp", "alive", "phase", "level_idx")

    def __init__(self, surf: pygame.Surface, level_idx: int, wingmen: int = 0) -> None:
        self.surf = surf
        self.level_idx = level_idx
        base = 290 + level_idx * 28 + (level_idx * level_idx) // 4
        wm = min(max(0, wingmen), BOSS_HP_WINGMAN_CAP)
        self.max_hp = int(round(base * (1.0 + wm * BOSS_HP_PER_WINGMAN_MULT)))
        self.hp = self.max_hp
        self.rect = surf.get_rect(center=(WIDTH // 2, _vu(95)))
        self.alive = True
        self.phase = random.random() * math.pi * 2

    def center(self) -> tuple[float, float]:
        return float(self.rect.centerx), float(self.rect.centery)

    def update(self, dt: float) -> None:
        if not self.alive:
            return
        self.phase += dt * 1.15
        span = min(_vf(155), _vf(110) + self.level_idx * _vf(8))
        cx = WIDTH // 2 + math.sin(self.phase) * span
        bob = math.sin(self.phase * 2.1) * _vf(6)
        self.rect.center = (int(cx), int(_vu(88) + bob))
        m = _vu(20)
        self.rect.clamp_ip(pygame.Rect(m, _vu(50), WIDTH - 2 * m, _vu(200)))


class Player:
    __slots__ = ("rect", "support_left", "surf", "gun_mode", "wingmen", "power_mul")

    def __init__(
        self,
        surf: pygame.Surface,
        gun_mode: GunMode = GunMode.SINGLE,
        wingmen: int = 0,
    ) -> None:
        self.surf = surf
        self.rect = surf.get_rect(midbottom=(WIDTH // 2, HEIGHT - _vu(40)))
        self.support_left = 12
        self.gun_mode = gun_mode
        self.wingmen = wingmen
        self.power_mul = 1.0

    def draw(self, surf: pygame.Surface) -> None:
        surf.blit(self.surf, self.rect)
        if self.wingmen > 0:
            ox = _vu(30)
            mini = pygame.transform.smoothscale(self.surf, (_vu(18), _vu(16)))
            ly = self.rect.bottom - _vu(6)
            surf.blit(mini, mini.get_rect(midright=(self.rect.left + ox // 2, ly)))
            surf.blit(mini, mini.get_rect(midleft=(self.rect.right - ox // 2, ly)))


class Phase(Enum):
    WAVE = auto()
    BOSS = auto()
    SETTLEMENT = auto()
    GAME_OVER = auto()


@dataclass
class LanInput:
    dx: int = 0
    dy: int = 0
    fire: bool = False
    support: bool = False
    respawn: bool = False


def _safe_send_json(sock: socket.socket, addr: tuple[str, int], payload: dict[str, object]) -> None:
    try:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        sock.sendto(data, addr)
    except OSError:
        pass


def lan_log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LAN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _poll_lan_messages(sock: socket.socket) -> list[tuple[dict[str, object], tuple[str, int]]]:
    out: list[tuple[dict[str, object], tuple[str, int]]] = []
    while True:
        try:
            raw, addr = sock.recvfrom(LAN_PACKET_SIZE)
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


def auto_pair_and_run(port: int = LAN_PORT, timeout_s: float = 120.0) -> None:
    """
    局域网自动发现并配对：
    - 两台都启动 --lan auto
    - 通过广播互相发现
    - 用随机 id 决定角色（小 id=host，大 id=client）
    """
    pygame.init()
    win_w, win_h = compute_window_size(WIDTH, HEIGHT)
    screen = pygame.display.set_mode((win_w, win_h))
    canvas = pygame.Surface((WIDTH, HEIGHT))
    pygame.display.set_caption("雷电 · 自动配对中")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("microsoftyahei", max(14, _vu(22)))
    font_small = pygame.font.SysFont("microsoftyahei", max(12, _vu(18)))

    my_id = random.randint(100_000, 999_999_999)
    lan_log(f"[AUTO] start id={my_id} game_port={port} discovery_port={LAN_DISCOVERY_PORT} pid={os.getpid()}")
    hello = {"type": "discover", "id": my_id, "port": port, "game": "leidian"}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", LAN_DISCOVERY_PORT))
    sock.setblocking(False)

    start = time.time()
    last_broadcast = 0.0
    selected_peer: tuple[str, int] | None = None
    selected_peer_id: int | None = None
    selected_peer_type = "discover"
    selected_peer_port = port
    # id -> (ip, game_port, last_seen_ts, msg_type)
    discovered: dict[int, tuple[str, int, float, str]] = {}
    select_idx = 0
    running = True

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
                    # 回车后按当前高亮会话连接
                    alive_list = [
                        (rid, ip, gp, seen, mtype)
                        for rid, (ip, gp, seen, mtype) in discovered.items()
                        if time.time() - seen <= 3.0
                    ]
                    alive_list.sort(key=lambda x: x[3], reverse=True)
                    if alive_list:
                        select_idx = max(0, min(select_idx, len(alive_list) - 1))
                        rid, ip, gp, _, mtype = alive_list[select_idx]
                        selected_peer = (ip, LAN_DISCOVERY_PORT)
                        selected_peer_id = rid
                        selected_peer_type = mtype
                        selected_peer_port = gp
                        lan_log(f"[AUTO] select peer rid={rid} type={mtype} ip={ip} gp={gp} my_id={my_id}")
                        running = False

        now = time.time()
        if now - last_broadcast >= 0.5:
            _safe_send_json(sock, ("255.255.255.255", LAN_DISCOVERY_PORT), hello)
            lan_log(f"[AUTO] broadcast discover id={my_id} gp={port}")
            last_broadcast = now

        for msg, addr in _poll_lan_messages(sock):
            mtype = str(msg.get("type", ""))
            if mtype not in ("discover", "host_waiting"):
                continue
            if msg.get("game") != "leidian":
                continue
            try:
                rid = int(msg.get("id", 0))
                gp = int(msg.get("port", port))
            except (TypeError, ValueError):
                continue
            if rid == my_id and mtype == "discover":
                continue
            if rid == 0:
                # 手动 host 无 id，给一个稳定伪 id（按 ip）
                rid = abs(hash(addr[0])) % 1_000_000_000 + 1_000_000_000
            gp = max(1, min(65535, gp))
            discovered[rid] = (addr[0], gp, time.time(), mtype)
            lan_log(f"[AUTO] recv {mtype} from={addr[0]} rid={rid} gp={gp}")

        alive_list = [
            (rid, ip, gp, seen, mtype)
            for rid, (ip, gp, seen, mtype) in discovered.items()
            if time.time() - seen <= 3.0
        ]
        alive_list.sort(key=lambda x: x[3], reverse=True)
        if alive_list:
            select_idx = max(0, min(select_idx, len(alive_list) - 1))
        else:
            select_idx = 0

        elapsed = time.time() - start
        remain = max(0, int(timeout_s - elapsed))
        canvas.fill((12, 16, 30))
        title = font.render("局域网会话列表（自动发现中）", True, (220, 235, 255))
        tip1 = font_small.render("另一台电脑运行: python main.py --lan auto", True, (185, 200, 220))
        tip2 = font_small.render("上下键选择会话，回车连接；超时后自动主机等待", True, (255, 220, 140))
        tip3 = font_small.render(f"倒计时: {remain}s  |  发现会话: {len(alive_list)}", True, (170, 180, 200))
        canvas.blit(title, (_vu(20), _vu(44)))
        canvas.blit(tip1, (_vu(20), _vu(78)))
        canvas.blit(tip2, (_vu(20), _vu(104)))
        canvas.blit(tip3, (_vu(20), _vu(130)))

        list_top = _vu(176)
        row_h = _vu(34)
        max_rows = 10
        if not alive_list:
            empty = font_small.render("暂无可连接会话，正在继续搜索...", True, (200, 205, 220))
            canvas.blit(empty, (_vu(26), list_top))
        else:
            for i, (rid, ip, gp, seen, mtype) in enumerate(alive_list[:max_rows]):
                y = list_top + i * row_h
                selected = i == select_idx
                row_rect = pygame.Rect(_vu(20), y - _vu(4), WIDTH - _vu(40), row_h - _vu(2))
                if selected:
                    pygame.draw.rect(canvas, (42, 66, 110), row_rect, border_radius=6)
                age_ms = int((time.time() - seen) * 1000)
                role_hint = "可直连主机" if mtype == "host_waiting" else "自动竞选"
                txt = f"[{i + 1}] {ip}:{gp}   id={rid}   {role_hint}   延迟≈{age_ms}ms"
                color = (240, 245, 255) if selected else (205, 215, 235)
                canvas.blit(font_small.render(txt, True, color), (_vu(28), y))

        pygame.transform.smoothscale(canvas, (win_w, win_h), screen)
        pygame.display.flip()
        clock.tick(30)

    sock.close()
    pygame.quit()

    if selected_peer is None or selected_peer_id is None:
        if running:
            # 未选中会话/超时：自动做主机，方便另一台稍后加入
            lan_log("[AUTO] timeout/no selection -> host mode")
            run_lan_host("0.0.0.0", port)
        return

    if selected_peer_type == "host_waiting":
        lan_log(f"[AUTO] selected host_waiting -> force CLIENT target={selected_peer[0]}:{selected_peer_port}")
        run_lan_client(selected_peer[0], selected_peer_port)
    elif my_id < selected_peer_id:
        lan_log(f"[AUTO] role=HOST my_id={my_id} peer_id={selected_peer_id}")
        run_lan_host("0.0.0.0", port)
    else:
        lan_log(f"[AUTO] role=CLIENT my_id={my_id} peer_id={selected_peer_id} target={selected_peer[0]}:{selected_peer_port}")
        run_lan_client(selected_peer[0], selected_peer_port)


def run_lan_host(bind_ip: str, port: int) -> None:
    pygame.init()
    win_w, win_h = compute_window_size(WIDTH, HEIGHT)
    screen = pygame.display.set_mode((win_w, win_h))
    canvas = pygame.Surface((WIDTH, HEIGHT))
    pygame.display.set_caption("雷电 · 局域网主机")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("microsoftyahei", max(12, _vu(18)))

    assets = Assets()
    boss_bullet_skin = make_boss_bullet_surf(_vu(9), _vu(19))
    players = [Player(assets.player), Player(assets.player)]
    spawn_pos = [
        (WIDTH // 2 - _vu(80), HEIGHT - _vu(40)),
        (WIDTH // 2 + _vu(80), HEIGHT - _vu(40)),
    ]
    players[0].rect.midbottom = spawn_pos[0]
    players[1].rect.midbottom = spawn_pos[1]
    player_alive = [True, True]
    bullets: list[AngledBullet] = []
    drones: list[DroneMissile] = []
    enemies: list[Enemy] = []
    pickups: list[Pickup] = []
    boss_bullets: list[EnemyBullet] = []
    explosions: list[ExplosionFx] = []
    remote_input = LanInput()
    last_fire = [0, 0]
    last_support = [0, 0]
    last_spawn = pygame.time.get_ticks()
    last_boss_shot = pygame.time.get_ticks()
    current_level = 1
    score = 0
    score_at_level_start = 0
    wave_kills = 0
    lives = [3, 3]
    phase = Phase.WAVE
    boss: Boss | None = None
    settlement_until = 0
    settlement_level_score = 0
    lightning_until = 0
    wing_fan_phase = [0.0, 0.0]
    bg_t = 0.0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_ip, port))
    sock.setblocking(False)
    client_addr: tuple[str, int] | None = None
    host_id = random.randint(100_000, 999_999_999)
    discover_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    discover_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    discover_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        discover_sock.bind(("", LAN_DISCOVERY_PORT))
    except OSError:
        # 某些环境端口被占用时，至少保留广播能力
        pass
    discover_sock.setblocking(False)
    last_wait_broadcast = 0.0
    lan_log(f"[HOST] start bind={bind_ip}:{port} host_id={host_id} pid={os.getpid()}")

    running = True
    while running:
        now = pygame.time.get_ticks()
        now_sec = time.time()
        if client_addr is None and now_sec - last_wait_broadcast >= 0.5:
            _safe_send_json(
                discover_sock,
                ("255.255.255.255", LAN_DISCOVERY_PORT),
                {"type": "host_waiting", "id": host_id, "port": port, "game": "leidian"},
            )
            lan_log(f"[HOST] broadcast host_waiting id={host_id} gp={port}")
            last_wait_broadcast = now_sec
        if client_addr is None:
            # 响应自动发现请求：B 机广播 discover 后，主机定向单播回复
            for dmsg, daddr in _poll_lan_messages(discover_sock):
                if dmsg.get("type") != "discover":
                    continue
                if dmsg.get("game") != "leidian":
                    continue
                lan_log(f"[HOST] recv discover from={daddr[0]}:{daddr[1]} -> reply host_waiting")
                _safe_send_json(
                    discover_sock,
                    daddr,
                    {"type": "host_waiting", "id": host_id, "port": port, "game": "leidian"},
                )
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        for msg, addr in _poll_lan_messages(sock):
            if msg.get("type") == "join":
                client_addr = addr
                lan_log(f"[HOST] recv join from={addr[0]}:{addr[1]} -> ack")
                _safe_send_json(sock, addr, {"type": "ack"})
            elif msg.get("type") == "input":
                if client_addr is None:
                    client_addr = addr
                if addr == client_addr:
                    remote_input.dx = int(clamp(float(msg.get("dx", 0)), -1, 1))
                    remote_input.dy = int(clamp(float(msg.get("dy", 0)), -1, 1))
                    remote_input.fire = bool(msg.get("fire", False))
                    remote_input.support = bool(msg.get("support", False))
                    remote_input.respawn = bool(msg.get("respawn", False))

        keys = pygame.key.get_pressed()
        local = LanInput(
            dx=(-1 if (keys[pygame.K_LEFT] or keys[pygame.K_a]) else 0)
            + (1 if (keys[pygame.K_RIGHT] or keys[pygame.K_d]) else 0),
            dy=(-1 if (keys[pygame.K_UP] or keys[pygame.K_w]) else 0)
            + (1 if (keys[pygame.K_DOWN] or keys[pygame.K_s]) else 0),
            fire=bool(keys[pygame.K_SPACE]),
            support=bool(keys[pygame.K_m] or keys[pygame.K_LCTRL]),
            respawn=bool(keys[pygame.K_r]),
        )
        inputs = [local, remote_input]
        game_over = all(v <= 0 for v in lives)

        for idx, p in enumerate(players):
            if (not player_alive[idx]) and lives[idx] > 0 and inputs[idx].respawn:
                player_alive[idx] = True
                p.gun_mode = GunMode.SINGLE
                p.wingmen = 0
                p.power_mul = 1.0
                p.support_left = 5
                p.rect.midbottom = spawn_pos[idx]
            if game_over:
                continue
            if phase not in (Phase.WAVE, Phase.BOSS, Phase.SETTLEMENT):
                continue
            if not player_alive[idx]:
                continue
            dx = float(inputs[idx].dx)
            dy = float(inputs[idx].dy)
            if dx != 0 and dy != 0:
                dx *= 0.707
                dy *= 0.707
            p.rect.x += int(dx * PLAYER_SPEED)
            p.rect.y += int(dy * PLAYER_SPEED)
            p.rect.clamp_ip(canvas.get_rect())
            if phase in (Phase.WAVE, Phase.BOSS) and inputs[idx].fire and now - last_fire[idx] >= BULLET_COOLDOWN_MS:
                spawn_player_bullets(
                    bullets,
                    assets,
                    float(p.rect.centerx),
                    float(p.rect.top),
                    p.gun_mode,
                    owner=idx,
                    power_mul=p.power_mul,
                )
                if p.wingmen > 0:
                    wing_fan_phase[idx] += _vf(0.55)
                    for wi in range(p.wingmen):
                        spawn_drone_pair(p.rect, assets, wing_fan_phase[idx] + wi * 0.45, drones, owner=idx)
                last_fire[idx] = now

            if (
                phase in (Phase.WAVE, Phase.BOSS)
                and inputs[idx].support
                and now - last_support[idx] >= SUPPORT_COOLDOWN_MS
                and p.support_left > 0
            ):
                p.support_left -= 1
                last_support[idx] = now
                lightning_until = now + LIGHTNING_FLASH_MS
                if phase == Phase.WAVE:
                    for e in enemies:
                        if not e.alive:
                            continue
                        e.alive = False
                        push_explosion(explosions, float(e.rect.centerx), float(e.rect.centery), now)
                        score += e.base_score + (25 if e.is_special else 0)
                        wave_kills += 1
                        if e.is_special:
                            pickups.append(
                                Pickup(float(e.rect.centerx), float(e.rect.centery), random_pickup_kind())
                            )
                elif phase == Phase.BOSS and boss and boss.alive:
                    loss = max(1, boss.hp // 3)
                    boss.hp -= loss
                    if boss.hp <= 0:
                        boss.alive = False
                        push_explosion(explosions, float(boss.rect.centerx), float(boss.rect.centery), now, big=True)
                        score += 200 + current_level * 50

        need_kills = level_kills_to_boss(current_level)
        if game_over:
            bullets.clear()
            drones.clear()
            enemies.clear()
            pickups.clear()
            boss_bullets.clear()
        if (not game_over) and phase == Phase.WAVE:
            wm_for_spawn = max(
                players[0].wingmen if player_alive[0] else 0,
                players[1].wingmen if player_alive[1] else 0,
            )
            spawn_interval = spawn_interval_with_wingmen(current_level, score, wm_for_spawn)
            if now - last_spawn >= spawn_interval and wave_kills < need_kills:
                active_arch = archetypes_for_level(assets.enemy_archetypes, current_level)
                pad = _vu(50)
                inner_lo, inner_hi = pad + _vu(24), WIDTH - pad - _vu(24)
                if inner_hi <= inner_lo:
                    inner_lo, inner_hi = pad, WIDTH - pad
                for _ in range(enemies_per_spawn_with_wingmen(current_level, wm_for_spawn)):
                    arch = pick_spawn_archetype(active_arch)
                    enemies.append(
                        Enemy(
                            random.uniform(inner_lo, inner_hi),
                            float(-_vu(20)),
                            arch,
                            special=(random.random() < SPECIAL_SPAWN_CHANCE),
                            level=current_level,
                        )
                    )
                last_spawn = now

        if not game_over:
            for b in bullets:
                b.update()
            bullets = [b for b in bullets if bullet_on_screen(b)]
            if phase == Phase.WAVE:
                homing_targets: list[object] = [e for e in enemies if e.alive]
            else:
                homing_targets = [boss] if boss and boss.alive else []
            for d in drones:
                d.update(homing_targets)
            drones = [d for d in drones if d.alive and -40 < d.y < HEIGHT + 40 and -40 < d.x < WIDTH + 40]
        if (not game_over) and phase == Phase.WAVE:
            for e in enemies:
                e.update()
            enemies = [e for e in enemies if e.alive and e.rect.top < HEIGHT + 80]
        if (not game_over) and phase == Phase.BOSS and boss:
            boss.update(clock.get_time() / 1000.0)
            if boss.alive and now - last_boss_shot >= boss_fire_interval(current_level):
                bx = boss.rect.centerx + random.randint(-12, 12)
                boss_bullets.append(
                    EnemyBullet(
                        bx,
                        boss.rect.bottom + _vu(4),
                        boss_bullet_skin,
                        boss_bullet_speed(current_level),
                    )
                )
                last_boss_shot = now
            for bb in boss_bullets:
                bb.update()
            boss_bullets = [bb for bb in boss_bullets if bb.rect.top < HEIGHT + 20]
        elif (not game_over) and phase == Phase.SETTLEMENT:
            for bb in boss_bullets:
                bb.update()
            boss_bullets = [bb for bb in boss_bullets if bb.rect.top < HEIGHT + 20]

        to_remove_b: set[int] = set()
        if (not game_over) and phase == Phase.WAVE:
            for bi, b in enumerate(bullets):
                for e in enemies:
                    if not e.alive:
                        continue
                    if b.rect.colliderect(e.rect):
                        e.hp -= b.damage
                        if e.hp <= 0:
                            e.alive = False
                            push_explosion(explosions, float(e.rect.centerx), float(e.rect.centery), now)
                            score += e.base_score + (25 if e.is_special else 0)
                            wave_kills += 1
                            if e.is_special:
                                pickups.append(
                                    Pickup(float(e.rect.centerx), float(e.rect.centery), random_pickup_kind())
                                )
                        to_remove_b.add(bi)
                        break
            for d in drones:
                if not d.alive:
                    continue
                dr = d.hit_rect()
                for e in enemies:
                    if not e.alive:
                        continue
                    if dr.colliderect(e.rect):
                        e.hp -= d.damage
                        if e.hp <= 0:
                            e.alive = False
                            push_explosion(explosions, float(e.rect.centerx), float(e.rect.centery), now)
                            score += e.base_score + (25 if e.is_special else 0)
                            wave_kills += 1
                            if e.is_special:
                                pickups.append(
                                    Pickup(float(e.rect.centerx), float(e.rect.centery), random_pickup_kind())
                                )
                        d.alive = False
                        break
        elif (not game_over) and phase == Phase.BOSS and boss and boss.alive:
            for bi, b in enumerate(bullets):
                if b.rect.colliderect(boss.rect):
                    boss.hp -= b.damage
                    if boss.hp <= 0:
                        boss.alive = False
                        push_explosion(explosions, float(boss.rect.centerx), float(boss.rect.centery), now, big=True)
                        score += 200 + current_level * 50
                    to_remove_b.add(bi)
            for d in drones:
                if not d.alive:
                    continue
                if d.hit_rect().colliderect(boss.rect):
                    boss.hp -= d.damage
                    d.alive = False
                    if boss.hp <= 0:
                        boss.alive = False
                        push_explosion(explosions, float(boss.rect.centerx), float(boss.rect.centery), now, big=True)
                        score += 200 + current_level * 50
        bullets = [b for i, b in enumerate(bullets) if i not in to_remove_b]
        drones = [d for d in drones if d.alive and -40 < d.y < HEIGHT + 40 and -40 < d.x < WIDTH + 40]

        explosions = [fx for fx in explosions if fx.alive(now)]

        if (not game_over) and phase == Phase.WAVE:
            for e in enemies:
                if not e.alive:
                    continue
                for i, p in enumerate(players):
                    if player_alive[i] and lives[i] > 0 and e.rect.colliderect(p.rect):
                        e.alive = False
                        push_explosion(explosions, float(e.rect.centerx), float(e.rect.centery), now)
                        lives[i] -= 1
                        player_alive[i] = False
                        players[i].gun_mode = GunMode.SINGLE
                        players[i].wingmen = 0
                        players[i].power_mul = 1.0
                        bullets = [bb for bb in bullets if bb.owner != i]
                        drones = [dd for dd in drones if dd.owner != i]
                        push_explosion(explosions, float(players[i].rect.centerx), float(players[i].rect.centery), now, big=True)
                        break
            enemies = [e for e in enemies if e.alive and e.rect.top < HEIGHT + 80]
        elif (not game_over) and phase == Phase.BOSS and boss:
            for bb in boss_bullets:
                for i, p in enumerate(players):
                    if player_alive[i] and lives[i] > 0 and bb.rect.colliderect(p.rect):
                        bb.rect.y = HEIGHT + 99
                        lives[i] -= 1
                        player_alive[i] = False
                        players[i].gun_mode = GunMode.SINGLE
                        players[i].wingmen = 0
                        players[i].power_mul = 1.0
                        bullets = [bbb for bbb in bullets if bbb.owner != i]
                        drones = [ddd for ddd in drones if ddd.owner != i]
                        push_explosion(explosions, float(players[i].rect.centerx), float(players[i].rect.centery), now, big=True)
            if boss.alive:
                for i, p in enumerate(players):
                    if player_alive[i] and lives[i] > 0 and boss.rect.colliderect(p.rect):
                        lives[i] -= 1
                        player_alive[i] = False
                        players[i].gun_mode = GunMode.SINGLE
                        players[i].wingmen = 0
                        players[i].power_mul = 1.0
                        bullets = [bbb for bbb in bullets if bbb.owner != i]
                        drones = [ddd for ddd in drones if ddd.owner != i]
                        push_explosion(explosions, float(players[i].rect.centerx), float(players[i].rect.centery), now, big=True)
                        p.rect.midbottom = spawn_pos[i]
            boss_bullets = [bb for bb in boss_bullets if bb.rect.top < HEIGHT + 20]

        if not game_over:
            for p in pickups:
                p.update()
            pickups = [
                p
                for p in pickups
                if p.alive and (p.kind == PickupKind.WINGMAN or p.rect.top < HEIGHT + _vu(40))
            ]
            for pick in pickups:
                if not pick.alive:
                    continue
                for i, pl in enumerate(players):
                    if lives[i] <= 0:
                        continue
                    if pick.rect.colliderect(pl.rect.inflate(_vu(4), _vu(4))):
                        apply_pickup(pl, pick.kind)
                        pick.alive = False
                        break
            pickups = [p for p in pickups if p.alive]

        if (not game_over) and phase == Phase.WAVE and wave_kills >= need_kills and not any(e.alive for e in enemies):
            enemies.clear()
            bullets.clear()
            drones.clear()
            pickups = [p for p in pickups if p.alive and p.kind == PickupKind.WINGMAN]
            boss_bullets.clear()
            boss = Boss(assets.boss_for_level(current_level), current_level - 1, max(players[0].wingmen, players[1].wingmen))
            last_boss_shot = now
            phase = Phase.BOSS
        if (not game_over) and phase == Phase.BOSS and boss and not boss.alive:
            settlement_level_score = score - score_at_level_start
            settlement_until = now + 2000
            phase = Phase.SETTLEMENT
        if (not game_over) and phase == Phase.SETTLEMENT and now >= settlement_until:
            current_level += 1
            wave_kills = 0
            enemies.clear()
            bullets.clear()
            drones.clear()
            pickups = [p for p in pickups if p.alive and p.kind == PickupKind.WINGMAN]
            boss_bullets.clear()
            boss = None
            score_at_level_start = score
            phase = Phase.WAVE

        game_over = all(v <= 0 for v in lives)

        if client_addr is not None:
            state = {
                "type": "state",
                "players": [{"x": p.rect.centerx, "y": p.rect.centery} for p in players],
                "player_alive": player_alive,
                "bullets": [
                    {"x": b.rect.centerx, "y": b.rect.centery, "owner": b.owner, "mode": b.mode.name}
                    for b in bullets
                ],
                "drones": [{"x": d.x, "y": d.y, "angle": d.angle, "owner": d.owner} for d in drones if d.alive],
                "explosions": [
                    {
                        "x": fx.x,
                        "y": fx.y,
                        "delay_ms": max(0, fx.born_ms - now),
                        "dur": fx.duration_ms,
                        "big": fx.big,
                    }
                    for fx in explosions
                ],
                "enemies": [
                    {
                        "x": e.rect.centerx,
                        "y": e.rect.centery,
                        "w": e.rect.width,
                        "h": e.rect.height,
                        "sp": e.is_special,
                        "pat": e.pattern,
                    }
                    for e in enemies
                    if e.alive
                ],
                "pickups": [
                    {"x": p.rect.centerx, "y": p.rect.centery, "kind": p.kind.name}
                    for p in pickups
                    if p.alive
                ],
                "boss": (
                    {
                        "x": boss.rect.centerx,
                        "y": boss.rect.centery,
                        "alive": boss.alive,
                        "hp": boss.hp,
                        "max_hp": boss.max_hp,
                    }
                    if boss
                    else None
                ),
                "boss_bullets": [{"x": bb.rect.centerx, "y": bb.rect.centery} for bb in boss_bullets],
                "score": score,
                "level": current_level,
                "phase": phase.name,
                "settlement_score": settlement_level_score,
                "settlement_left_ms": max(0, settlement_until - now) if phase == Phase.SETTLEMENT else 0,
                "wave_kills": wave_kills,
                "need_kills": need_kills,
                "lives": lives,
                "guns": [pl.gun_mode.name for pl in players],
                "wingmen": [pl.wingmen for pl in players],
                "supports": [pl.support_left for pl in players],
                "lightning": max(0, lightning_until - now),
                "game_over": game_over,
                "ts": time.time(),
            }
            _safe_send_json(sock, client_addr, state)

        bg_t += 1.0 / FPS
        draw_background(canvas, assets, bg_t)
        if phase == Phase.WAVE:
            for e in enemies:
                if e.alive:
                    draw_enemy(canvas, e)
        if phase == Phase.BOSS and boss and boss.alive:
            canvas.blit(boss.surf, boss.rect)
            draw_boss_hp(canvas, boss, font)
            for bb in boss_bullets:
                canvas.blit(bb.surf, bb.rect)
        if phase == Phase.SETTLEMENT:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((10, 14, 28, 170))
            canvas.blit(overlay, (0, 0))
            t = font.render(f"第 {current_level} 关完成", True, (150, 255, 195))
            canvas.blit(t, t.get_rect(center=(WIDTH // 2, _vu(120))))
            ss = font.render(f"本关得分 +{settlement_level_score}", True, (235, 240, 250))
            canvas.blit(ss, ss.get_rect(center=(WIDTH // 2, _vu(160))))
            left = max(0, (settlement_until - now) / 1000.0)
            h = font.render(f"{left:.1f}s 后进入下一关", True, (200, 210, 230))
            canvas.blit(h, h.get_rect(center=(WIDTH // 2, _vu(196))))
        for p in pickups:
            if p.alive:
                canvas.blit(p.surf, p.rect)
        for b in bullets:
            canvas.blit(b.surf, b.rect)
        for d in drones:
            if d.alive:
                rot = pygame.transform.rotate(d.base, d.angle)
                r = rot.get_rect(center=(int(d.x), int(d.y)))
                canvas.blit(rot, r)
        for fx in explosions:
            fx.draw(canvas, now)
        for i, p in enumerate(players):
            if player_alive[i]:
                p.draw(canvas)
        if now < lightning_until:
            draw_lightning_flash(canvas)
        tip1 = (
            f"主机 {bind_ip}:{port}  |  P1本机 / P2远程  |  "
            f"{phase.name} 第{current_level}关 {min(wave_kills, need_kills)}/{need_kills}  分{score}"
        )
        tip2 = f"命数 {lives[0]}|{lives[1]}  支援 {players[0].support_left}|{players[1].support_left}"
        tx = _vu(8)
        max_w = WIDTH - tx * 2
        y_next = draw_wrapped_text(canvas, font, tip1, (230, 235, 245), tx, _vu(10), max_w, line_gap=2)
        y_next = draw_wrapped_text(canvas, font, tip2, (220, 228, 240), tx, y_next, max_w, line_gap=2)
        if client_addr is None:
            y_next = draw_wrapped_text(
                canvas, font, "等待客户端加入...", (255, 220, 120), tx, y_next, max_w, line_gap=2
            )
        else:
            c = f"客户端: {client_addr[0]}:{client_addr[1]}"
            y_next = draw_wrapped_text(canvas, font, c, (180, 240, 190), tx, y_next, max_w, line_gap=2)
        if game_over:
            over = font.render("联机游戏结束：双方生命归零（关闭窗口退出）", True, (255, 120, 120))
            canvas.blit(over, (tx, y_next))
        else:
            if lives[0] > 0 and not player_alive[0]:
                t = font.render("P1已击毁，按 R 复活", True, (255, 180, 120))
                canvas.blit(t, t.get_rect(center=(players[0].rect.centerx, players[0].rect.centery - _vu(24))))
            if lives[1] > 0 and not player_alive[1]:
                t = font.render("P2已击毁，按 R 复活", True, (255, 180, 120))
                canvas.blit(t, t.get_rect(center=(players[1].rect.centerx, players[1].rect.centery - _vu(24))))

        pygame.transform.smoothscale(canvas, (win_w, win_h), screen)
        pygame.display.flip()
        clock.tick(FPS)

    sock.close()
    discover_sock.close()
    pygame.quit()
    sys.exit(0)


def run_lan_client(server_ip: str, port: int) -> None:
    pygame.init()
    win_w, win_h = compute_window_size(WIDTH, HEIGHT)
    screen = pygame.display.set_mode((win_w, win_h))
    canvas = pygame.Surface((WIDTH, HEIGHT))
    pygame.display.set_caption("雷电 · 局域网客户端")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("microsoftyahei", max(12, _vu(18)))
    font_small = pygame.font.SysFont("microsoftyahei", max(11, _vu(15)))
    assets = Assets()
    player_surf = assets.player
    bullet_surf_by_mode = {m.name: v[0] for m, v in assets.gun_lasers.items()}
    default_bullet_surf = assets.gun_lasers[GunMode.SINGLE][0]
    boss_bullet_surf = make_boss_bullet_surf(_vu(9), _vu(19))
    drone_base_surf = assets.wing_missile
    pattern_enemy_surf: dict[str, pygame.Surface] = {}
    for a in assets.enemy_archetypes:
        if a.pattern not in pattern_enemy_surf:
            pattern_enemy_surf[a.pattern] = a.surf
    fallback_enemy_surf = (
        assets.enemy_archetypes[0].surf
        if assets.enemy_archetypes
        else load_scaled("enemy_red.png", ENEMY_MAX_W, ENEMY_MAX_H)
    )
    boss_surf_cache: dict[int, pygame.Surface] = {}
    pickup_surf_cache = {k.name: make_pickup_surface(k) for k in PickupKind}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    server_addr = (server_ip, port)
    lan_log(f"[CLIENT] start target={server_ip}:{port} pid={os.getpid()}")
    _safe_send_json(sock, server_addr, {"type": "join"})
    lan_log(f"[CLIENT] send join -> {server_ip}:{port}")
    last_state: dict[str, object] | None = None
    bg_t = 0.0
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        keys = pygame.key.get_pressed()
        dx = (-1 if (keys[pygame.K_LEFT] or keys[pygame.K_a]) else 0) + (
            1 if (keys[pygame.K_RIGHT] or keys[pygame.K_d]) else 0
        )
        dy = (-1 if (keys[pygame.K_UP] or keys[pygame.K_w]) else 0) + (
            1 if (keys[pygame.K_DOWN] or keys[pygame.K_s]) else 0
        )
        fire = bool(keys[pygame.K_SPACE])
        support = bool(keys[pygame.K_m] or keys[pygame.K_LCTRL])
        respawn = bool(keys[pygame.K_r])
        _safe_send_json(
            sock,
            server_addr,
            {"type": "input", "dx": dx, "dy": dy, "fire": fire, "support": support, "respawn": respawn},
        )

        for msg, _ in _poll_lan_messages(sock):
            if msg.get("type") == "ack":
                lan_log(f"[CLIENT] recv ack from={server_ip}:{port}")
            if msg.get("type") == "state":
                last_state = msg

        bg_t += 1.0 / FPS
        draw_background(canvas, assets, bg_t)
        if last_state:
            players = last_state.get("players", [])
            player_alive = last_state.get("player_alive", [True, True])
            bullets = last_state.get("bullets", [])
            drones = last_state.get("drones", [])
            explosions = last_state.get("explosions", [])
            enemies = last_state.get("enemies", [])
            pickups = last_state.get("pickups", [])
            guns = last_state.get("guns", ["SINGLE", "SINGLE"])
            wingmen = last_state.get("wingmen", [0, 0])
            supports = last_state.get("supports", [12, 12])
            phase_name = str(last_state.get("phase", "WAVE"))
            settlement_score = int(last_state.get("settlement_score", 0))
            settlement_left_ms = int(last_state.get("settlement_left_ms", 0))
            boss_info = last_state.get("boss", None)
            boss_bullets = last_state.get("boss_bullets", [])
            lightning_left = int(last_state.get("lightning", 0))
            if isinstance(players, list):
                for i, p in enumerate(players):
                    if not isinstance(p, dict):
                        continue
                    alive = True
                    if isinstance(player_alive, list) and i < len(player_alive):
                        alive = bool(player_alive[i])
                    if not alive:
                        continue
                    x = int(p.get("x", WIDTH // 2))
                    y = int(p.get("y", HEIGHT - _vu(40)))
                    r = player_surf.get_rect(center=(x, y))
                    canvas.blit(player_surf, r)
                    wm = 0
                    if isinstance(wingmen, list) and i < len(wingmen):
                        wm = int(wingmen[i])
                    if wm > 0:
                        mini = pygame.transform.smoothscale(player_surf, (_vu(18), _vu(16)))
                        ly = r.bottom - _vu(6)
                        ox = _vu(30)
                        canvas.blit(mini, mini.get_rect(midright=(r.left + ox // 2, ly)))
                        canvas.blit(mini, mini.get_rect(midleft=(r.right - ox // 2, ly)))
            if isinstance(bullets, list):
                for b in bullets:
                    if not isinstance(b, dict):
                        continue
                    x = int(b.get("x", WIDTH // 2))
                    y = int(b.get("y", HEIGHT // 2))
                    mode_name = str(b.get("mode", GunMode.SINGLE.name))
                    bullet_surf = bullet_surf_by_mode.get(mode_name, default_bullet_surf)
                    r = bullet_surf.get_rect(center=(x, y))
                    canvas.blit(bullet_surf, r)
            if isinstance(drones, list):
                for d in drones:
                    if not isinstance(d, dict):
                        continue
                    x = int(d.get("x", WIDTH // 2))
                    y = int(d.get("y", HEIGHT // 2))
                    angle = float(d.get("angle", 0.0))
                    rot = pygame.transform.rotate(drone_base_surf, angle)
                    rr = rot.get_rect(center=(x, y))
                    canvas.blit(rot, rr)
            if isinstance(explosions, list):
                now_ms = pygame.time.get_ticks()
                for fx in explosions:
                    if not isinstance(fx, dict):
                        continue
                    # 使用相对时间，避免主客机本地 tick 不一致导致特效丢失
                    if "delay_ms" in fx:
                        born = now_ms + max(0, int(fx.get("delay_ms", 0)))
                    else:
                        # 兼容旧字段
                        born = int(fx.get("born", now_ms))
                    dur = max(1, int(fx.get("dur", EXPLOSION_DURATION_MS)))
                    ex = ExplosionFx(
                        x=float(fx.get("x", WIDTH // 2)),
                        y=float(fx.get("y", HEIGHT // 2)),
                        born_ms=born,
                        duration_ms=dur,
                        radius=_vf(16.0 if bool(fx.get("big", False)) else 10.0),
                        big=bool(fx.get("big", False)),
                    )
                    if ex.alive(now_ms):
                        ex.draw(canvas, now_ms)
            if isinstance(enemies, list):
                for e in enemies:
                    if not isinstance(e, dict):
                        continue
                    x = int(e.get("x", WIDTH // 2))
                    y = int(e.get("y", HEIGHT // 2))
                    sp = bool(e.get("sp", False))
                    pat = str(e.get("pat", "standard"))
                    enemy_surf = pattern_enemy_surf.get(pat, fallback_enemy_surf)
                    rc = enemy_surf.get_rect(center=(x, y))
                    canvas.blit(enemy_surf, rc)
                    if sp:
                        pygame.draw.rect(
                            canvas,
                            (255, 245, 160),
                            rc.inflate(_vu(6), _vu(6)),
                            width=max(1, _vu(2)),
                            border_radius=max(2, _vu(4)),
                        )
            if isinstance(pickups, list):
                for p in pickups:
                    if not isinstance(p, dict):
                        continue
                    x = int(p.get("x", WIDTH // 2))
                    y = int(p.get("y", HEIGHT // 2))
                    kind_name = str(p.get("kind", PickupKind.GUN_SINGLE.name))
                    ps = pickup_surf_cache.get(kind_name, pickup_surf_cache[PickupKind.GUN_SINGLE.name])
                    pr = ps.get_rect(center=(x, y))
                    canvas.blit(ps, pr)
            if isinstance(boss_info, dict) and bool(boss_info.get("alive", False)):
                level_for_boss = int(last_state.get("level", 1))
                if level_for_boss not in boss_surf_cache:
                    boss_surf_cache[level_for_boss] = assets.boss_for_level(level_for_boss)
                bs = boss_surf_cache[level_for_boss]
                bx = int(boss_info.get("x", WIDTH // 2))
                by = int(boss_info.get("y", _vu(95)))
                br = bs.get_rect(center=(bx, by))
                canvas.blit(bs, br)
                bmax = max(1, int(boss_info.get("max_hp", 1)))
                bhp = max(0, int(boss_info.get("hp", 0)))
                bar_w = min(int(WIDTH * 0.75), WIDTH - _vu(40))
                x0 = (WIDTH - bar_w) // 2
                y0 = br.bottom + _vu(8)
                bh = _vu(10)
                pygame.draw.rect(canvas, (40, 40, 55), (x0, y0, bar_w, bh), border_radius=3)
                pygame.draw.rect(canvas, (220, 60, 90), (x0, y0, int(bar_w * (bhp / bmax)), bh), border_radius=3)
                pygame.draw.rect(canvas, (255, 180, 190), (x0, y0, bar_w, bh), 1, border_radius=3)
            if isinstance(boss_bullets, list):
                for bb in boss_bullets:
                    if not isinstance(bb, dict):
                        continue
                    x = int(bb.get("x", WIDTH // 2))
                    y = int(bb.get("y", HEIGHT // 2))
                    r = boss_bullet_surf.get_rect(center=(x, y))
                    canvas.blit(boss_bullet_surf, r)
            if phase_name == Phase.SETTLEMENT.name:
                overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
                overlay.fill((10, 14, 28, 170))
                canvas.blit(overlay, (0, 0))
                t = font.render(f"第 {level} 关完成", True, (150, 255, 195))
                canvas.blit(t, t.get_rect(center=(WIDTH // 2, _vu(120))))
                ss = font.render(f"本关得分 +{settlement_score}", True, (235, 240, 250))
                canvas.blit(ss, ss.get_rect(center=(WIDTH // 2, _vu(160))))
                h = font.render(f"{max(0, settlement_left_ms) / 1000.0:.1f}s 后进入下一关", True, (200, 210, 230))
                canvas.blit(h, h.get_rect(center=(WIDTH // 2, _vu(196))))
            score = int(last_state.get("score", 0))
            level = int(last_state.get("level", 1))
            wave_kills = int(last_state.get("wave_kills", 0))
            need_kills = int(last_state.get("need_kills", 1))
            lives = last_state.get("lives", [3, 3])
            if not isinstance(lives, list) or len(lives) < 2:
                lives = [3, 3]
            if not isinstance(supports, list) or len(supports) < 2:
                supports = [12, 12]
            game_over = bool(last_state.get("game_over", False))
            gun_labels = []
            if isinstance(guns, list):
                for g in guns[:2]:
                    try:
                        gun_labels.append(gun_mode_label(GunMode[str(g)]))
                    except Exception:
                        gun_labels.append("单发")
            while len(gun_labels) < 2:
                gun_labels.append("单发")
            st = f"已连接主机 {server_ip}:{port}  |  你是P2(控制右侧飞机)"
            tx = _vu(8)
            max_w = WIDTH - tx * 2
            y_next = draw_wrapped_text(canvas, font, st, (180, 240, 190), tx, _vu(10), max_w, line_gap=2)
            hud = (
                f"{phase_name} 第{level}关 {min(wave_kills, need_kills)}/{need_kills}  分{score}  "
                f"命数{lives[0]}|{lives[1]}  援{supports[0]}|{supports[1]}  P1:{gun_labels[0]} P2:{gun_labels[1]}"
            )
            y_next = draw_wrapped_text(canvas, font, hud, (230, 235, 245), tx, y_next, max_w, line_gap=2)
            if lightning_left > 0:
                draw_lightning_flash(canvas)
            if game_over:
                y_next = draw_wrapped_text(
                    canvas, font, "联机游戏结束：双方生命归零", (255, 120, 120), tx, y_next, max_w, line_gap=2
                )
            else:
                if isinstance(player_alive, list) and len(player_alive) >= 2:
                    if not bool(player_alive[0]):
                        y_next = draw_wrapped_text(
                            canvas,
                            font_small,
                            "P1已击毁，按R复活",
                            (255, 180, 120),
                            tx,
                            y_next,
                            max_w,
                            line_gap=2,
                        )
                    if not bool(player_alive[1]):
                        y_next = draw_wrapped_text(
                            canvas,
                            font_small,
                            "P2已击毁，按R复活",
                            (255, 180, 120),
                            tx,
                            y_next,
                            max_w,
                            line_gap=2,
                        )
        else:
            st = f"连接中 {server_ip}:{port} ... 请确认主机已启动"
            draw_wrapped_text(canvas, font, st, (255, 220, 120), _vu(8), _vu(10), WIDTH - _vu(16), line_gap=2)

        pygame.transform.smoothscale(canvas, (win_w, win_h), screen)
        pygame.display.flip()
        clock.tick(FPS)

    sock.close()
    pygame.quit()
    sys.exit(0)


def level_kills_to_boss(level: int) -> int:
    return WAVE_KILLS_LEVEL1 + (level - 1) * WAVE_KILLS_STEP


def level_spawn_interval(level: int, score: int) -> int:
    lv = max(1, level)
    base_ms = ENEMY_SPAWN_MS_START - (lv - 1) * ENEMY_SPAWN_MS_PER_LEVEL
    score_adj = min(ENEMY_SPAWN_SCORE_CAP_MS, score // ENEMY_SPAWN_SCORE_DIV)
    return max(ENEMY_SPAWN_MS_FLOOR, int(base_ms - score_adj))


def _spawn_tier(level: int) -> int:
    return (max(1, level) - 1) // ENEMY_SPAWN_BATCH_EVERY


def enemies_per_spawn_tick(level: int) -> int:
    """每关刷怪 tick 架数：随关卡分档递增，每档 +ENEMY_SPAWN_COUNT_STEP。"""
    tier = _spawn_tier(level)
    n = ENEMY_SPAWN_COUNT_START + tier * ENEMY_SPAWN_COUNT_STEP
    return max(1, min(n, ENEMY_SPAWN_COUNT_CAP))


def spawn_interval_with_wingmen(level: int, score: int, wingmen: int) -> int:
    """有僚机时缩短刷怪间隔。"""
    ms = level_spawn_interval(level, score)
    if wingmen > 0:
        ms = int(ms * WINGMAN_SPAWN_INTERVAL_MUL)
    return max(ENEMY_SPAWN_MS_FLOOR, ms)


def enemies_per_spawn_with_wingmen(level: int, wingmen: int) -> int:
    """无僚机用分档递增；有僚机在同档基础上再加额外架数（仍递增）。"""
    n = enemies_per_spawn_tick(level)
    if wingmen > 0:
        tier = _spawn_tier(level)
        n += WINGMAN_SPAWN_EXTRA_BASE + tier * WINGMAN_SPAWN_EXTRA_PER_TIER
        n = min(n, ENEMY_SPAWN_ABSOLUTE_CAP)
    return max(1, n)


def boss_fire_interval(level: int) -> int:
    return max(260, 820 - min(level, 45) * 11)


def boss_bullet_speed(level: int) -> float:
    return min(12.5, 3.8 + min(level, 48) * 0.18)


def draw_background(surf: pygame.Surface, assets: Assets, t: float) -> None:
    th = assets.bg_tile.get_height()
    yoff = int((t * 80) % th)
    for y in range(-th, HEIGHT + th, th):
        surf.blit(assets.bg_tile, (0, y + yoff))
    for i in range(36):
        sx = (i * 97 + int(t * 40)) % WIDTH
        sy = (i * 53 + int(t * 80)) % HEIGHT
        pygame.draw.circle(surf, (210, 220, 255), (sx, sy), 1)


def draw_lightning_flash(surf: pygame.Surface) -> None:
    """呼叫支援时的全屏闪电效果（每帧随机，形成风暴感）。"""
    lw = max(1, _vu(2))
    for _ in range(16):
        x = float(random.randint(0, WIDTH - 1))
        y = 0.0
        while y < HEIGHT:
            ny = y + float(random.randint(_vu(45), _vu(130)))
            nx = x + float(random.randint(-_vu(40), _vu(40)))
            ny = min(float(HEIGHT), ny)
            pygame.draw.line(surf, (255, 255, 200), (int(x), int(y)), (int(nx), int(ny)), lw)
            pygame.draw.line(surf, (200, 230, 255), (int(x), int(y)), (int(nx), int(ny)), 1)
            x, y = nx, ny
    veil = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    veil.fill((220, 235, 255, 55))
    surf.blit(veil, (0, 0))


def draw_wrapped_text(
    surf: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: tuple[int, int, int],
    x: int,
    y: int,
    max_width: int,
    line_gap: int = 4,
) -> int:
    """按像素宽度自动换行，返回绘制后的 y。"""
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = w if not cur else f"{cur} {w}"
        if font.size(cand)[0] <= max_width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    yy = y
    for ln in lines:
        surf.blit(font.render(ln, True, color), (x, yy))
        yy += font.get_height() + line_gap
    return yy


def make_boss_bullet_surf(w: int, h: int) -> pygame.Surface:
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    s.fill((255, 100, 90, 230))
    pygame.draw.rect(s, (255, 200, 120), s.get_rect().inflate(-2, -2), border_radius=2)
    return s


def draw_boss_hp(surf: pygame.Surface, boss: Boss, font: pygame.font.Font) -> None:
    if not boss.alive:
        return
    bar_w = min(int(WIDTH * 0.75), WIDTH - _vu(40))
    x0 = (WIDTH - bar_w) // 2
    y0 = boss.rect.bottom + _vu(8)
    bh = _vu(10)
    pygame.draw.rect(surf, (40, 40, 55), (x0, y0, bar_w, bh), border_radius=3)
    ratio = boss.hp / max(1, boss.max_hp)
    pygame.draw.rect(surf, (220, 60, 90), (x0, y0, int(bar_w * ratio), bh), border_radius=3)
    pygame.draw.rect(surf, (255, 180, 190), (x0, y0, bar_w, bh), 1, border_radius=3)
    tag = font.render("BOSS", True, (255, 200, 210))
    surf.blit(tag, tag.get_rect(midbottom=(WIDTH // 2, y0 - _vu(2))))


def random_pickup_kind() -> PickupKind:
    return random.choice(
        [
            PickupKind.GUN_SINGLE,
            PickupKind.GUN_DOUBLE,
            PickupKind.GUN_TRIPLE,
            PickupKind.GUN_SPREAD,
            PickupKind.WINGMAN,
        ]
    )


def apply_pickup(player: Player, kind: PickupKind) -> None:
    if kind == PickupKind.GUN_SINGLE:
        player.gun_mode = GunMode.SINGLE
        player.power_mul = min(3.5, player.power_mul + 0.12)
    elif kind == PickupKind.GUN_DOUBLE:
        player.gun_mode = GunMode.DOUBLE
        player.power_mul = min(3.5, player.power_mul + 0.12)
    elif kind == PickupKind.GUN_TRIPLE:
        player.gun_mode = GunMode.TRIPLE
        player.power_mul = min(3.5, player.power_mul + 0.12)
    elif kind == PickupKind.GUN_SPREAD:
        player.gun_mode = GunMode.SPREAD
        player.power_mul = min(3.5, player.power_mul + 0.12)
    elif kind == PickupKind.WINGMAN:
        player.wingmen += 1


def bullet_on_screen(b: AngledBullet) -> bool:
    m = 80
    return -m < b.rect.centerx < WIDTH + m and -m < b.rect.centery < HEIGHT + m


def reset_level_state(
    assets: Assets,
    _level: int,
    gun_mode: GunMode,
    wingmen: int,
) -> tuple[
    Player,
    list[AngledBullet],
    list[DroneMissile],
    list[Enemy],
    list[EnemyBullet],
    list[Pickup],
    Boss | None,
    int,
    int,
    int,
    int,
]:
    player = Player(assets.player, gun_mode=gun_mode, wingmen=wingmen)
    bullets: list[AngledBullet] = []
    drones: list[DroneMissile] = []
    enemies: list[Enemy] = []
    boss_bullets: list[EnemyBullet] = []
    pickups: list[Pickup] = []
    explosions: list[ExplosionFx] = []
    boss: Boss | None = None
    player_alive = True
    wave_kills = 0
    t = pygame.time.get_ticks()
    last_fire = last_support = last_spawn = t
    return (
        player,
        bullets,
        drones,
        enemies,
        boss_bullets,
        pickups,
        boss,
        wave_kills,
        last_fire,
        last_support,
        last_spawn,
    )


def draw_enemy(surf: pygame.Surface, e: Enemy) -> None:
    surf.blit(e.surf, e.rect)
    if e.is_special:
        g = _vu(10)
        glow = e.rect.inflate(g, g)
        pygame.draw.rect(surf, (255, 230, 80), glow, width=max(1, _vu(2)), border_radius=_vu(6))


@dataclass
class ExplosionFx:
    x: float
    y: float
    born_ms: int
    duration_ms: int
    radius: float
    big: bool = False

    def alive(self, now_ms: int) -> bool:
        age = now_ms - self.born_ms
        return age < self.duration_ms

    def draw(self, surf: pygame.Surface, now_ms: int) -> None:
        age = max(0, now_ms - self.born_ms)
        if now_ms < self.born_ms:
            return
        t = clamp(age / max(1, self.duration_ms), 0.0, 1.0)
        r = int(self.radius * (0.45 + 1.35 * t))
        alpha = int(220 * (1.0 - t))
        ring_alpha = int(180 * (1.0 - t))
        core_col = (255, 250, 190, max(0, alpha))
        ring_col = (255, 165, 90, max(0, ring_alpha))
        fx = pygame.Surface((max(2, r * 2 + 8), max(2, r * 2 + 8)), pygame.SRCALPHA)
        center = (fx.get_width() // 2, fx.get_height() // 2)
        pygame.draw.circle(fx, core_col, center, max(2, int(r * 0.52)))
        pygame.draw.circle(fx, ring_col, center, max(2, r), width=max(1, _vu(2)))
        if self.big:
            pygame.draw.circle(
                fx,
                (255, 235, 140, max(0, int(130 * (1.0 - t)))),
                center,
                max(2, int(r * 1.25)),
                width=max(1, _vu(1)),
            )
        surf.blit(fx, fx.get_rect(center=(int(self.x), int(self.y))))


def push_explosion(
    explosions: list[ExplosionFx],
    x: float,
    y: float,
    now_ms: int,
    *,
    big: bool = False,
) -> None:
    # 多段爆炸：中心主爆 + 若干随机偏移子爆，增强打击感
    base_r = _vf(16.0 if big else 10.0)
    dur = int(EXPLOSION_DURATION_MS * (1.2 if big else 1.0))
    seg = 5 if big else 3
    for i in range(seg):
        t_off = i * 42
        j = _vf(16.0 if big else 9.0) * (0.15 + i * 0.32)
        dx = 0.0 if i == 0 else random.uniform(-j, j)
        dy = 0.0 if i == 0 else random.uniform(-j, j)
        rr = base_r * (1.0 - min(0.45, i * 0.12))
        explosions.append(
            ExplosionFx(
                x=x + dx,
                y=y + dy,
                born_ms=now_ms + t_off,
                duration_ms=max(90, dur - i * 18),
                radius=rr,
                big=big,
            )
        )


def resolve_enemy_death(
    e: Enemy,
    pickups: list[Pickup],
    score: int,
    wave_kills: int,
    total_enemies_shot: int,
) -> tuple[int, int, int]:
    points = e.base_score + (25 if e.is_special else 0)
    score += points
    wave_kills += 1
    total_enemies_shot += 1
    if e.is_special:
        pickups.append(Pickup(float(e.rect.centerx), float(e.rect.centery), random_pickup_kind()))
    return score, wave_kills, total_enemies_shot


def main() -> None:
    pygame.init()
    win_w, win_h = compute_window_size(WIDTH, HEIGHT)
    screen = pygame.display.set_mode((win_w, win_h))
    canvas = pygame.Surface((WIDTH, HEIGHT))
    pygame.display.set_caption("雷电 · Raiden")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("microsoftyahei", max(12, _vu(18)))
    font_mid = pygame.font.SysFont("microsoftyahei", max(14, _vu(22)))
    font_big = pygame.font.SysFont("microsoftyahei", max(22, _vu(34)))

    assets = Assets()
    boss_bullet_skin = make_boss_bullet_surf(_vu(9), _vu(19))

    current_level = 1
    score = 0
    score_at_level_start = 0
    lives = 3
    phase = Phase.WAVE
    wave_kills = 0
    total_enemies_shot = 0

    player = Player(assets.player)
    bullets: list[AngledBullet] = []
    drones: list[DroneMissile] = []
    enemies: list[Enemy] = []
    boss_bullets: list[EnemyBullet] = []
    pickups: list[Pickup] = []
    boss: Boss | None = None

    last_boss_shot = 0
    wing_fan_phase = 0.0
    lightning_until = 0
    running = True
    bg_t = 0.0

    settlement_level_score = 0
    settlement_all_cleared = False
    settlement_until = 0

    t0 = pygame.time.get_ticks()
    last_fire = last_support = last_spawn = t0

    def homing_targets() -> list[object]:
        t: list[object] = [e for e in enemies if e.alive]
        if boss and boss.alive:
            t.append(boss)
        return t

    def process_bullet_hits() -> None:
        nonlocal score, wave_kills, total_enemies_shot
        to_remove_b: set[int] = set()
        for bi, b in enumerate(bullets):
            for e in enemies:
                if not e.alive:
                    continue
                if b.rect.colliderect(e.rect):
                    e.hp -= b.damage
                    if e.hp <= 0:
                        e.alive = False
                        push_explosion(explosions, float(e.rect.centerx), float(e.rect.centery), now)
                        score, wave_kills, total_enemies_shot = resolve_enemy_death(
                            e, pickups, score, wave_kills, total_enemies_shot
                        )
                    to_remove_b.add(bi)
                    break
        nonlocal_b = [b for i, b in enumerate(bullets) if i not in to_remove_b]
        bullets.clear()
        bullets.extend(nonlocal_b)

    def process_drone_hits() -> None:
        nonlocal score, wave_kills, total_enemies_shot
        for d in drones:
            if not d.alive:
                continue
            dr = d.hit_rect()
            for e in enemies:
                if e.alive and dr.colliderect(e.rect):
                    e.hp -= d.damage
                    if e.hp <= 0:
                        e.alive = False
                        push_explosion(explosions, float(e.rect.centerx), float(e.rect.centery), now)
                        score, wave_kills, total_enemies_shot = resolve_enemy_death(
                            e, pickups, score, wave_kills, total_enemies_shot
                        )
                    d.alive = False
                    break

    def fire_support_lightning() -> None:
        nonlocal score, wave_kills, total_enemies_shot, lightning_until
        lightning_until = now + LIGHTNING_FLASH_MS
        if phase == Phase.WAVE:
            for e in list(enemies):
                if not e.alive:
                    continue
                push_explosion(explosions, float(e.rect.centerx), float(e.rect.centery), now)
                score, wave_kills, total_enemies_shot = resolve_enemy_death(
                    e, pickups, score, wave_kills, total_enemies_shot
                )
                e.alive = False
        elif phase == Phase.BOSS and boss and boss.alive:
            loss = max(1, boss.hp // 3)
            boss.hp -= loss
            if boss.hp <= 0:
                boss.alive = False
                push_explosion(explosions, float(boss.rect.centerx), float(boss.rect.centery), now, big=True)
                score += 200 + current_level * 50

    while running:
        now = pygame.time.get_ticks()
        active_wingmen = player.wingmen if player_alive else 0
        spawn_interval = spawn_interval_with_wingmen(current_level, score, active_wingmen)
        need_kills = level_kills_to_boss(current_level)
        dt = clock.get_time() / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.KEYDOWN:
                if phase == Phase.GAME_OVER:
                    if event.key == pygame.K_r:
                        current_level = 1
                        score = 0
                        score_at_level_start = 0
                        lives = 3
                        phase = Phase.WAVE
                        wave_kills = 0
                        total_enemies_shot = 0
                        wing_fan_phase = 0.0
                        (
                            player,
                            bullets,
                            drones,
                            enemies,
                            boss_bullets,
                            pickups,
                            boss,
                            wave_kills,
                            last_fire,
                            last_support,
                            last_spawn,
                        ) = reset_level_state(assets, current_level, GunMode.SINGLE, 0)
                        player_alive = True
                        last_boss_shot = now
                    elif event.key == pygame.K_ESCAPE:
                        running = False

                elif phase == Phase.SETTLEMENT:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                elif phase in (Phase.WAVE, Phase.BOSS):
                    if (not player_alive) and lives > 0 and event.key == pygame.K_r:
                        player_alive = True
                        player.gun_mode = GunMode.SINGLE
                        player.wingmen = 0
                        player.power_mul = 1.0
                        player.support_left = 5
                        player.rect.midbottom = (WIDTH // 2, HEIGHT - _vu(40))

        if phase == Phase.WAVE:
            keys = pygame.key.get_pressed()
            if player_alive:
                dx = dy = 0
                if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                    dx -= 1
                if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                    dx += 1
                if keys[pygame.K_UP] or keys[pygame.K_w]:
                    dy -= 1
                if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                    dy += 1
                if dx != 0 and dy != 0:
                    dx *= 0.707
                    dy *= 0.707
                player.rect.x += int(dx * PLAYER_SPEED)
                player.rect.y += int(dy * PLAYER_SPEED)
                player.rect.clamp_ip(canvas.get_rect())

                if keys[pygame.K_SPACE] and now - last_fire >= BULLET_COOLDOWN_MS:
                    spawn_player_bullets(
                        bullets,
                        assets,
                        float(player.rect.centerx),
                        float(player.rect.top),
                        player.gun_mode,
                        power_mul=player.power_mul,
                    )
                    if player.wingmen > 0:
                        wing_fan_phase += _vf(0.55)
                        for wi in range(player.wingmen):
                            spawn_drone_pair(player.rect, assets, wing_fan_phase + wi * 0.45, drones)
                    last_fire = now

                if (
                    (keys[pygame.K_m] or keys[pygame.K_LCTRL])
                    and now - last_support >= SUPPORT_COOLDOWN_MS
                    and player.support_left > 0
                ):
                    player.support_left -= 1
                    last_support = now
                    fire_support_lightning()

            if now - last_spawn >= spawn_interval and wave_kills < need_kills:
                active_arch = archetypes_for_level(assets.enemy_archetypes, current_level)
                pad = _vu(50)
                inner_lo, inner_hi = pad + _vu(24), WIDTH - pad - _vu(24)
                if inner_hi <= inner_lo:
                    inner_lo, inner_hi = pad, WIDTH - pad
                for _ in range(enemies_per_spawn_with_wingmen(current_level, active_wingmen)):
                    arch = pick_spawn_archetype(active_arch)
                    sp = random.random() < SPECIAL_SPAWN_CHANCE
                    enemies.append(
                        Enemy(
                            random.uniform(inner_lo, inner_hi),
                            float(-_vu(20)),
                            arch,
                            special=sp,
                            level=current_level,
                        )
                    )
                last_spawn = now

            for b in bullets:
                b.update()
            bullets = [b for b in bullets if bullet_on_screen(b)]

            for d in drones:
                d.update(homing_targets())
            drones = [d for d in drones if d.alive and -40 < d.y < HEIGHT + 40 and -40 < d.x < WIDTH + 40]

            for p in pickups:
                p.update()
            pickups = [
                p
                for p in pickups
                if p.alive and (p.kind == PickupKind.WINGMAN or p.rect.top < HEIGHT + _vu(40))
            ]

            for e in enemies:
                e.update()
            enemies = [e for e in enemies if e.alive and e.rect.top < HEIGHT + 80]

            for p in pickups:
                if p.alive and p.rect.colliderect(player.rect.inflate(_vu(4), _vu(4))):
                    apply_pickup(player, p.kind)
                    p.alive = False
            pickups = [p for p in pickups if p.alive]

            process_bullet_hits()
            process_drone_hits()

            for e in enemies:
                if player_alive and e.alive and e.rect.colliderect(player.rect):
                    e.alive = False
                    push_explosion(explosions, float(e.rect.centerx), float(e.rect.centery), now)
                    push_explosion(explosions, float(player.rect.centerx), float(player.rect.centery), now, big=True)
                    player_alive = False
                    lives -= 1
                    player.gun_mode = GunMode.SINGLE
                    player.wingmen = 0
                    player.power_mul = 1.0
                    bullets.clear()
                    drones.clear()
                    if lives <= 0:
                        phase = Phase.GAME_OVER

            if (
                wave_kills >= need_kills
                and phase == Phase.WAVE
                and not any(e.alive for e in enemies)
            ):
                enemies.clear()
                bullets.clear()
                drones.clear()
                boss_bullets.clear()
                boss = Boss(
                    assets.boss_for_level(current_level),
                    current_level - 1,
                    player.wingmen,
                )
                last_boss_shot = now
                phase = Phase.BOSS

            bg_t += 1.0 / FPS

        elif phase == Phase.BOSS and boss:
            keys = pygame.key.get_pressed()
            if player_alive:
                dx = dy = 0
                if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                    dx -= 1
                if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                    dx += 1
                if keys[pygame.K_UP] or keys[pygame.K_w]:
                    dy -= 1
                if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                    dy += 1
                if dx != 0 and dy != 0:
                    dx *= 0.707
                    dy *= 0.707
                player.rect.x += int(dx * PLAYER_SPEED)
                player.rect.y += int(dy * PLAYER_SPEED)
                player.rect.clamp_ip(canvas.get_rect())

                if keys[pygame.K_SPACE] and now - last_fire >= BULLET_COOLDOWN_MS:
                    spawn_player_bullets(
                        bullets,
                        assets,
                        float(player.rect.centerx),
                        float(player.rect.top),
                        player.gun_mode,
                        power_mul=player.power_mul,
                    )
                    if player.wingmen > 0:
                        wing_fan_phase += _vf(0.55)
                        for wi in range(player.wingmen):
                            spawn_drone_pair(player.rect, assets, wing_fan_phase + wi * 0.45, drones)
                    last_fire = now

                if (
                    (keys[pygame.K_m] or keys[pygame.K_LCTRL])
                    and now - last_support >= SUPPORT_COOLDOWN_MS
                    and player.support_left > 0
                ):
                    player.support_left -= 1
                    last_support = now
                    fire_support_lightning()

            boss.update(dt)
            if boss.alive and now - last_boss_shot >= boss_fire_interval(current_level):
                bx = boss.rect.centerx + random.randint(-12, 12)
                boss_bullets.append(
                    EnemyBullet(
                        bx,
                        boss.rect.bottom + _vu(4),
                        boss_bullet_skin,
                        boss_bullet_speed(current_level),
                    )
                )
                last_boss_shot = now

            for b in bullets:
                b.update()
            bullets = [b for b in bullets if bullet_on_screen(b)]

            for d in drones:
                d.update(homing_targets())
            drones = [d for d in drones if d.alive and -40 < d.y < HEIGHT + 40 and -40 < d.x < WIDTH + 40]

            for p in pickups:
                p.update()
            pickups = [
                p
                for p in pickups
                if p.alive and (p.kind == PickupKind.WINGMAN or p.rect.top < HEIGHT + _vu(40))
            ]
            for p in pickups:
                if p.alive and p.rect.colliderect(player.rect.inflate(_vu(4), _vu(4))):
                    apply_pickup(player, p.kind)
                    p.alive = False
            pickups = [p for p in pickups if p.alive]

            for bb in boss_bullets:
                bb.update()
            boss_bullets = [bb for bb in boss_bullets if bb.rect.top < HEIGHT + 20]

            to_remove_b = set()
            for bi, b in enumerate(bullets):
                if boss.alive and b.rect.colliderect(boss.rect):
                    boss.hp -= b.damage
                    if boss.hp <= 0:
                        boss.alive = False
                        push_explosion(explosions, float(boss.rect.centerx), float(boss.rect.centery), now, big=True)
                        score += 200 + current_level * 50
                    to_remove_b.add(bi)
            bullets = [b for i, b in enumerate(bullets) if i not in to_remove_b]

            for d in drones:
                if not d.alive or not boss.alive:
                    continue
                if d.hit_rect().colliderect(boss.rect):
                    boss.hp -= d.damage
                    d.alive = False
                    if boss.hp <= 0:
                        boss.alive = False
                        push_explosion(explosions, float(boss.rect.centerx), float(boss.rect.centery), now, big=True)
                        score += 200 + current_level * 50

            for bb in boss_bullets:
                if player_alive and bb.rect.colliderect(player.rect):
                    bb.rect.y = HEIGHT + 99
                    push_explosion(explosions, float(player.rect.centerx), float(player.rect.centery), now, big=True)
                    player_alive = False
                    lives -= 1
                    player.gun_mode = GunMode.SINGLE
                    player.wingmen = 0
                    player.power_mul = 1.0
                    bullets.clear()
                    drones.clear()
                    if lives <= 0:
                        phase = Phase.GAME_OVER

            if player_alive and boss.alive and boss.rect.colliderect(player.rect):
                push_explosion(explosions, float(player.rect.centerx), float(player.rect.centery), now, big=True)
                player_alive = False
                lives -= 1
                player.gun_mode = GunMode.SINGLE
                player.wingmen = 0
                player.power_mul = 1.0
                bullets.clear()
                drones.clear()
                if lives <= 0:
                    phase = Phase.GAME_OVER
                else:
                    player.rect.bottom = HEIGHT - _vu(8)

            if not boss.alive:
                settlement_level_score = score - score_at_level_start
                settlement_all_cleared = current_level >= MAX_LEVEL
                settlement_until = now + 2000
                phase = Phase.SETTLEMENT

            bg_t += 1.0 / FPS

        elif phase == Phase.SETTLEMENT:
            keys = pygame.key.get_pressed()
            if player_alive:
                dx = dy = 0
                if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                    dx -= 1
                if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                    dx += 1
                if keys[pygame.K_UP] or keys[pygame.K_w]:
                    dy -= 1
                if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                    dy += 1
                if dx != 0 and dy != 0:
                    dx *= 0.707
                    dy *= 0.707
                player.rect.x += int(dx * PLAYER_SPEED)
                player.rect.y += int(dy * PLAYER_SPEED)
                player.rect.clamp_ip(canvas.get_rect())
            for p in pickups:
                p.update()
            pickups = [
                p
                for p in pickups
                if p.alive and (p.kind == PickupKind.WINGMAN or p.rect.top < HEIGHT + _vu(40))
            ]
            for p in pickups:
                if player_alive and p.alive and p.rect.colliderect(player.rect.inflate(_vu(4), _vu(4))):
                    apply_pickup(player, p.kind)
                    p.alive = False
            pickups = [p for p in pickups if p.alive]
            for bb in boss_bullets:
                bb.update()
            boss_bullets = [bb for bb in boss_bullets if bb.rect.top < HEIGHT + 20]
            if now >= settlement_until:
                gm, wm = player.gun_mode, player.wingmen
                prev_support = player.support_left
                keep_wingman_pickups = [p for p in pickups if p.alive and p.kind == PickupKind.WINGMAN]
                if settlement_all_cleared:
                    current_level = 1
                    score = 0
                    score_at_level_start = 0
                    lives = 3
                    total_enemies_shot = 0
                    gm, wm = GunMode.SINGLE, 0
                    next_support = 12
                    keep_wingman_pickups = []
                else:
                    current_level += 1
                    next_support = prev_support + SUPPORT_BONUS_PER_NEW_LEVEL
                phase = Phase.WAVE
                wave_kills = 0
                score_at_level_start = score
                (
                    player,
                    bullets,
                    drones,
                    enemies,
                    boss_bullets,
                    pickups,
                    boss,
                    _,
                    last_fire,
                    last_support,
                    last_spawn,
                ) = reset_level_state(assets, current_level, gm, wm)
                pickups.extend(keep_wingman_pickups)
                player.support_left = next_support
                player_alive = True
                last_boss_shot = now
            bg_t += 1.0 / FPS
        elif phase == Phase.GAME_OVER:
            bg_t += 1.0 / FPS

        explosions = [fx for fx in explosions if fx.alive(now)]

        draw_background(canvas, assets, bg_t)

        if phase in (Phase.WAVE, Phase.BOSS, Phase.SETTLEMENT):
            for e in enemies:
                if e.alive:
                    draw_enemy(canvas, e)
            for p in pickups:
                if p.alive:
                    canvas.blit(p.surf, p.rect)
            if boss and boss.alive:
                canvas.blit(boss.surf, boss.rect)
                draw_boss_hp(canvas, boss, font)
            for b in bullets:
                canvas.blit(b.surf, b.rect)
            for bb in boss_bullets:
                canvas.blit(bb.surf, bb.rect)
            for d in drones:
                if d.alive:
                    rot = pygame.transform.rotate(d.base, d.angle)
                    r = rot.get_rect(center=(int(d.x), int(d.y)))
                    canvas.blit(rot, r)
            for fx in explosions:
                fx.draw(canvas, now)
            if player_alive:
                player.draw(canvas)
            if now < lightning_until:
                draw_lightning_flash(canvas)
            if (not player_alive) and lives > 0:
                t = font.render("战机已击毁，按 R 复活", True, (255, 180, 120))
                canvas.blit(t, t.get_rect(center=(WIDTH // 2, HEIGHT - _vu(70))))

        gun_txt = gun_mode_label(player.gun_mode)
        wing_txt = f"僚机×{player.wingmen}" if player.wingmen else "无僚机"
        if phase == Phase.WAVE:
            act_lab = level_enemy_label(archetypes_for_level(assets.enemy_archetypes, current_level))
            hud = (
                f"第 {current_level} 关  |  敌:{act_lab}  |  {min(wave_kills, need_kills)}/{need_kills}  |  "
                f"弹:{gun_txt}  |  {wing_txt}  |  分{score}  命数:{lives}  援×{player.support_left}"
            )
        elif phase == Phase.BOSS:
            hud = (
                f"第 {current_level} 关·BOSS  |  子弹:{gun_txt}  |  {wing_txt}  |  "
                f"分 {score}  |  命数:{lives}  |  援×{player.support_left}"
            )
        elif phase == Phase.SETTLEMENT:
            hud = f"第 {current_level} 关 结算"
        else:
            hud = f"分数 {score}"

        canvas.blit(font.render(hud, True, (240, 240, 240)), (_vu(10), _vu(8)))

        if phase == Phase.SETTLEMENT:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((12, 14, 28, 220))
            canvas.blit(overlay, (0, 0))
            title = "通关！" if settlement_all_cleared else f"第 {current_level} 关完成"
            t_surf = font_big.render(title, True, (130, 255, 190))
            canvas.blit(t_surf, t_surf.get_rect(center=(WIDTH // 2, _vu(120))))

            lines = [
                f"本关得分：{settlement_level_score}",
                f"累计总分：{score}",
                f"剩余生命：{lives}",
                f"剩余支援：{player.support_left}",
                f"子弹模式：{gun_mode_label(player.gun_mode)}",
                f"僚机：×{player.wingmen}",
                f"累计击毁敌机：{total_enemies_shot}",
            ]
            y = _vu(190)
            for line in lines:
                s = font_mid.render(line, True, (230, 232, 245))
                canvas.blit(s, s.get_rect(center=(WIDTH // 2, y)))
                y += _vu(32)

            if settlement_all_cleared:
                hint = f"{max(0.0, (settlement_until - now) / 1000.0):.1f}s 后从第 1 关再玩一次    ESC：退出"
            else:
                hint = f"{max(0.0, (settlement_until - now) / 1000.0):.1f}s 后进入下一关    ESC：退出"
            h = font.render(hint, True, (180, 190, 215))
            canvas.blit(h, h.get_rect(center=(WIDTH // 2, HEIGHT - _vu(80))))

        if phase == Phase.GAME_OVER:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 170))
            canvas.blit(overlay, (0, 0))
            msg = font_big.render("游戏结束", True, (255, 90, 90))
            canvas.blit(msg, msg.get_rect(center=(WIDTH // 2, HEIGHT // 2 - _vu(40))))
            info = font_mid.render(f"到达第 {current_level} 关 · 总分 {score}", True, (220, 220, 230))
            canvas.blit(info, info.get_rect(center=(WIDTH // 2, HEIGHT // 2 + _vu(10))))
            hint = font.render("R 重新开始    ESC 退出", True, (200, 200, 210))
            canvas.blit(hint, hint.get_rect(center=(WIDTH // 2, HEIGHT // 2 + _vu(55))))

        if phase not in (Phase.SETTLEMENT, Phase.GAME_OVER):
            tip = (
                "移动:WASD  射击:空格(僚机同发)  支援:M 闪电清屏/Boss扣血⅓  |  金框=特殊机掉强化  拾取:1单2双3扇 僚=僚机"
            )
            canvas.blit(font.render(tip, True, (190, 200, 220)), (_vu(10), HEIGHT - _vu(28)))

        pygame.transform.smoothscale(canvas, (win_w, win_h), screen)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    sys.exit(0)


def run_main_menu(port: int = LAN_PORT) -> None:
    """启动后先选模式：局域网配对或单机；无需命令行参数。"""
    pygame.init()
    win_w, win_h = compute_window_size(WIDTH, HEIGHT)
    screen = pygame.display.set_mode((win_w, win_h))
    canvas = pygame.Surface((WIDTH, HEIGHT))
    pygame.display.set_caption("雷电 · Leidian")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("microsoftyahei", max(16, _vu(24)))
    font_small = pygame.font.SysFont("microsoftyahei", max(12, _vu(18)))
    options: list[tuple[str, str]] = [
        ("局域网双人（自动发现配对）", "lan"),
        ("单机游戏", "solo"),
    ]
    sel = 0
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_UP, pygame.K_w):
                    sel = (sel - 1) % len(options)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    sel = (sel + 1) % len(options)
                elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    choice = options[sel][1]
                    pygame.quit()
                    if choice == "lan":
                        auto_pair_and_run(port)
                    else:
                        main()
                    return
                elif event.key == pygame.K_ESCAPE:
                    running = False
        canvas.fill((14, 18, 36))
        title = font.render("雷电 Leidian", True, (220, 235, 255))
        canvas.blit(title, title.get_rect(center=(WIDTH // 2, _vu(96))))
        sub = font_small.render("选择游戏模式", True, (170, 190, 220))
        canvas.blit(sub, sub.get_rect(center=(WIDTH // 2, _vu(142))))
        tip = font_small.render("↑↓ 选择   回车 确认   ESC 退出", True, (150, 165, 195))
        canvas.blit(tip, tip.get_rect(center=(WIDTH // 2, _vu(178))))
        y0 = _vu(230)
        row_h = _vu(50)
        for i, (label, _) in enumerate(options):
            y = y0 + i * row_h
            rect = pygame.Rect(_vu(56), y, WIDTH - _vu(112), _vu(42))
            if i == sel:
                pygame.draw.rect(canvas, (48, 78, 128), rect, border_radius=10)
                pygame.draw.rect(canvas, (120, 180, 255), rect, width=max(1, _vu(2)), border_radius=10)
            color = (255, 248, 210) if i == sel else (200, 210, 230)
            t = font.render(label, True, color)
            canvas.blit(t, t.get_rect(center=rect.center))
        pygame.transform.smoothscale(canvas, (win_w, win_h), screen)
        pygame.display.flip()
        clock.tick(30)
    pygame.quit()
    sys.exit(0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="雷电单机/局域网双人模式")
    parser.add_argument(
        "--lan",
        choices=("menu", "off", "host", "client", "auto"),
        default="menu",
        help="menu=启动菜单(默认) / off=直接单机 / host|client|auto=跳过菜单进联机",
    )
    parser.add_argument("--host-ip", default="127.0.0.1", help="客户端连接的主机IP")
    parser.add_argument("--bind-ip", default="0.0.0.0", help="主机绑定IP")
    parser.add_argument("--port", type=int, default=LAN_PORT, help="联机端口")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.lan == "host":
        run_lan_host(args.bind_ip, args.port)
    elif args.lan == "client":
        run_lan_client(args.host_ip, args.port)
    elif args.lan == "auto":
        auto_pair_and_run(args.port)
    elif args.lan == "off":
        main()
    else:
        run_main_menu(args.port)

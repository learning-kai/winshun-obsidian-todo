# -*- coding: utf-8 -*-
"""Pure-stdlib analytical-SDF anti-aliased PNG renderer for silky smooth checkboxes & dots.

Produces Retina/HiDPI-grade anti-aliased Tk PhotoImage sprites in ~1 ms with zero third-party deps.
"""
from __future__ import annotations

import math
import struct
import zlib
from typing import Dict, Tuple


def _hex_rgb(color: str) -> Tuple[float, float, float]:
    s = str(color or "#000000").strip().lstrip("#")
    if len(s) != 6:
        return (0.0, 0.0, 0.0)
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)


def _encode_rgba_png(width: int, height: int, rgba: bytearray) -> bytes:
    stride = width * 4
    rows = [b"\x00" + bytes(rgba[y * stride : (y + 1) * stride]) for y in range(height)]
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def _rrect_sdf(px: float, py: float, cx: float, cy: float, hx: float, hy: float, r: float) -> float:
    qx = abs(px - cx) - (hx - r)
    qy = abs(py - cy) - (hy - r)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def _seg_dist(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 <= 1e-9:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _over(dst: Tuple[float, float, float, float], rgb: Tuple[float, float, float], alpha: float) -> Tuple[float, float, float, float]:
    if alpha <= 0.0:
        return dst
    if alpha >= 1.0:
        return (rgb[0], rgb[1], rgb[2], 1.0)
    dr, dg, db, da = dst
    out_a = alpha + da * (1.0 - alpha)
    if out_a <= 1e-6:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        (rgb[0] * alpha + dr * da * (1.0 - alpha)) / out_a,
        (rgb[1] * alpha + dg * da * (1.0 - alpha)) / out_a,
        (rgb[2] * alpha + db * da * (1.0 - alpha)) / out_a,
        out_a,
    )


def render_bullet_checkbox_png(
    *,
    width: int,
    height: int,
    dot_x: float,
    dot_r: float,
    box_cx: float,
    box_cy: float,
    box_size: float,
    stroke_w: float,
    checked: bool,
    dot_color: str,
    border_color: str,
    fill_color: str,
    check_color: str = "#FFFFFF",
) -> bytes:
    """Render a crisp anti-aliased bullet dot + generously rounded square checkbox as RGBA PNG."""
    w, h = max(4, int(width)), max(4, int(height))
    rgba = bytearray(w * h * 4)

    dot_rgb = _hex_rgb(dot_color)
    border_rgb = _hex_rgb(border_color)
    fill_rgb = _hex_rgb(fill_color)
    chk_rgb = _hex_rgb(check_color)

    half = box_size * 0.5
    # Generous HuaJian-style soft corner radius (~30% of box side)
    rad = max(3.0, box_size * 0.30)
    half_sw = stroke_w * 0.5

    # Checkmark control points inside box
    bx1 = box_cx - half
    by1 = box_cy - half
    c1x, c1y = bx1 + box_size * 0.25, by1 + box_size * 0.52
    c2x, c2y = bx1 + box_size * 0.43, by1 + box_size * 0.70
    c3x, c3y = bx1 + box_size * 0.76, by1 + box_size * 0.31
    chk_r = max(1.0, stroke_w * 0.68)

    for y in range(h):
        py = y + 0.5
        row_off = y * w * 4
        for x in range(w):
            px = x + 0.5
            col = (0.0, 0.0, 0.0, 0.0)

            # 1. Silky anti-aliased bullet dot (•)
            d_dot = math.hypot(px - dot_x, py - box_cy) - dot_r
            if d_dot < 1.0:
                cov = max(0.0, min(1.0, 0.5 - d_dot))
                col = _over(col, dot_rgb, cov)

            # 2. Silky rounded square checkbox
            d_box = _rrect_sdf(px, py, box_cx, box_cy, half, half, rad)
            if checked:
                if d_box < 1.0:
                    cov = max(0.0, min(1.0, 0.5 - d_box))
                    col = _over(col, fill_rgb, cov)
                d_chk = min(
                    _seg_dist(px, py, c1x, c1y, c2x, c2y),
                    _seg_dist(px, py, c2x, c2y, c3x, c3y),
                ) - chk_r
                if d_chk < 1.0:
                    cov = max(0.0, min(1.0, 0.5 - d_chk))
                    col = _over(col, chk_rgb, cov)
            else:
                # Soft interior fill (subtle paper tint) + anti-aliased outer rounded border
                d_inner = d_box + stroke_w
                if d_inner < 1.0:
                    cov_in = max(0.0, min(1.0, 0.5 - d_inner))
                    col = _over(col, fill_rgb, cov_in)
                d_ring = abs(d_box + half_sw) - half_sw
                if d_ring < 1.0:
                    cov_ring = max(0.0, min(1.0, 0.5 - d_ring))
                    col = _over(col, border_rgb, cov_ring)

            idx = row_off + x * 4
            rgba[idx] = max(0, min(255, int(round(col[0] * 255.0))))
            rgba[idx + 1] = max(0, min(255, int(round(col[1] * 255.0))))
            rgba[idx + 2] = max(0, min(255, int(round(col[2] * 255.0))))
            rgba[idx + 3] = max(0, min(255, int(round(col[3] * 255.0))))

    return _encode_rgba_png(w, h, rgba)


def render_pill_progress_png(
    *,
    width: int,
    height: int,
    progress: float,
    track_color: str,
    fill_color: str,
) -> bytes:
    """Render a silky anti-aliased rounded pill progress bar as RGBA PNG."""
    w, h = max(8, int(width)), max(4, int(height))
    rgba = bytearray(w * h * 4)
    track_rgb = _hex_rgb(track_color)
    fill_rgb = _hex_rgb(fill_color)
    prog = max(0.0, min(1.0, float(progress)))

    cy = h * 0.5
    r = max(1.5, h * 0.48)
    hx = w * 0.5 - 0.5
    hy = r

    fill_w = w * prog
    has_fill = prog > 0.001 and fill_w >= 1.5
    fill_hx = max(r, fill_w * 0.5)
    fill_cx = fill_hx

    for y in range(h):
        py = y + 0.5
        row_off = y * w * 4
        for x in range(w):
            px = x + 0.5
            d_tr = _rrect_sdf(px, py, w * 0.5, cy, hx, hy, r)
            if d_tr >= 1.0:
                continue
            cov_tr = max(0.0, min(1.0, 0.5 - d_tr))
            col = _over((0.0, 0.0, 0.0, 0.0), track_rgb, cov_tr)
            if has_fill and px <= fill_w + 1.5:
                d_fl = _rrect_sdf(px, py, fill_cx, cy, fill_hx, hy, r)
                if d_fl < 1.0:
                    cov_fl = min(cov_tr, max(0.0, min(1.0, 0.5 - d_fl)))
                    col = _over(col, fill_rgb, cov_fl)
            idx = row_off + x * 4
            rgba[idx] = max(0, min(255, int(round(col[0] * 255.0))))
            rgba[idx + 1] = max(0, min(255, int(round(col[1] * 255.0))))
            rgba[idx + 2] = max(0, min(255, int(round(col[2] * 255.0))))
            rgba[idx + 3] = max(0, min(255, int(round(col[3] * 255.0))))

    return _encode_rgba_png(w, h, rgba)


def render_circular_dial_png(
    *,
    size: int,
    progress: float,
    bg_color: str,
    border_color: str,
    track_color: str,
    arc_color: str,
    stroke_w: float,
    rim_w: float,
    tick_color: str = "",
    dark: bool = False,
) -> bytes:
    """Boutique watch-face SDF renderer: warm bezel shading, 12 delicate ticks, thick rounded arc & pearl tip."""
    sz = max(48, int(size))
    rgba = bytearray(sz * sz * 4)

    bg_r, bg_g, bg_b = (int(round(c * 255)) for c in _hex_rgb(bg_color))
    bd_r, bd_g, bd_b = (int(round(c * 255)) for c in _hex_rgb(border_color))
    tr_r, tr_g, tr_b = (int(round(c * 255)) for c in _hex_rgb(track_color))
    ac_r, ac_g, ac_b = (int(round(c * 255)) for c in _hex_rgb(arc_color))
    if tick_color:
        tk_r, tk_g, tk_b = (int(round(c * 255)) for c in _hex_rgb(tick_color))
    else:
        tk_r, tk_g, tk_b = bd_r, bd_g, bd_b

    # Slightly deeper warm bezel shade outside the ring for tactile ceramic depth
    shade = -10 if not dark else -7
    bez_r = max(0, min(255, bg_r + shade))
    bez_g = max(0, min(255, bg_g + shade))
    bez_b = max(0, min(255, bg_b + shade))

    bg_px = bytes((bg_r, bg_g, bg_b, 255))
    cx = sz * 0.5
    cy = sz * 0.5
    disc_r = sz * 0.5 - 1.0
    half_sw = stroke_w * 0.5
    ring_r = disc_r - rim_w - half_sw - max(4.0, sz * 0.028)

    # 12 delicate subtle hour/5-min ticks tucked just inside the progress track
    tick_r_out = ring_r - half_sw - max(3.5, sz * 0.018)
    tick_len = max(3.5, sz * 0.022)
    tick_r_in = tick_r_out - tick_len
    tick_half_w = max(0.75, sz * 0.0032)

    inner_solid_r = max(0.0, tick_r_in - 2.0)
    disc_r2 = disc_r * disc_r
    inner_solid_r2 = inner_solid_r * inner_solid_r

    prog = max(0.0, min(1.0, float(progress)))
    sweep = prog * 2.0 * math.pi
    full_circle = prog >= 0.9995
    has_arc = prog > 0.002

    # Rounded arc caps + luminous pearl dot at moving tip
    cap1_x, cap1_y = cx, cy - ring_r
    end_ang = -math.pi * 0.5 + sweep
    cap2_x = cx + ring_r * math.cos(end_ang)
    cap2_y = cy + ring_r * math.sin(end_ang)
    pearl_r = max(1.8, half_sw * 0.48)
    show_pearl = has_arc and not full_circle

    # Precompute 12 tick unit vectors (every 30 deg)
    two_pi = 2.0 * math.pi
    half_pi = math.pi * 0.5
    tick_step = two_pi / 12.0

    for y in range(sz):
        py = y + 0.5
        dy = py - cy
        dy2 = dy * dy
        if dy2 > disc_r2:
            continue
        dx_outer = math.sqrt(disc_r2 - dy2)
        x_start = max(0, int(math.floor(cx - dx_outer)))
        x_end = min(sz, int(math.ceil(cx + dx_outer)) + 1)

        row_off = y * sz * 4
        if dy2 < inner_solid_r2:
            dx_in = math.sqrt(inner_solid_r2 - dy2)
            in_left = max(x_start, int(math.ceil(cx - dx_in)))
            in_right = min(x_end, int(math.floor(cx + dx_in)))
            if in_right > in_left:
                rgba[row_off + in_left * 4 : row_off + in_right * 4] = bg_px * (in_right - in_left)
            spans = ((x_start, in_left), (in_right, x_end))
        else:
            spans = ((x_start, x_end),)

        for s_x, e_x in spans:
            for x in range(s_x, e_x):
                px = x + 0.5
                dx = px - cx
                dist = math.hypot(dx, dy)
                if dist > disc_r:
                    continue

                # Gentle warm bezel shading outside the ring; clean uniform paper inside
                if dist > ring_r:
                    t = min(1.0, (dist - ring_r) / max(1.0, disc_r - ring_r))
                    r_c = int(round(bg_r + (bez_r - bg_r) * t))
                    g_c = int(round(bg_g + (bez_g - bg_g) * t))
                    b_c = int(round(bg_b + (bez_b - bg_b) * t))
                else:
                    r_c, g_c, b_c = bg_r, bg_g, bg_b

                # 1. Outer crisp rim
                d_rim = disc_r - dist
                if d_rim < rim_w + 1.0:
                    cov_rim = max(0.0, min(1.0, rim_w + 0.5 - d_rim))
                    inv = 1.0 - cov_rim
                    r_c = int(round(bd_r * cov_rim + r_c * inv))
                    g_c = int(round(bd_g * cov_rim + g_c * inv))
                    b_c = int(round(bd_b * cov_rim + b_c * inv))

                # 2. 12 delicate whisper ticks just inside the ring
                if tick_r_in - 1.2 <= dist <= tick_r_out + 1.2:
                    ang0 = math.atan2(dy, dx) + half_pi
                    if ang0 < 0.0:
                        ang0 += two_pi
                    nearest_idx = int(round(ang0 / tick_step)) % 12
                    delta_ang = abs(ang0 - nearest_idx * tick_step)
                    if delta_ang > math.pi:
                        delta_ang = two_pi - delta_ang
                    tang_dist = delta_ang * dist
                    # Cardinal ticks (12/3/6/9) slightly firmer than minor ticks
                    is_cardinal = (nearest_idx % 3) == 0
                    t_hw = tick_half_w * (1.25 if is_cardinal else 0.9)
                    t_in = tick_r_in if is_cardinal else (tick_r_in + tick_len * 0.25)
                    d_rad = max(t_in - dist, dist - tick_r_out)
                    d_tick = max(tang_dist - t_hw, d_rad)
                    if d_tick < 1.0:
                        cov_tk = max(0.0, min(1.0, 0.5 - d_tick)) * (0.72 if is_cardinal else 0.42)
                        inv = 1.0 - cov_tk
                        r_c = int(round(tk_r * cov_tk + r_c * inv))
                        g_c = int(round(tk_g * cov_tk + g_c * inv))
                        b_c = int(round(tk_b * cov_tk + b_c * inv))

                # 3. Circular recessed track ring
                d_ring = abs(dist - ring_r) - half_sw
                if d_ring < 1.0:
                    cov_tr = max(0.0, min(1.0, 0.5 - d_ring))
                    inv = 1.0 - cov_tr
                    r_c = int(round(tr_r * cov_tr + r_c * inv))
                    g_c = int(round(tr_g * cov_tr + g_c * inv))
                    b_c = int(round(tr_b * cov_tr + b_c * inv))

                # 4. Active progress arc with rounded circular end-caps + subtle pearl tip
                if has_arc and abs(dist - ring_r) <= half_sw + 1.5:
                    if full_circle:
                        d_arc = d_ring
                    else:
                        ang = math.atan2(dy, dx) + half_pi
                        if ang < 0.0:
                            ang += two_pi
                        if ang <= sweep:
                            d_arc = d_ring
                        else:
                            d_arc = min(
                                math.hypot(px - cap1_x, py - cap1_y) - half_sw,
                                math.hypot(px - cap2_x, py - cap2_y) - half_sw,
                            )
                    if d_arc < 1.0:
                        cov_ac = max(0.0, min(1.0, 0.5 - d_arc))
                        inv = 1.0 - cov_ac
                        r_c = int(round(ac_r * cov_ac + r_c * inv))
                        g_c = int(round(ac_g * cov_ac + g_c * inv))
                        b_c = int(round(ac_b * cov_ac + b_c * inv))

                    if show_pearl:
                        d_prl = math.hypot(px - cap2_x, py - cap2_y) - pearl_r
                        if d_prl < 1.0:
                            cov_p = max(0.0, min(1.0, 0.5 - d_prl)) * 0.90
                            inv = 1.0 - cov_p
                            r_c = int(round(255 * cov_p + r_c * inv))
                            g_c = int(round(255 * cov_p + g_c * inv))
                            b_c = int(round(255 * cov_p + b_c * inv))

                idx = row_off + x * 4
                rgba[idx] = r_c
                rgba[idx + 1] = g_c
                rgba[idx + 2] = b_c
                rgba[idx + 3] = 255

    return _encode_rgba_png(sz, sz, rgba)

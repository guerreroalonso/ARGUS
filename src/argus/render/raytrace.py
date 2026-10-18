"""Trazado de rayos exacto en la CPU: referencia de la vista 3D.

Un rayo recorre la geometría como lo haría una partícula con la subrutina
STEP de PENGEOM (versión simplificada, la misma que usa el *shader* de la
vista 3D):

1. se localiza el origen con LOCATE (``IBODY``);
2. se calculan los cortes del rayo con las superficies del cuerpo actual
   (``KSURF`` con ``KFLAG <= 4``: las suyas, las de sus cuerpos limitantes y,
   si es un módulo, las de sus hijas), desde el punto actual y con las mismas
   reglas que STEPSI (superficie difusa: si el punto está sobre ella solo
   cuenta el corte en el sentido del movimiento);
3. se avanza hasta el corte más cercano y se vuelve a localizar el punto con
   LOCATE, que en la superficie decide por la dirección del rayo.

El resultado es la lista de cuerpos que atraviesa el rayo y la superficie
por la que entra en cada uno (para el sombreado). Se verifica contra STEP con
la sonda PENGEOM (pruebas *oracle*).

Con Numba el núcleo está compilado y se reparte entre los núcleos de la CPU;
sin Numba las mismas funciones se ejecutan en Python (solo para pocos rayos:
selección con el ratón, pruebas).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.parallel import PARALLEL_LOCK
from ..pengeom import locate_fast

try:  # pragma: no cover - depende del entorno
    from numba import njit, prange

    HAVE_NUMBA = True
except Exception:  # pragma: no cover
    HAVE_NUMBA = False

    def njit(*args, **kwargs):  # type: ignore[misc]
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        return lambda f: f

    prange = range  # type: ignore[assignment]

FUZZL = 1.0e-12
INF = 1.0e300


# ---------------------------------------------------------------------------
# Núcleo (mismas expresiones que FSURF, LOCATE y STEPSI)
# ---------------------------------------------------------------------------

@njit(cache=True, nogil=True)
def _abc(coef, kplane, ks, x, y, z, u, v, w):
    """Coeficientes A, B, C de F(r + s·d) = A s² + B s + C (orden de FSURF)."""
    axx = coef[ks, 0]
    axy = coef[ks, 1]
    axz = coef[ks, 2]
    ayy = coef[ks, 3]
    ayz = coef[ks, 4]
    azz = coef[ks, 5]
    ax = coef[ks, 6]
    ay = coef[ks, 7]
    az = coef[ks, 8]
    a0 = coef[ks, 9]
    if kplane[ks] == 0:
        a = u * (axx * u + axy * v + axz * w) + v * (ayy * v + ayz * w) + w * azz * w
        xxx = axx * x + axy * y + axz * z + ax
        yyy = ayy * y + ayz * z + ay
        zzz = azz * z + az
        b = u * (axx * x + xxx) + v * (axy * x + ayy * y + yyy) + w * (axz * x + ayz * y + azz * z + zzz)
        c = x * xxx + y * yyy + z * zzz + a0
    else:
        a = 0.0
        b = u * ax + v * ay + w * az
        c = x * ax + y * ay + z * az + a0
    return a, b, c


@njit(cache=True, nogil=True)
def _side1(coef, kplane, ks, x, y, z, u, v, w):
    a, b, c = _abc(coef, kplane, ks, x, y, z, u, v, w)
    absa = abs(a)
    if absa > 1.0e-36:
        fuzz = FUZZL * abs(b * b - 4.0 * a * c) / absa
    else:
        fuzz = FUZZL * abs(b)
    if c < -fuzz:
        return 1
    if c > fuzz:
        return 2
    if b < 0.0:
        return 1
    return 2


@njit(cache=True, nogil=True)
def _locate1(x, y, z, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok, ndght,
             ks_force=0, side_force=0):
    """LOCATE de un punto (sin caché de lados: el resultado es el mismo).

    ``ks_force``/``side_force``: lado impuesto para la superficie que se acaba
    de cruzar. STEP no la vuelve a evaluar, cambia su lado (KSP) al cruzarla;
    así el resultado no depende del redondeo en el punto de cruce (lejos del
    origen, F(r) pierde muchas cifras).
    """
    kb0 = nbodys
    while True:
        descend = -1
        for jd in range(dght_ptr[kb0], dght_ptr[kb0 + 1]):
            if dok[jd] == 0:
                continue
            kb = dght[jd]
            ok = True
            for c in range(cons_ptr[kb], cons_ptr[kb + 1]):
                ks = cons_ks[c]
                if ks == ks_force:
                    sd = side_force
                else:
                    sd = _side1(coef, kplane, ks, x, y, z, u, v, w)
                if sd != cons_kf[c]:
                    ok = False
                    break
            if not ok:
                continue
            if kb == kb0:
                return kb
            if ndght[kb] > 1:
                descend = kb
                break
            return kb
        if descend < 0:
            return nbodys + 1
        kb0 = descend


MEM = 16  # superficies cruzadas cuyo corte siguiente se recuerda


@njit(cache=True, nogil=True)
def _next_crossing(kb, t0, x, y, z, u, v, w, coef, kplane, eval_ptr, eval_ks, mem_ks, mem_t, mem_sd):
    """Corte más cercano con las superficies KSURF (KFLAG<=4) de ``kb`` (reglas de STEPSI).

    ``(x, y, z)`` es el punto del rayo a la distancia ``t0``. Devuelve
    (distancia absoluta del corte, superficie, lado tras el cruce: 1 si F pasa
    a ser negativa, 2 si pasa a ser positiva, distancia absoluta del otro
    corte de esa superficie más adelante o INF, lado tras ese otro corte).

    Como STEP, los cortes de una superficie ya cruzada no se recalculan desde
    el punto de cruce (allí F(r) es casi 0 y el redondeo inventaría cortes o
    los perdería): se usan los que se calcularon antes (``mem_*``).
    """
    smin = INF
    ksmin = 0
    side = 0
    other = INF
    oside = 0
    for e in range(eval_ptr[kb], eval_ptr[kb + 1]):
        ks = eval_ks[e]
        known = -1
        for m in range(mem_ks.shape[0]):
            if mem_ks[m] == ks:
                known = m
                break
        if known >= 0:
            tk = mem_t[known]
            if t0 < tk < smin:
                smin = tk
                ksmin = ks
                side = mem_sd[known]
                other = INF
                oside = 0
            continue
        a, b, c = _abc(coef, kplane, ks, x, y, z, u, v, w)
        absa = abs(a)
        if absa < 1.0e-36:  # plano: una raíz
            if abs(b) > 0.0:
                if c < -FUZZL or c > FUZZL:
                    t1 = -c / b
                    if 0.0 < t1 and t0 + t1 < smin:
                        smin = t0 + t1
                        ksmin = ks
                        side = 1 if b < 0.0 else 2
                        other = INF
                        oside = 0
            continue
        discr = b * b - 4.0 * a * c
        fuzz = FUZZL * abs(discr) / absa
        ambig = -fuzz <= c <= fuzz
        if discr < 1.0e-36:
            continue
        r2a = 0.5 / a
        delta = math.sqrt(discr) * abs(r2a)
        sh = -b * r2a
        # F' en la raíz menor tiene el signo de -A y en la mayor el de A.
        s_lo = 1 if a > 0.0 else 2
        s_hi = 2 if a > 0.0 else 1
        if not ambig:
            t1 = sh - delta
            t2 = sh + delta
            if 0.0 < t1 and t0 + t1 < smin:
                smin = t0 + t1
                ksmin = ks
                side = s_lo
                other = t0 + t2
                oside = s_hi
            elif 0.0 < t2 and t0 + t2 < smin:
                smin = t0 + t2
                ksmin = ks
                side = s_hi
                other = INF
                oside = 0
        elif b * a < 0.0:  # sobre la superficie: solo cuenta el corte hacia delante
            t2 = max(sh + delta, 0.0)
            if 0.0 < t2 and t0 + t2 < smin:
                smin = t0 + t2
                ksmin = ks
                side = s_hi
                other = INF
                oside = 0
    return smin, ksmin, side, other, oside


@njit(cache=True, nogil=True)
def _remember(ks, t_other, sd_other, mem_ks, mem_t, mem_sd, slot):
    """Anota el siguiente corte de la superficie recién cruzada (anillo de MEM entradas)."""
    for m in range(mem_ks.shape[0]):
        if mem_ks[m] == ks:
            mem_t[m] = t_other
            mem_sd[m] = sd_other
            return slot
    mem_ks[slot] = ks
    mem_t[slot] = t_other
    mem_sd[slot] = sd_other
    return (slot + 1) % mem_ks.shape[0]


@njit(cache=True, nogil=True)
def _trace(x0, y0, z0, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok, ndght,
           eval_ptr, eval_ks, out_t, out_kb, out_ks, max_seg, all_crossings):
    """Cuerpos que atraviesa el rayo: (distancia de entrada, cuerpo, superficie de entrada).

    El primer tramo es el del origen (distancia 0, superficie 0). Termina al
    salir de la envolvente o al llenar ``max_seg`` tramos; devuelve el número
    de tramos. Con ``all_crossings`` se anota cada superficie cruzada aunque
    el cuerpo no cambie (así se detiene STEP de PENGEOM).
    """
    kb = _locate1(x0, y0, z0, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok,
                  ndght)
    out_t[0] = 0.0
    out_kb[0] = kb
    out_ks[0] = 0
    n = 1
    t = 0.0
    inside = kb <= nbodys
    guard = 0
    mem_ks = np.zeros(MEM, dtype=np.int64)
    mem_t = np.zeros(MEM)
    mem_sd = np.zeros(MEM, dtype=np.int64)
    slot = 0
    while n < max_seg and guard < 100000:
        guard += 1
        kc = kb if kb <= nbodys else nbodys  # fuera: superficies de la envolvente (STEPSI(NBODYS))
        x = x0 + t * u
        y = y0 + t * v
        z = z0 + t * w
        tn, ks, sd, t_other, sd_other = _next_crossing(kc, t, x, y, z, u, v, w, coef, kplane, eval_ptr, eval_ks,
                                                       mem_ks, mem_t, mem_sd)
        if ks == 0:
            break
        slot = _remember(ks, t_other, sd_other, mem_ks, mem_t, mem_sd, slot)
        t = tn
        x = x0 + t * u
        y = y0 + t * v
        z = z0 + t * w
        kn = _locate1(x, y, z, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok,
                      ndght, ks, sd)
        if kn == kb and not (all_crossings and kb <= nbodys):
            continue
        kb = kn
        out_t[n] = t
        out_kb[n] = kb
        out_ks[n] = ks
        n += 1
        if kb > nbodys:
            if inside:
                break  # la partícula escapa
        else:
            inside = True
    return n


@njit(cache=True, nogil=True)
def _clip_interval(x0, y0, z0, u, v, w, clip):
    """Tramo (t0, t1, plano) del rayo oculto por los cortes; t1 < t0 si no hay.

    ``clip``: (m, 4) con (nx, ny, nz, d); se oculta la intersección de los
    semiespacios n·r > d. ``plano``: índice del plano por el que se sale de
    la zona oculta (el de la cara del corte).
    """
    c_lo = 0.0
    c_hi = -1.0
    plane = -1
    m = clip.shape[0]
    if m == 0:
        return c_lo, c_hi, plane
    c_hi = INF
    for i in range(m):
        nd = clip[i, 0] * u + clip[i, 1] * v + clip[i, 2] * w
        f0 = clip[i, 0] * x0 + clip[i, 1] * y0 + clip[i, 2] * z0 - clip[i, 3]
        a0 = 0.0
        a1 = INF
        if abs(nd) < 1.0e-300:
            if f0 <= 0.0:
                a0, a1 = 1.0, 0.0
        else:
            tp = -f0 / nd
            if nd > 0.0:  # entra en el semiespacio en tp
                a0 = max(tp, 0.0)
            else:  # sale de él en tp
                a1 = tp
        c_lo = max(c_lo, a0)
        if a1 < c_hi:
            c_hi = a1
            plane = i
    return c_lo, c_hi, plane



# ---------------------------------------------------------------------------
# Vóxeles de la caja CT (recorrido DDA en el sistema del CT)
# ---------------------------------------------------------------------------
# ct_i = (cuerpo CT o 0, NX, NY, NZ); ct_f = (R fila a fila (9), D (3), DX, DY, DZ); ct_mat: material de cada
# vóxel (IA = IX + NX·(IY + NY·IZ), desde 0). r_lab = R·r_CT + D, como SHPRD.

@njit(cache=True, nogil=True)
def _ct_local(x, y, z, ct_f):
    ox = x - ct_f[9]
    oy = y - ct_f[10]
    oz = z - ct_f[11]
    return (ct_f[0] * ox + ct_f[3] * oy + ct_f[6] * oz,
            ct_f[1] * ox + ct_f[4] * oy + ct_f[7] * oz,
            ct_f[2] * ox + ct_f[5] * oy + ct_f[8] * oz)


@njit(cache=True, nogil=True)
def _ct_dir(u, v, w, ct_f):
    return (ct_f[0] * u + ct_f[3] * v + ct_f[6] * w,
            ct_f[1] * u + ct_f[4] * v + ct_f[7] * w,
            ct_f[2] * u + ct_f[5] * v + ct_f[8] * w)


@njit(cache=True, nogil=True)
def _ct_start(x0, y0, z0, u, v, w, t, ct_i, ct_f):
    """Vóxel en que está el rayo en t y distancias a sus caras: (ix, iy, iz, tmx, tmy, tmz, tdx, tdy, tdz, dl)."""
    lx, ly, lz = _ct_local(x0, y0, z0, ct_f)
    dx_, dy_, dz_ = _ct_dir(u, v, w, ct_f)
    vx, vy, vz = ct_f[12], ct_f[13], ct_f[14]
    px = lx + t * dx_
    py = ly + t * dy_
    pz = lz + t * dz_
    ix = min(max(int(math.floor(px / vx)), 0), ct_i[1] - 1)
    iy = min(max(int(math.floor(py / vy)), 0), ct_i[2] - 1)
    iz = min(max(int(math.floor(pz / vz)), 0), ct_i[3] - 1)
    if dx_ > 0.0:
        tmx = ((ix + 1) * vx - lx) / dx_
        tdx = vx / dx_
    elif dx_ < 0.0:
        tmx = (ix * vx - lx) / dx_
        tdx = -vx / dx_
    else:
        tmx = INF
        tdx = INF
    if dy_ > 0.0:
        tmy = ((iy + 1) * vy - ly) / dy_
        tdy = vy / dy_
    elif dy_ < 0.0:
        tmy = (iy * vy - ly) / dy_
        tdy = -vy / dy_
    else:
        tmy = INF
        tdy = INF
    if dz_ > 0.0:
        tmz = ((iz + 1) * vz - lz) / dz_
        tdz = vz / dz_
    elif dz_ < 0.0:
        tmz = (iz * vz - lz) / dz_
        tdz = -vz / dz_
    else:
        tmz = INF
        tdz = INF
    return ix, iy, iz, tmx, tmy, tmz, tdx, tdy, tdz, dx_, dy_, dz_


@njit(cache=True, nogil=True)
def _ct_first(x0, y0, z0, u, v, w, t0, t1, ct_i, ct_f, ct_mat, mat_vis, clip_on, c_lo, c_hi):
    """Primer punto visible dentro de la caja CT entre t0 y t1: (t, cara); t < 0 si no hay.

    cara: −1 al entrar en el tramo, −2 en la cara del corte, 0/1/2 cara del vóxel perpendicular a x/y/z.
    """
    ix, iy, iz, tmx, tmy, tmz, tdx, tdy, tdz, dx_, dy_, dz_ = _ct_start(x0, y0, z0, u, v, w, t0, ct_i, ct_f)
    nx, ny, nz = ct_i[1], ct_i[2], ct_i[3]
    t = t0
    face = -1
    for _ in range(nx + ny + nz + 3):
        tn = min(tmx, tmy, tmz, t1)
        m = ct_mat[ix + nx * (iy + ny * iz)]
        if m < mat_vis.shape[0] and mat_vis[m] == 1:
            tol = 1e-9 * (1.0 + abs(c_hi))
            if clip_on and abs(t - c_hi) <= tol and c_hi < tn:
                return c_hi, -2  # plano entre vóxeles sobre el del corte: la cara del corte
            if not clip_on or t < c_lo or t >= c_hi:
                return t, face
            if c_hi < tn - tol:  # el corte acaba dentro de este vóxel (si acaba en su cara, se ve el siguiente)
                return c_hi, -2
        if tn >= t1:
            break
        t = tn
        if tmx <= tmy and tmx <= tmz:
            ix += 1 if dx_ > 0.0 else -1
            tmx += tdx
            face = 0
            if ix < 0 or ix >= nx:
                break
        elif tmy <= tmz:
            iy += 1 if dy_ > 0.0 else -1
            tmy += tdy
            face = 1
            if iy < 0 or iy >= ny:
                break
        else:
            iz += 1 if dz_ > 0.0 else -1
            tmz += tdz
            face = 2
            if iz < 0 or iz >= nz:
                break
    return -1.0, -1


@njit(cache=True, nogil=True)
def _ct_color(x0, y0, z0, u, v, w, t0, t1, ks_in, ct_i, ct_f, ct_mat, mat_rgba, selected, clip, clip_on, c_lo,
              c_hi, c_plane, coef, kplane, light, state):
    """Capas de los vóxeles visibles entre t0 y t1 (una por tramo de vóxeles del mismo material).

    ``state``: (R, G, B premultiplicados, alfa acumulado, primer cuerpo, huella), se actualiza.
    """
    ix, iy, iz, tmx, tmy, tmz, tdx, tdy, tdz, dx_, dy_, dz_ = _ct_start(x0, y0, z0, u, v, w, t0, ct_i, ct_f)
    nx, ny, nz = ct_i[1], ct_i[2], ct_i[3]
    kb = ct_i[0]
    t = t0
    face = -1
    cur = -1
    done = False  # ya se ha pintado el tramo actual
    for _ in range(nx + ny + nz + 3):
        tn = min(tmx, tmy, tmz, t1)
        m = ct_mat[ix + nx * (iy + ny * iz)]
        if m != cur:
            cur = m
            done = False
        a = mat_rgba[m, 3] if m < mat_rgba.shape[0] else 0.0
        if a > 0.0 and not done:
            vis = False
            kf = face
            tol = 1e-9 * (1.0 + abs(c_hi))
            if clip_on and abs(t - c_hi) <= tol and c_hi < tn:
                kf = -2  # plano entre vóxeles sobre el del corte: la cara del corte
                vis = True
            elif not clip_on or t < c_lo or t >= c_hi:
                vis = True
            elif c_hi < tn - tol:  # el corte acaba dentro de este vóxel (si acaba en su cara, se ve el siguiente)
                kf = -2
                vis = True
            if vis:
                done = True
                if kf == -2:
                    nxx, nyy, nzz = clip[c_plane, 0], clip[c_plane, 1], clip[c_plane, 2]
                elif kf == -1:
                    if ks_in > 0 and kplane[ks_in] != 0:
                        nxx, nyy, nzz = coef[ks_in, 6], coef[ks_in, 7], coef[ks_in, 8]
                    else:
                        nxx, nyy, nzz = -u, -v, -w
                else:  # cara del vóxel: columna kf de R
                    nxx, nyy, nzz = ct_f[kf], ct_f[3 + kf], ct_f[6 + kf]
                ln = math.sqrt(nxx * nxx + nyy * nyy + nzz * nzz)
                if ln > 0.0:
                    nxx, nyy, nzz = nxx / ln, nyy / ln, nzz / ln
                else:
                    nxx, nyy, nzz = -u, -v, -w
                if nxx * u + nyy * v + nzz * w > 0.0:
                    nxx, nyy, nzz = -nxx, -nyy, -nzz
                if state[4] == 0:
                    state[4] = kb
                    state[5] = 1 + ((kb * 73 + m * 37 + (min(kf + 1, 0) + 7) * 151) % 255 + 255) % 255
                diff = abs(nxx * u + nyy * v + nzz * w)
                kd = max(nxx * light[0] + nyy * light[1] + nzz * light[2], 0.0)
                cr, cg, cb = mat_rgba[m, 0], mat_rgba[m, 1], mat_rgba[m, 2]
                if selected:
                    cr = 0.55 * cr + 0.45 * 1.0
                    cg = 0.55 * cg + 0.45 * 0.5
                    cb = 0.55 * cb + 0.45 * 0.055
                lit = 0.2 + 0.52 * diff + 0.36 * kd
                spec = 0.16 * diff ** 48
                f = 0.85 if kf == -2 else 1.0
                aa = 1.0 if a > 0.99 else a
                wgt = (1.0 - state[3]) * aa
                state[0] += wgt * min(1.0, (cr * lit + spec) * f)
                state[1] += wgt * min(1.0, (cg * lit + spec) * f)
                state[2] += wgt * min(1.0, (cb * lit + spec) * f)
                state[3] += wgt
                if state[3] > 0.985:
                    return
        if tn >= t1:
            return
        t = tn
        if tmx <= tmy and tmx <= tmz:
            ix += 1 if dx_ > 0.0 else -1
            tmx += tdx
            face = 0
            if ix < 0 or ix >= nx:
                return
        elif tmy <= tmz:
            iy += 1 if dy_ > 0.0 else -1
            tmy += tdy
            face = 1
            if iy < 0 or iy >= ny:
                return
        else:
            iz += 1 if dz_ > 0.0 else -1
            tmz += tdz
            face = 2
            if iz < 0 or iz >= nz:
                return


@njit(cache=True, nogil=True)
def _first_visible(x0, y0, z0, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok,
                   ndght, eval_ptr, eval_ks, vis, clip, max_steps, ct_i, ct_f, ct_mat, mat_vis):
    """Primer punto visible del rayo: (t, cuerpo, superficie).

    ``vis[kb]``: 1 si el cuerpo se ve. La superficie es -1-i en la cara del
    plano de corte i (véase ``_clip_interval``). En la caja CT se ve el primer
    vóxel de un material con ``mat_vis[m] == 1`` (superficie 0 en la cara de
    un vóxel interior).
    """
    kb = _locate1(x0, y0, z0, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok,
                  ndght)
    c_lo, c_hi, c_plane = _clip_interval(x0, y0, z0, u, v, w, clip)
    clip_on = c_hi > c_lo
    t = 0.0
    ks_in = 0
    inside = kb <= nbodys
    mem_ks = np.zeros(MEM, dtype=np.int64)
    mem_t = np.zeros(MEM)
    mem_sd = np.zeros(MEM, dtype=np.int64)
    slot = 0
    for _ in range(max_steps):
        kc = kb if kb <= nbodys else nbodys
        x = x0 + t * u
        y = y0 + t * v
        z = z0 + t * w
        tn, ks, sd, t_other, sd_other = _next_crossing(kc, t, x, y, z, u, v, w, coef, kplane, eval_ptr, eval_ks,
                                                       mem_ks, mem_t, mem_sd)
        t_end = tn if ks != 0 else INF
        if kb <= nbodys and kb == ct_i[0] and vis[kb] == 1:
            tv, face = _ct_first(x0, y0, z0, u, v, w, t, t_end, ct_i, ct_f, ct_mat, mat_vis, clip_on, c_lo, c_hi)
            if tv >= 0.0:
                if face == -2:
                    return tv, kb, -1 - c_plane
                return tv, kb, ks_in if face == -1 else 0
        elif kb <= nbodys and vis[kb] == 1:
            # Tramo visible [t, t_end) menos el intervalo oculto.
            if not clip_on or t < c_lo or t >= c_hi:
                return t, kb, ks_in
            if c_hi < t_end:
                return c_hi, kb, -1 - c_plane
        if ks == 0:
            break
        slot = _remember(ks, t_other, sd_other, mem_ks, mem_t, mem_sd, slot)
        t = t_end
        x = x0 + t * u
        y = y0 + t * v
        z = z0 + t * w
        kn = _locate1(x, y, z, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok,
                      ndght, ks, sd)
        if kn != kb:
            kb = kn
            ks_in = ks
            if kb > nbodys:
                if inside:
                    break
            else:
                inside = True
    return -1.0, 0, 0


@njit(parallel=True, cache=True)
def _render(orig, dirs, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok, ndght, eval_ptr,
            eval_ks, vis, clip, max_steps, ct_i, ct_f, ct_mat, mat_vis, out_t, out_kb, out_ks):
    n = dirs.shape[0]
    for i in prange(n):
        t, kb, ks = _first_visible(orig[i, 0], orig[i, 1], orig[i, 2], dirs[i, 0], dirs[i, 1], dirs[i, 2],
                                   nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok, ndght,
                                   eval_ptr, eval_ks, vis, clip, max_steps, ct_i, ct_f, ct_mat, mat_vis)
        out_t[i] = t
        out_kb[i] = kb
        out_ks[i] = ks


@njit(cache=True, nogil=True)
def _gradient(coef, ks, x, y, z):
    return (2.0 * coef[ks, 0] * x + coef[ks, 1] * y + coef[ks, 2] * z + coef[ks, 6],
            coef[ks, 1] * x + 2.0 * coef[ks, 3] * y + coef[ks, 4] * z + coef[ks, 7],
            coef[ks, 2] * x + coef[ks, 4] * y + 2.0 * coef[ks, 5] * z + coef[ks, 8])


@njit(cache=True, nogil=True)
def edge_key(kb, ks):
    """Huella (1..255) del cuerpo visible y de la cara del corte: donde cambia se dibuja una frontera."""
    return 1 + ((kb * 73 + (min(ks, 0) + 7) * 151) % 255 + 255) % 255


@njit(cache=True, nogil=True)
def _color_ray(x0, y0, z0, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok,
               ndght, eval_ptr, eval_ks, colors, selected, clip, light, max_steps, ct_i, ct_f, ct_mat, mat_rgba, out):
    """Color de un píxel como en el *shader*: capas translúcidas y la primera opaca.

    ``colors[kb]``: RGBA en [0, 1]; alfa 0 = oculto, 1 = opaco. ``out``:
    (R, G, B premultiplicados, alfa acumulado, primer cuerpo, huella).
    """
    kb = _locate1(x0, y0, z0, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok,
                  ndght)
    c_lo, c_hi, c_plane = _clip_interval(x0, y0, z0, u, v, w, clip)
    clip_on = c_hi > c_lo
    t = 0.0
    ks_in = 0
    inside = kb <= nbodys
    mem_ks = np.zeros(MEM, dtype=np.int64)
    mem_t = np.zeros(MEM)
    mem_sd = np.zeros(MEM, dtype=np.int64)
    slot = 0
    r = 0.0
    g = 0.0
    b = 0.0
    acc = 0.0
    first = 0
    key = 0
    drawn = False  # una sola capa por cuerpo atravesado
    for _ in range(max_steps):
        kc = kb if kb <= nbodys else nbodys
        x = x0 + t * u
        y = y0 + t * v
        z = z0 + t * w
        tn, ks, sd, t_other, sd_other = _next_crossing(kc, t, x, y, z, u, v, w, coef, kplane, eval_ptr, eval_ks,
                                                       mem_ks, mem_t, mem_sd)
        t_end = tn if ks != 0 else INF
        if kb <= nbodys and kb == ct_i[0]:
            if colors[kb, 3] > 0.0 and not drawn:
                drawn = True
                out[0] = r
                out[1] = g
                out[2] = b
                out[3] = acc
                out[4] = first
                out[5] = key
                _ct_color(x0, y0, z0, u, v, w, t, t_end, ks_in, ct_i, ct_f, ct_mat, mat_rgba, kb == selected, clip,
                          clip_on, c_lo, c_hi, c_plane, coef, kplane, light, out)
                r, g, b, acc = out[0], out[1], out[2], out[3]
                first = int(out[4])
                key = int(out[5])
                if acc > 0.985:
                    break
        elif kb <= nbodys and colors[kb, 3] > 0.0 and not drawn:
            tv = -1.0
            kf = ks_in
            if not clip_on or t < c_lo or t >= c_hi:
                tv = t
            elif c_hi < t_end:
                tv = c_hi
                kf = -1 - c_plane
            if tv >= 0.0:
                drawn = True
                if first == 0:
                    first = kb
                    key = edge_key(kb, kf)
                if kf < 0:
                    nx, ny, nz = clip[-1 - kf, 0], clip[-1 - kf, 1], clip[-1 - kf, 2]
                elif kf == 0:
                    nx, ny, nz = -u, -v, -w
                elif kplane[kf] != 0:
                    nx, ny, nz = coef[kf, 6], coef[kf, 7], coef[kf, 8]
                else:
                    nx, ny, nz = _gradient(coef, kf, x0 + tv * u, y0 + tv * v, z0 + tv * w)
                ln = math.sqrt(nx * nx + ny * ny + nz * nz)
                if ln > 0.0:
                    nx, ny, nz = nx / ln, ny / ln, nz / ln
                else:
                    nx, ny, nz = -u, -v, -w
                if nx * u + ny * v + nz * w > 0.0:  # hacia el observador
                    nx, ny, nz = -nx, -ny, -nz
                diff = abs(nx * u + ny * v + nz * w)
                kd = max(nx * light[0] + ny * light[1] + nz * light[2], 0.0)
                cr, cg, cb = colors[kb, 0], colors[kb, 1], colors[kb, 2]
                if kb == selected:
                    cr = 0.55 * cr + 0.45 * 1.0
                    cg = 0.55 * cg + 0.45 * 0.5
                    cb = 0.55 * cb + 0.45 * 0.055
                lit = 0.2 + 0.52 * diff + 0.36 * kd
                spec = 0.16 * diff ** 48
                f = 0.85 if kf < 0 else 1.0
                a = 1.0 if colors[kb, 3] > 0.99 else colors[kb, 3]
                wgt = (1.0 - acc) * a
                r += wgt * min(1.0, (cr * lit + spec) * f)
                g += wgt * min(1.0, (cg * lit + spec) * f)
                b += wgt * min(1.0, (cb * lit + spec) * f)
                acc += wgt
                if acc > 0.985:
                    break
        if ks == 0:
            break
        slot = _remember(ks, t_other, sd_other, mem_ks, mem_t, mem_sd, slot)
        t = t_end
        x = x0 + t * u
        y = y0 + t * v
        z = z0 + t * w
        kn = _locate1(x, y, z, u, v, w, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok,
                      ndght, ks, sd)
        if kn != kb:
            kb = kn
            ks_in = ks
            drawn = False
            if kb > nbodys:
                if inside:
                    break
            else:
                inside = True
    out[0] = r
    out[1] = g
    out[2] = b
    out[3] = acc
    out[4] = first
    out[5] = key


@njit(parallel=True, cache=True)
def _render_color(orig, dirs, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok, ndght,
                  eval_ptr, eval_ks, colors, selected, clip, light, max_steps, ct_i, ct_f, ct_mat, mat_rgba, out):
    n = dirs.shape[0]
    for i in prange(n):
        _color_ray(orig[i, 0], orig[i, 1], orig[i, 2], dirs[i, 0], dirs[i, 1], dirs[i, 2], nbodys, coef, kplane,
                   cons_ptr, cons_ks, cons_kf, dght_ptr, dght, dok, ndght, eval_ptr, eval_ks, colors, selected,
                   clip, light, max_steps, ct_i, ct_f, ct_mat, mat_rgba, out[i])


# ---------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------

def clip_array(clip) -> np.ndarray:
    """Planos de corte como matriz (m, 4) contigua (m = 0: sin corte)."""
    if clip is None:
        return np.zeros((0, 4))
    a = np.asarray(clip, dtype=float).reshape(-1, 4)
    return np.ascontiguousarray(a)


NMAT_CT = 100  # materiales de un CT: 0..99 (formato I2)


def ct_tables(geo) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ct_i, ct_f, ct_mat) para los núcleos: cuerpo CT, colocación y material de cada vóxel."""
    # La matriz de materiales va siempre de solo lectura (la del CT lo es): Numba compila una versión para cada
    # caso, y así la que compila warmup() al arrancar (sin CT) es la misma que usan los CT.
    ct = getattr(geo, "ct", None)
    if ct is None or getattr(geo, "ct_body", None) is None:
        mat = np.zeros(1, dtype=np.uint8)
        mat.flags.writeable = False
        return np.zeros(4, dtype=np.int64), np.zeros(15), mat
    from ..pengeom.ct import ct_frame

    fr = ct_frame(geo)
    nx, ny, nz = ct.shape
    ct_i = np.array([geo.ct_body, nx, ny, nz], dtype=np.int64)
    ct_f = np.concatenate([fr.rotation.reshape(-1), fr.shift, fr.voxel]).astype(np.float64)
    mat = ct.material_u8().reshape(-1)
    if int(ct.max_material) >= NMAT_CT:
        mat = np.minimum(mat, NMAT_CT - 1)
        mat.flags.writeable = False
    return ct_i, ct_f, mat


def default_material_visibility() -> np.ndarray:
    """Todos los materiales de los vóxeles se ven, salvo el vacío (0)."""
    v = np.ones(NMAT_CT, dtype=np.int64)
    v[0] = 0
    return v


def default_material_rgba() -> np.ndarray:
    from .colors import hex_to_rgb, material_color

    out = np.zeros((NMAT_CT, 4))
    for m in range(1, NMAT_CT):
        out[m, :3] = np.array(hex_to_rgb(material_color(m))) / 255.0
        out[m, 3] = 1.0
    return out


def _rays(origins, dirs) -> tuple[np.ndarray, np.ndarray]:
    d = np.ascontiguousarray(np.asarray(dirs, dtype=float).reshape(-1, 3))
    d = d / np.linalg.norm(d, axis=1)[:, None]
    o = np.ascontiguousarray(np.broadcast_to(np.asarray(origins, dtype=float).reshape(-1, 3), d.shape))
    return o, d


@dataclass
class Segment:
    t: float  # distancia desde el origen del rayo a la entrada en el cuerpo
    body: int  # índice PENGEOM (NBODYS+1 = fuera de la envolvente)
    material: int
    surface: int  # superficie por la que entra (0 en el origen)


class RayTracer:
    """Trazador de rayos sobre una geometría compilada."""

    def __init__(self, geo):
        if not geo.ok:
            raise ValueError("La geometría no está compilada correctamente.")
        self.geo = geo
        self.nbodys = geo.nbodys
        t = locate_fast.build_tables(geo)
        self.t = t
        self.material = np.array([0] + [b.material for b in geo.bodies] + [0], dtype=np.int64)
        self._args = (self.nbodys, t["coef"], t["kplane"], t["cons_ptr"], t["cons_ks"], t["cons_kf"],
                      t["dght_ptr"], t["dght"], t["dok"], t["ndght"], t["eval_ptr"], t["eval_ks"])
        self.ct_i, self.ct_f, self.ct_mat = ct_tables(geo)

    @property
    def has_ct(self) -> bool:
        return int(self.ct_i[0]) > 0

    def _ct_args(self, mat: np.ndarray) -> tuple:
        return self.ct_i, self.ct_f, self.ct_mat, mat

    # -- un rayo ---------------------------------------------------------------
    def trace(self, origin, direction, max_seg: int = 10000, all_crossings: bool = False) -> list[Segment]:
        """Cuerpos que atraviesa el rayo (``direction`` se normaliza)."""
        o = np.asarray(origin, dtype=float)
        d = np.asarray(direction, dtype=float)
        d = d / np.linalg.norm(d)
        out_t = np.zeros(max_seg)
        out_kb = np.zeros(max_seg, dtype=np.int64)
        out_ks = np.zeros(max_seg, dtype=np.int64)
        n = _trace(o[0], o[1], o[2], d[0], d[1], d[2], *self._args, out_t, out_kb, out_ks, max_seg,
                   all_crossings)
        return [Segment(float(out_t[i]), int(out_kb[i]), int(self.material[min(out_kb[i], self.nbodys + 1)]),
                        int(out_ks[i])) for i in range(n)]

    def step_stops(self, origin, direction, max_seg: int = 10000) -> list[Segment]:
        """Puntos en que se detendría STEP de PENGEOM con un paso infinito y sin detectores.

        STEP cruza sin parar las regiones de vacío; se detiene tras cada
        superficie cruzada que deja la partícula en un cuerpo con material
        (aunque sea el mismo cuerpo o el mismo material) y termina al salir de
        la envolvente.
        """
        segs = self.trace(origin, direction, max_seg, all_crossings=True)
        out = []
        for sg in segs[1:]:
            if sg.body > self.nbodys:
                break
            if sg.material != 0:
                out.append(sg)
        return out

    def first_visible(self, origin, direction, visible: np.ndarray | None = None, clip=None,
                      max_steps: int = 4096, mat_vis: np.ndarray | None = None) -> tuple[float, int, int]:
        """(t, cuerpo, superficie) del primer punto visible; cuerpo 0 si no hay.

        ``clip``: planos (nx, ny, nz, d); se oculta la intersección de los
        semiespacios n·r > d. En la cara del corte i la superficie es -1-i.
        ``mat_vis``: materiales de los vóxeles del CT que se ven (1) o no (0).
        """
        o = np.asarray(origin, dtype=float)
        d = np.asarray(direction, dtype=float)
        d = d / np.linalg.norm(d)
        vis = self.default_visibility() if visible is None else np.asarray(visible, dtype=np.int64)
        mv = default_material_visibility() if mat_vis is None else np.asarray(mat_vis, dtype=np.int64)
        t, kb, ks = _first_visible(o[0], o[1], o[2], d[0], d[1], d[2], *self._args, vis, clip_array(clip),
                                   max_steps, *self._ct_args(mv))
        return float(t), int(kb), int(ks)

    # -- muchos rayos ----------------------------------------------------------
    def default_visibility(self) -> np.ndarray:
        """Cuerpos que se ven: los que tienen material (no la envolvente ni los módulos vacíos)."""
        vis = np.zeros(self.nbodys + 2, dtype=np.int64)
        for b in self.geo.bodies:
            if b.material > 0 and b.origin != "enclosure":
                vis[b.index] = 1
        return vis

    def render(self, origins: np.ndarray, dirs: np.ndarray, visible: np.ndarray | None = None, clip=None,
               max_steps: int = 4096, mat_vis: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Primer punto visible de muchos rayos: (t, cuerpo, superficie)."""
        o, d = _rays(origins, dirs)
        n = d.shape[0]
        vis = self.default_visibility() if visible is None else np.asarray(visible, dtype=np.int64)
        mv = default_material_visibility() if mat_vis is None else np.asarray(mat_vis, dtype=np.int64)
        cl = clip_array(clip)
        out_t = np.empty(n)
        out_kb = np.empty(n, dtype=np.int64)
        out_ks = np.empty(n, dtype=np.int64)
        if HAVE_NUMBA:
            with PARALLEL_LOCK:
                _render(o, d, *self._args, vis, cl, max_steps, *self._ct_args(mv), out_t, out_kb, out_ks)
        else:  # pragma: no cover - muy lento; solo para pocos rayos
            for i in range(n):
                out_t[i], out_kb[i], out_ks[i] = _first_visible(o[i, 0], o[i, 1], o[i, 2], d[i, 0], d[i, 1],
                                                                d[i, 2], *self._args, vis, cl, max_steps,
                                                                *self._ct_args(mv))
        return out_t, out_kb, out_ks

    def render_color(self, origins: np.ndarray, dirs: np.ndarray, colors: np.ndarray, selected: int = 0,
                     clip=None, light=(0.0, 0.0, 1.0), max_steps: int = 4096,
                     mat_rgba: np.ndarray | None = None) -> np.ndarray:
        """Color de muchos rayos, como el *shader* de la vista 3D (para cuando no hay GPU).

        ``colors``: (NBODYS+2, 4) en [0, 1] (alfa: 0 oculto, 1 opaco, otro
        valor translúcido). ``mat_rgba``: (n, 4) en [0, 1], color y visibilidad
        de cada material en los vóxeles del CT. Devuelve (n, 6): RGB
        premultiplicado, alfa acumulado, primer cuerpo visible y huella para
        las fronteras.
        """
        o, d = _rays(origins, dirs)
        n = d.shape[0]
        cols = np.ascontiguousarray(colors, dtype=float)
        mr = default_material_rgba() if mat_rgba is None else np.ascontiguousarray(mat_rgba, dtype=float)
        cl = clip_array(clip)
        lt = np.asarray(light, dtype=float)
        out = np.zeros((n, 6))
        if HAVE_NUMBA:
            with PARALLEL_LOCK:
                _render_color(o, d, *self._args, cols, int(selected), cl, lt, max_steps, *self._ct_args(mr), out)
        else:  # pragma: no cover
            for i in range(n):
                _color_ray(o[i, 0], o[i, 1], o[i, 2], d[i, 0], d[i, 1], d[i, 2], *self._args, cols,
                           int(selected), cl, lt, max_steps, *self._ct_args(mr), out[i])
        return out

    def segment_through(self, origin, direction, kb: int, t_from: float = 0.0) -> tuple[float, float] | None:
        """Tramo (entrada, salida) del rayo dentro del cuerpo ``kb`` a partir de ``t_from``."""
        segs = self.trace(origin, direction)
        for s0, s1 in zip(segs, segs[1:] + [None]):
            end = s1.t if s1 is not None else INF
            if s0.body == kb and end > t_from:
                return max(s0.t, t_from), end
        return None

    def normal(self, ks: int, p: np.ndarray, direction: np.ndarray) -> np.ndarray:
        """Normal unitaria de la superficie ``ks`` en ``p``, orientada hacia el observador."""
        c = self.t["coef"][ks]
        axx, axy, axz, ayy, ayz, azz, ax, ay, az, _ = c
        x, y, z = p
        g = np.array([2 * axx * x + axy * y + axz * z + ax,
                      axy * x + 2 * ayy * y + ayz * z + ay,
                      axz * x + ayz * y + 2 * azz * z + az])
        n = np.linalg.norm(g)
        if n == 0:
            return -np.asarray(direction, dtype=float)
        g = g / n
        return -g if g @ direction > 0 else g


_WARMUP_GEO = """Esfera para compilar el trazador
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   esfera
INDICES=( 1, 1, 1, 0,-1)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   1)   esfera
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def warmup() -> None:
    """Compila los núcleos del trazador (unos segundos la primera vez; luego quedan en caché)."""
    if not HAVE_NUMBA:
        return
    from ..pengeom.compiler import compile_geometry
    from ..pengeom.parser import parse_geo_text

    geo = compile_geometry(parse_geo_text(_WARMUP_GEO))
    if not geo.ok:  # pragma: no cover
        return
    rt = RayTracer(geo)
    o = np.array([[0.0, 0.0, -5.0]] * 4)
    d = np.array([[0.0, 0.0, 1.0]] * 4)
    rt.trace(o[0], d[0])
    rt.first_visible(o[0], d[0])
    rt.render(o, d, clip=[(1.0, 0.0, 0.0, 0.0)])
    rt.render_color(o, d, np.ones((geo.nbodys + 2, 4)))

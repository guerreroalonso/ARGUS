"""Shaders GLSL (4.1 core) de la vista 3D.

El *fragment shader* traza un rayo por píxel recorriendo el árbol de cuerpos
de PENGEOM, con la misma lógica que el trazador de la CPU (``raytrace.py``):

* LOCATE en el origen del rayo (bajando por los módulos);
* cortes con las superficies KSURF (KFLAG <= 4) del cuerpo actual; si el
  punto está sobre una superficie (franja difusa de anchura ``uTol`` más el
  error de ``float32`` a esa distancia), solo cuenta el corte en el sentido
  del movimiento, como en STEPSI;
* tras cruzar una superficie se impone su lado en LOCATE y se recuerda su
  otro corte (no se recalcula desde el punto de cruce).

Los datos de la geometría van en texturas (véase ``scene3d.gpu_tables``).
Se trabaja en coordenadas relativas al *origen flotante* para no perder
precisión en ``float32``.

``uMode`` 0 da la imagen sombreada (alfa = huella del primer cuerpo y
superficie visibles, para dibujar las fronteras); 1 da el índice del primer
cuerpo visible en RGB (pruebas contra la CPU).
"""

VERTEX = """#version 410 core
// Triángulo que cubre toda la pantalla (sin búferes de vértices).
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

TRACE = """#version 410 core
uniform sampler2D uCoef;    // 4 texeles por superficie
uniform isampler2D uBody;   // 2 texeles por cuerpo
uniform isampler2D uCons;   // ks*4 + kf (superficies que delimitan cada cuerpo)
uniform isampler2D uEval;   // ks (superficies que se evalúan en cada cuerpo)
uniform isampler2D uDght;   // kb*2 + ok (hijas de cada módulo)
uniform sampler2D uColor;   // RGBA8 por cuerpo; alfa = visibilidad
uniform usampler3D uCtMat;  // material de cada vóxel de la caja CT (R8UI)
uniform sampler2D uMatColor; // RGBA8 por material de los vóxeles; alfa = visibilidad

uniform int uCtBody;        // cuerpo de la caja CT (0: no hay)
uniform vec3 uCtO;          // vértice de la caja CT (relativo al origen flotante)
uniform vec3 uCtAx;         // ejes x, y, z de la caja CT en el laboratorio (columnas de R)
uniform vec3 uCtAy;
uniform vec3 uCtAz;
uniform vec3 uCtVox;        // tamaño del vóxel (cm)
uniform ivec3 uCtN;         // NX, NY, NZ

uniform int uNBodys;
uniform int uMaxSteps;
uniform float uTol;

uniform vec2 uFull;     // tamaño de la imagen completa (píxeles)
uniform float uScale;   // píxeles de la imagen completa por píxel de este búfer

uniform vec3 uCamPos;   // punto de t = 0 de los rayos (relativo al origen flotante)
uniform float uT0;      // t de salida: 0 en perspectiva; en ortográfica, negativo (delante de la escena)
uniform vec3 uRight;
uniform vec3 uUp;
uniform vec3 uFwd;
uniform float uTan;
uniform int uOrtho;
uniform float uHalf;
uniform vec3 uKey;      // dirección hacia la segunda luz

uniform int uClipN;     // planos de corte: se oculta la intersección de los semiespacios n·r > d
uniform vec4 uClip0;
uniform vec4 uClip1;
uniform vec4 uClip2;

uniform int uSelected;
uniform int uMode;
uniform vec3 uBg0;      // fondo abajo
uniform vec3 uBg1;      // fondo arriba

layout(location = 0) out vec4 fragColor;
// Profundidad (t a lo largo del rayo) de lo primero opaco y de lo primero visible, para tapar con la geometría lo que
// se dibuja encima (trayectorias). Se descarta si el búfer de destino no tiene una segunda textura.
layout(location = 1) out vec4 fragDepth;

const float BIG = 3.0e38;
const int MEM = 8;

float gFirstT;
float gOpaqueT;

// Una capa pintada en t = tv con opacidad alpha (accA: la acumulada después).
void depthAt(float tv, float alpha, float accA) {
    if (gFirstT >= BIG) gFirstT = tv;
    if (gOpaqueT >= BIG && (alpha > 0.99 || accA > 0.985)) gOpaqueT = tv;
}

int memKs[MEM];
float memT[MEM];
int memSd[MEM];
int memSlot;

ivec2 A(int i) { return ivec2(i & 4095, i >> 12); }

// Anchura de la franja difusa a la distancia t del origen del rayo: la fija
// (proporcional al encuadre) más el error de redondeo de o + t·d en float32.
float tolAt(float t) { return uTol + 3.0e-7 * abs(t); }

// A, B, C de F(p + s·d) = A s² + B s + C, y el gradiente de F en p. Cada superficie
// está centrada en su propio punto de referencia (4.º texel): se evalúa en q = p - c.
vec3 abc(int ks, vec3 p, vec3 d, out vec3 g) {
    int i = 4 * ks;
    vec4 c0 = texelFetch(uCoef, A(i), 0);
    vec4 c1 = texelFetch(uCoef, A(i + 1), 0);
    vec4 c2 = texelFetch(uCoef, A(i + 2), 0);
    p -= texelFetch(uCoef, A(i + 3), 0).xyz;
    vec3 lin = vec3(c1.z, c1.w, c2.x);
    if (c2.z > 0.5) {
        g = lin;
        return vec3(0.0, dot(d, lin), dot(p, lin) + c2.y);
    }
    // F = rᵀ M r + lin·r + a0, M simétrica con los términos cruzados a la mitad.
    vec3 m0 = vec3(c0.x, 0.5 * c0.y, 0.5 * c0.z);
    vec3 m1 = vec3(0.5 * c0.y, c0.w, 0.5 * c1.x);
    vec3 m2 = vec3(0.5 * c0.z, 0.5 * c1.x, c1.y);
    vec3 mp = vec3(dot(m0, p), dot(m1, p), dot(m2, p));
    vec3 md = vec3(dot(m0, d), dot(m1, d), dot(m2, d));
    g = 2.0 * mp + lin;
    return vec3(dot(d, md), dot(d, g), dot(p, mp) + dot(p, lin) + c2.y);
}

int side(int ks, vec3 p, vec3 d, float band) {
    vec3 g;
    vec3 q = abc(ks, p, d, g);
    float tol = band * length(g);
    if (q.z < -tol) return 1;
    if (q.z > tol) return 2;
    return q.y < 0.0 ? 1 : 2;
}

int locate(vec3 p, vec3 d, int ksF, int sdF, float band) {
    int kb0 = uNBodys;
    for (int level = 0; level < 256; ++level) {
        ivec4 m = texelFetch(uBody, A(2 * kb0 + 1), 0);
        int descend = -1;
        for (int jd = m.x; jd < m.y; ++jd) {
            int e = texelFetch(uDght, A(jd), 0).x;
            if ((e & 1) == 0) continue;
            int kb = e >> 1;
            ivec4 b = texelFetch(uBody, A(2 * kb), 0);
            bool ok = true;
            for (int c = b.x; c < b.y; ++c) {
                int v = texelFetch(uCons, A(c), 0).x;
                int ks = v >> 2;
                int sd = ks == ksF ? sdF : side(ks, p, d, band);
                if (sd != (v & 3)) { ok = false; break; }
            }
            if (!ok) continue;
            if (kb == kb0) return kb;
            if (texelFetch(uBody, A(2 * kb + 1), 0).z > 1) { descend = kb; break; }
            return kb;
        }
        if (descend < 0) return uNBodys + 1;
        kb0 = descend;
    }
    return uNBodys + 1;
}

int memFind(int ks) {
    for (int m = 0; m < MEM; ++m) {
        if (memKs[m] == ks) return m;
    }
    return -1;
}

void remember(int ks, float tOther, int sdOther) {
    int m = memFind(ks);
    if (m < 0) {
        m = memSlot;
        memSlot = (memSlot + 1) % MEM;
        memKs[m] = ks;
    }
    memT[m] = tOther;
    memSd[m] = sdOther;
}

// Corte más cercano con las superficies que se evalúan en el cuerpo kc (reglas de STEPSI).
void nextCrossing(int kc, float t0, vec3 p, vec3 d,
                  out float tHit, out int ksHit, out int sdHit, out float tOther, out int sdOther) {
    tHit = BIG; ksHit = 0; sdHit = 0; tOther = BIG; sdOther = 0;
    float band = tolAt(t0);
    ivec4 b = texelFetch(uBody, A(2 * kc), 0);
    for (int e = b.z; e < b.w; ++e) {
        int ks = texelFetch(uEval, A(e), 0).x;
        int m = memFind(ks);
        if (m >= 0) {
            float tk = memT[m];
            if (tk > t0 && tk < tHit) { tHit = tk; ksHit = ks; sdHit = memSd[m]; tOther = BIG; sdOther = 0; }
            continue;
        }
        vec3 g;
        vec3 q = abc(ks, p, d, g);
        float a = q.x;
        float bb = q.y;
        float c = q.z;
        bool amb = abs(c) <= band * length(g);
        if (abs(a) < 1.0e-30) {  // plano (o recta paralela al eje de la cuádrica): un corte
            if (!amb && bb != 0.0) {
                float s = -c / bb;
                if (s > 0.0 && t0 + s < tHit) {
                    tHit = t0 + s; ksHit = ks; sdHit = bb < 0.0 ? 1 : 2; tOther = BIG; sdOther = 0;
                }
            }
            continue;
        }
        float disc = bb * bb - 4.0 * a * c;
        if (disc <= 0.0) continue;
        float sq = sqrt(disc);
        float qq = -0.5 * (bb + (bb >= 0.0 ? sq : -sq));
        float r1 = qq / a;
        float r2 = c / qq;
        float lo = min(r1, r2);
        float hi = max(r1, r2);
        int sLo = a > 0.0 ? 1 : 2;
        int sHi = a > 0.0 ? 2 : 1;
        if (!amb) {
            if (lo > 0.0) {
                if (t0 + lo < tHit) { tHit = t0 + lo; ksHit = ks; sdHit = sLo; tOther = t0 + hi; sdOther = sHi; }
            } else if (hi > 0.0 && t0 + hi < tHit) {
                tHit = t0 + hi; ksHit = ks; sdHit = sHi; tOther = BIG; sdOther = 0;
            }
        } else if (bb * a < 0.0) {  // sobre la superficie: solo cuenta el corte hacia delante
            float s = -bb / a;
            if (s > 0.0 && t0 + s < tHit) { tHit = t0 + s; ksHit = ks; sdHit = sHi; tOther = BIG; sdOther = 0; }
        }
    }
}

vec4 clipPlane(int i) { return i == 0 ? uClip0 : (i == 1 ? uClip1 : uClip2); }

// Luz y color de una capa (cuerpo o tramo de vóxeles) con normal n; se suma a lo acumulado.
void shade(vec3 base, vec3 n, vec3 d, bool clipFace, float alpha, inout vec3 acc, inout float accA) {
    float ln = length(n);
    n = ln > 0.0 ? n / ln : -d;
    if (dot(n, d) > 0.0) n = -n;  // hacia el observador
    // Luz desde la cámara y otra desde arriba a la izquierda (se mueven con la cámara).
    float diff = abs(dot(n, d));
    float kd = max(dot(n, uKey), 0.0);
    vec3 c = min(base * (0.2 + 0.52 * diff + 0.36 * kd) + vec3(0.16 * pow(diff, 48.0)), vec3(1.0));
    if (clipFace) c *= 0.85;
    float a = alpha > 0.99 ? 1.0 : alpha;
    acc += (1.0 - accA) * a * c;
    accA += (1.0 - accA) * a;
}

// Vóxeles de la caja CT entre t0 y t1 (recorrido DDA en el sistema del CT, como CTSTEP): una capa por tramo de
// vóxeles del mismo material que se vea. kf: -1 cara de entrada (superficie ksIn), -2 cara del corte, 0/1/2 cara
// de un vóxel perpendicular a x/y/z del CT.
void ctMarch(vec3 o, vec3 d, float t0, float t1, int ksIn, bool clipOn, float cLo, float cHi, int cPlane,
             inout vec3 acc, inout float accA, inout int firstKb, inout int firstKey) {
    vec3 q = o - uCtO;
    vec3 lo = vec3(dot(uCtAx, q), dot(uCtAy, q), dot(uCtAz, q));
    vec3 dl = vec3(dot(uCtAx, d), dot(uCtAy, d), dot(uCtAz, d));
    vec3 vs = uCtVox;
    ivec3 idx = clamp(ivec3(floor((lo + t0 * dl) / vs)), ivec3(0), uCtN - 1);
    vec3 tMax;
    vec3 tDel;
    ivec3 stp;
    for (int a = 0; a < 3; ++a) {
        if (dl[a] > 0.0) {
            tMax[a] = (float(idx[a] + 1) * vs[a] - lo[a]) / dl[a]; tDel[a] = vs[a] / dl[a]; stp[a] = 1;
        } else if (dl[a] < 0.0) {
            tMax[a] = (float(idx[a]) * vs[a] - lo[a]) / dl[a]; tDel[a] = -vs[a] / dl[a]; stp[a] = -1;
        } else {
            tMax[a] = BIG; tDel[a] = BIG; stp[a] = 0;
        }
    }
    float t = t0;
    int face = -1;
    int cur = -1;
    bool done = false;
    int nmax = uCtN.x + uCtN.y + uCtN.z + 3;
    for (int i = 0; i < nmax; ++i) {
        float tn = min(min(tMax.x, tMax.y), min(tMax.z, t1));
        int m = int(texelFetch(uCtMat, idx, 0).r);
        if (m != cur) { cur = m; done = false; }
        vec4 col = texelFetch(uMatColor, ivec2(m, 0), 0);
        if (col.a > 0.0 && !done) {
            // «vis» y no un t negativo como marca: en proyección ortográfica los rayos empiezan en t < 0.
            bool vis = false;
            float tv = t;
            int kf = face;
            if (clipOn && abs(t - cHi) <= tolAt(cHi) && cHi < tn) {
                // El plano entre vóxeles coincide con el del corte: es la cara del corte (si no, el redondeo
                // pintaría unos píxeles como cara del vóxel y otros como corte).
                tv = cHi;
                kf = -2;
                vis = true;
            } else if (!clipOn || t < cLo || t >= cHi) {
                vis = true;
            } else if (cHi < tn - tolAt(cHi)) {
                // El corte acaba dentro de este vóxel; si acaba en su cara, lo que se ve es el siguiente.
                tv = cHi;
                kf = -2;
                vis = true;
            }
            if (vis) {
                done = true;
                if (firstKb == 0) {
                    firstKb = uCtBody;
                    firstKey = 1 + ((uCtBody * 73 + m * 37 + (min(kf + 1, 0) + 7) * 151) % 255 + 255) % 255;
                }
                if (uMode == 1) { accA = 1.0; return; }
                vec3 n;
                if (kf == -2) {
                    n = clipPlane(cPlane).xyz;
                } else if (kf == -1) {
                    if (ksIn > 0) { vec3 g; abc(ksIn, o + tv * d, d, g); n = g; } else { n = -d; }
                } else {
                    n = kf == 0 ? uCtAx : (kf == 1 ? uCtAy : uCtAz);
                }
                vec3 base = col.rgb;
                if (uCtBody == uSelected) base = mix(base, vec3(1.0, 0.5, 0.055), 0.45);
                shade(base, n, d, kf == -2, col.a, acc, accA);
                depthAt(tv, col.a, accA);
                if (accA > 0.985) return;
            }
        }
        if (tn >= t1) return;
        t = tn;
        if (tMax.x <= tMax.y && tMax.x <= tMax.z) {
            idx.x += stp.x; tMax.x += tDel.x; face = 0;
            if (idx.x < 0 || idx.x >= uCtN.x) return;
        } else if (tMax.y <= tMax.z) {
            idx.y += stp.y; tMax.y += tDel.y; face = 1;
            if (idx.y < 0 || idx.y >= uCtN.y) return;
        } else {
            idx.z += stp.z; tMax.z += tDel.z; face = 2;
            if (idx.z < 0 || idx.z >= uCtN.z) return;
        }
    }
}

// Huella del cuerpo visible (y de la cara del corte, si lo es): donde cambia se dibuja una frontera.
// No se usa la superficie de entrada: planos repetidos (mismo plano, otro índice) darían puntos sueltos.
int edgeKey(int kb, int kf) { return 1 + ((kb * 73 + (min(kf, 0) + 7) * 151) % 255 + 255) % 255; }

void main() {
    vec2 ndc = 2.0 * (gl_FragCoord.xy * uScale) / uFull - 1.0;
    float aspect = uFull.x / uFull.y;
    vec3 o;
    vec3 d;
    if (uOrtho == 1) {
        o = uCamPos + ndc.x * uHalf * aspect * uRight + ndc.y * uHalf * uUp;
        d = uFwd;
    } else {
        o = uCamPos;
        d = normalize(uFwd + ndc.x * uTan * aspect * uRight + ndc.y * uTan * uUp);
    }
    vec3 bg = mix(uBg0, uBg1, 0.5 * (ndc.y + 1.0));

    // Intervalo del rayo oculto por los planos de corte.
    float cLo = uT0;
    float cHi = uT0 - 1.0;
    int cPlane = -1;
    if (uClipN > 0) {
        cHi = BIG;
        for (int i = 0; i < uClipN; ++i) {
            vec4 P = clipPlane(i);
            float nd = dot(P.xyz, d);
            float f0 = dot(P.xyz, o) - P.w;
            float a0 = uT0;
            float a1 = BIG;
            if (abs(nd) < 1.0e-30) {
                if (f0 <= 0.0) { a0 = BIG; a1 = -BIG; }
            } else {
                float tp = -f0 / nd;
                if (nd > 0.0) { a0 = max(tp, uT0); } else { a1 = tp; }
            }
            cLo = max(cLo, a0);
            if (a1 < cHi) { cHi = a1; cPlane = i; }
        }
    }
    bool clipOn = cHi > cLo;

    for (int m = 0; m < MEM; ++m) { memKs[m] = 0; memT[m] = BIG; memSd[m] = 0; }
    memSlot = 0;
    gFirstT = BIG;
    gOpaqueT = BIG;

    float t = uT0;
    int kb = locate(o + t * d, d, 0, 0, tolAt(t));
    bool inside = kb <= uNBodys;
    int ksIn = 0;
    bool drawn = false;  // ya se ha pintado el tramo actual (una sola capa por cuerpo atravesado)
    vec3 acc = vec3(0.0);
    float accA = 0.0;
    int firstKb = 0;
    int firstKey = 0;
    for (int step = 0; step < uMaxSteps; ++step) {
        int kc = kb <= uNBodys ? kb : uNBodys + 1;
        vec3 p = o + t * d;
        float tn;
        int ks;
        int sd;
        float tO;
        int sdO;
        nextCrossing(kc, t, p, d, tn, ks, sd, tO, sdO);
        float tEnd = ks != 0 ? tn : BIG;
        if (kb <= uNBodys && kb == uCtBody && !drawn) {
            drawn = true;
            if (texelFetch(uColor, A(kb), 0).a > 0.0) {
                ctMarch(o, d, t, tEnd, ksIn, clipOn, cLo, cHi, cPlane, acc, accA, firstKb, firstKey);
                if (accA > 0.985 || (uMode == 1 && firstKb != 0)) break;
            }
        } else if (kb <= uNBodys && !drawn) {
            vec4 col = texelFetch(uColor, A(kb), 0);
            if (col.a > 0.0) {
                bool seen = false;
                float tv = t;
                int kf = ksIn;
                if (!clipOn || t < cLo || t >= cHi) {
                    seen = true;
                } else if (cHi < tEnd) {
                    seen = true;
                    tv = cHi;
                    kf = -1 - cPlane;
                }
                if (seen) {
                    drawn = true;
                    if (firstKb == 0) { firstKb = kb; firstKey = edgeKey(kb, kf); }
                    if (uMode == 1) break;
                    vec3 n;
                    if (kf < 0) {
                        n = clipPlane(-1 - kf).xyz;
                    } else if (kf == 0) {
                        n = -d;
                    } else {
                        vec3 g;
                        abc(kf, o + tv * d, d, g);
                        n = g;
                    }
                    float ln = length(n);
                    n = ln > 0.0 ? n / ln : -d;
                    if (dot(n, d) > 0.0) n = -n;  // hacia el observador
                    // Luz desde la cámara y otra desde arriba a la izquierda (se mueven con la cámara).
                    float diff = abs(dot(n, d));
                    float kd = max(dot(n, uKey), 0.0);
                    vec3 base = col.rgb;
                    if (kb == uSelected) base = mix(base, vec3(1.0, 0.5, 0.055), 0.45);
                    vec3 c = min(base * (0.2 + 0.52 * diff + 0.36 * kd) + vec3(0.16 * pow(diff, 48.0)), vec3(1.0));
                    if (kf < 0) c *= 0.85;
                    float a = col.a > 0.99 ? 1.0 : col.a;
                    acc += (1.0 - accA) * a * c;
                    accA += (1.0 - accA) * a;
                    depthAt(tv, col.a, accA);
                    if (accA > 0.985) break;
                }
            }
        }
        if (ks == 0) break;
        if (4.0e-7 * (tn - t) > tolAt(tn)) {
            // Paso muy largo para float32 (p. ej. desde lejos hasta una capa delgada): se avanza casi
            // hasta el corte sin cruzarlo y se vuelve a calcular desde allí, con más precisión.
            t += (1.0 - 1.0e-4) * (tn - t);
            continue;
        }
        remember(ks, tO, sdO);
        t = tEnd;
        p = o + t * d;
        int kn = locate(p, d, ks, sd, tolAt(t));
        if (kn != kb) {
            kb = kn;
            ksIn = ks;
            drawn = false;
            if (kb > uNBodys) {
                if (inside) break;
            } else {
                inside = true;
            }
        }
    }
    fragDepth = vec4(gOpaqueT, gFirstT, 0.0, 1.0);
    if (uMode == 1) {
        fragColor = vec4(float(firstKb & 255), float((firstKb >> 8) & 255), float((firstKb >> 16) & 255), 255.0)
                    / 255.0;
        return;
    }
    vec3 rgb = acc + (1.0 - accA) * bg;
    fragColor = vec4(clamp(rgb, 0.0, 1.0), float(firstKey) / 255.0);
}
"""

DISPLAY = """#version 410 core
// Copia la imagen trazada a la pantalla; opcionalmente oscurece las fronteras
// (píxeles cuya huella de cuerpo y superficie cambia respecto a un vecino). Con uCopyDepth, copia también la
// profundidad (de la vista previa, de menos resolución, a la imagen completa).
uniform sampler2D uImg;
uniform sampler2D uDepthImg;
uniform int uCopyDepth;
uniform vec2 uDst;      // tamaño del destino (píxeles)
uniform int uEdges;
layout(location = 0) out vec4 fragColor;
layout(location = 1) out vec4 fragDepth;

void main() {
    vec2 uv = gl_FragCoord.xy / uDst;
    vec4 c = texture(uImg, uv);
    if (uEdges == 1) {
        ivec2 sz = textureSize(uImg, 0);
        ivec2 tp = clamp(ivec2(uv * vec2(sz)), ivec2(0), sz - 1);
        float k = texelFetch(uImg, tp, 0).a;
        float diff = 0.0;
        diff += float(abs(texelFetch(uImg, clamp(tp + ivec2(1, 0), ivec2(0), sz - 1), 0).a - k) > 0.001);
        diff += float(abs(texelFetch(uImg, clamp(tp + ivec2(0, 1), ivec2(0), sz - 1), 0).a - k) > 0.001);
        diff += float(abs(texelFetch(uImg, clamp(tp - ivec2(1, 0), ivec2(0), sz - 1), 0).a - k) > 0.001);
        diff += float(abs(texelFetch(uImg, clamp(tp - ivec2(0, 1), ivec2(0), sz - 1), 0).a - k) > 0.001);
        c.rgb *= 1.0 - 0.14 * min(diff, 3.0);
    }
    fragColor = vec4(c.rgb, 1.0);
    if (uCopyDepth == 1) {
        ivec2 dsz = textureSize(uDepthImg, 0);
        fragDepth = texelFetch(uDepthImg, clamp(ivec2(uv * vec2(dsz)), ivec2(0), dsz - 1), 0);
    } else {
        fragDepth = vec4(3.0e38, 3.0e38, 0.0, 1.0);
    }
}
"""

# ---------------------------------------------------------------------------------------------------------------------
# Trayectorias (cascada de partículas) sobre la imagen trazada: líneas con la profundidad de la geometría.
# ---------------------------------------------------------------------------------------------------------------------

# Proyección de un punto relativo al origen flotante, igual que los rayos del trazado (uCamPos, uRight, uUp, uFwd,
# uTan, uHalf, uOrtho de frame_uniforms): perspectiva con w = profundidad (recorta lo que queda detrás de la cámara)
# u ortográfica.
_PROJECT = """
uniform vec3 uCamPos;
uniform vec3 uRight;
uniform vec3 uUp;
uniform vec3 uFwd;
uniform float uTan;
uniform float uHalf;
uniform float uAspect;
uniform float uNear;
uniform int uOrtho;

vec4 project(vec3 rel) {
    float x = dot(rel, uRight);
    float y = dot(rel, uUp);
    float z = dot(rel, uFwd);
    if (uOrtho == 1) return vec4(x / (uHalf * uAspect), y / uHalf, 0.0, 1.0);
    return vec4(x / (uTan * uAspect), y / uTan, z - 2.0 * uNear, z);
}
"""

TRACK_VERTEX = """#version 410 core
layout(location = 0) in vec3 aPos;    // relativo al origen de los datos (cm)
layout(location = 1) in vec4 aInfo;   // tiempo (ns), log10(E/eV), partícula (KPAR), lluvia
layout(location = 2) in float aGen;   // generación (0: primaria)
uniform vec3 uShift;       // origen de los datos - origen flotante del trazado
uniform int uColorMode;    // 0 partícula, 1 energía, 2 lluvia, 3 generación
uniform vec3 uKind[8];
uniform vec3 uPal[12];
uniform sampler2D uLut;    // 256 x 1: color por energía
uniform float uLogLo;
uniform float uLogHi;
uniform int uMask;         // bit k: partícula k visible
uniform float uShowerLo;   // lluvias que se ven: [lo, hi)
uniform float uShowerHi;
uniform float uStagger;    // ns entre una lluvia y la siguiente (0: todas a la vez)
uniform float uAlpha;
""" + _PROJECT + """
out vec3 vRel;
out float vT;
out vec4 vColor;
flat out int vDrop;
out float vEdge;

void main() {
    vEdge = 0.0;
    vec3 rel = aPos + uShift - uCamPos;
    vRel = rel;
    vT = aInfo.x + max(aInfo.w - uShowerLo, 0.0) * uStagger;  // la primera lluvia que se ve sale en t = 0
    int kind = int(aInfo.z + 0.5);
    vDrop = (((uMask >> kind) & 1) == 0 || aInfo.w < uShowerLo - 0.5 || aInfo.w >= uShowerHi - 0.5) ? 1 : 0;
    vec3 c;
    if (uColorMode == 1) {
        float u = clamp((aInfo.y - uLogLo) / max(uLogHi - uLogLo, 1.0e-6), 0.0, 1.0);
        c = texture(uLut, vec2(0.5 / 256.0 + u * 255.0 / 256.0, 0.5)).rgb;
    } else if (uColorMode == 2) {
        c = uPal[int(mod(aInfo.w, 12.0))];
    } else if (uColorMode == 3) {
        c = uPal[min(int(aGen + 0.5), 11)];
    } else {
        c = uKind[clamp(kind, 0, 7)];
    }
    vColor = vec4(c, uAlpha);
    gl_Position = project(rel);
}
"""

# Líneas con grosor (en píxeles) y bordes suaves: cada tramo se convierte en un rectángulo de pantalla. Si la
# tarjeta no acepta el geometry shader, se dibujan líneas de un píxel (sin él).
TRACK_GEOMETRY = """#version 410 core
layout(lines) in;
layout(triangle_strip, max_vertices = 4) out;
in vec3 vRel[];
in float vT[];
in vec4 vColor[];
flat in int vDrop[];
in float vEdge[];
uniform vec2 uViewport;    // píxeles
uniform float uWidth;      // grosor (píxeles)
uniform float uNear;
out vec3 gRel;
out float gT;
out vec4 gColor;
flat out int gDrop;
out float gEdge;

void emit(vec4 p, vec2 off, vec3 rel, float t, vec4 c, float e) {
    gl_Position = vec4(p.xy + off * p.w, p.zw);
    gRel = rel; gT = t; gColor = c; gEdge = e; gDrop = vDrop[0];
    EmitVertex();
}

void main() {
    if (vDrop[0] == 1) return;
    vec4 p0 = gl_in[0].gl_Position;
    vec4 p1 = gl_in[1].gl_Position;
    vec3 r0 = vRel[0], r1 = vRel[1];
    float t0 = vT[0], t1 = vT[1];
    vec4 c0 = vColor[0], c1 = vColor[1];
    // Lo que queda detrás de la cámara (perspectiva): se recorta en el plano cercano.
    float lim = 1.5 * uNear;
    if (p0.w < lim && p1.w < lim) return;
    if (p0.w < lim) { float f = (lim - p0.w) / (p1.w - p0.w); p0 = mix(p0, p1, f); r0 = mix(r0, r1, f);
                      t0 = mix(t0, t1, f); c0 = mix(c0, c1, f); }
    if (p1.w < lim) { float f = (lim - p1.w) / (p0.w - p1.w); p1 = mix(p1, p0, f); r1 = mix(r1, r0, f);
                      t1 = mix(t1, t0, f); c1 = mix(c1, c0, f); }
    vec2 s0 = p0.xy / p0.w * 0.5 * uViewport;
    vec2 s1 = p1.xy / p1.w * 0.5 * uViewport;
    vec2 d = s1 - s0;
    float len = length(d);
    vec2 dir = len > 1.0e-6 ? d / len : vec2(1.0, 0.0);
    vec2 n = vec2(-dir.y, dir.x) * (0.5 * uWidth + 1.0);   // un píxel más para el borde suave
    vec2 ext = dir * 0.5;                                  // medio píxel por los extremos
    vec2 k = 2.0 / uViewport;
    float edge = (0.5 * uWidth + 1.0) / max(0.5 * uWidth, 0.5);
    emit(p0, (n - ext) * k, r0, t0, c0, edge);
    emit(p0, (-n - ext) * k, r0, t0, c0, -edge);
    emit(p1, (n + ext) * k, r1, t1, c1, edge);
    emit(p1, (-n + ext) * k, r1, t1, c1, -edge);
    EndPrimitive();
}
"""

TRACK_FRAGMENT = """#version 410 core
in vec3 vRel;
in float vT;
in vec4 vColor;
flat in int vDrop;
in float vEdge;
uniform vec3 uFwd;
uniform int uOrtho;
uniform sampler2D uDepth;  // (t de lo primero opaco, t de lo primero visible) de la imagen trazada
uniform int uHasDepth;
uniform float uEps;        // margen para no tapar lo que está justo en la superficie
uniform int uAnimate;
uniform float uTime;       // ns: lo posterior no se dibuja todavía
uniform float uTrail;      // ns: lo más reciente se ve más intenso (0: todo igual)
uniform float uHiddenAlpha;  // lo que tapa la geometría opaca: 0 no se ve; si no, tenue
uniform float uEdgeMax;    // |vEdge| en el borde exterior del rectángulo de la línea (geometry shader)
layout(location = 0) out vec4 fragColor;

void main() {
    if (vDrop == 1) discard;
    if (uAnimate == 1 && vT > uTime) discard;
    float dist = uOrtho == 1 ? dot(vRel, uFwd) : length(vRel);
    float a = vColor.a;
    if (uHasDepth == 1) {
        vec4 dd = texelFetch(uDepth, ivec2(gl_FragCoord.xy), 0);
        float eps = uEps + 1.0e-5 * abs(dist);
        if (dist > dd.x + eps) {
            if (uHiddenAlpha <= 0.0) discard;
            a *= uHiddenAlpha;
        } else if (dist > dd.y + eps) {
            a *= 0.8;  // detrás de algo translúcido
        }
    }
    if (uAnimate == 1 && uTrail > 0.0) {
        a *= mix(1.0, 0.35, clamp((uTime - vT) / uTrail, 0.0, 1.0));
    }
    a *= 1.0 - smoothstep(1.0, max(uEdgeMax, 1.0001), abs(vEdge));  // borde suave (con el geometry shader)
    fragColor = vec4(vColor.rgb, a);
}
"""

# El fragment shader de las líneas con grosor: las mismas entradas con los nombres del geometry shader.
TRACK_FRAGMENT_WIDE = TRACK_FRAGMENT
for _v, _g in (("vRel", "gRel"), ("vT", "gT"), ("vColor", "gColor"), ("vDrop", "gDrop"), ("vEdge", "gEdge")):
    TRACK_FRAGMENT_WIDE = TRACK_FRAGMENT_WIDE.replace(_v, _g)

HEAD_VERTEX = """#version 410 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aColor;
uniform vec3 uShift;
uniform float uSize;
""" + _PROJECT + """
out vec3 vRel;
out vec3 vColor;

void main() {
    vec3 rel = aPos + uShift - uCamPos;
    vRel = rel;
    vColor = aColor;
    gl_Position = project(rel);
    gl_PointSize = uSize;
}
"""

HEAD_FRAGMENT = """#version 410 core
in vec3 vRel;
in vec3 vColor;
uniform vec3 uFwd;
uniform int uOrtho;
uniform sampler2D uDepth;
uniform int uHasDepth;
uniform float uEps;
layout(location = 0) out vec4 fragColor;

void main() {
    vec2 q = gl_PointCoord * 2.0 - 1.0;
    float r2 = dot(q, q);
    if (r2 > 1.0) discard;
    float dist = uOrtho == 1 ? dot(vRel, uFwd) : length(vRel);
    float a = 1.0;
    if (uHasDepth == 1) {
        vec4 dd = texelFetch(uDepth, ivec2(gl_FragCoord.xy), 0);
        if (dist > dd.x + uEps + 1.0e-5 * abs(dist)) a = 0.25;
    }
    vec3 c = mix(vec3(1.0), vColor, smoothstep(0.0, 0.55, r2));  // centro claro: se distinguen de las líneas
    fragColor = vec4(c, a * (1.0 - smoothstep(0.7, 1.0, r2)));
}
"""

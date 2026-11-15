"""Lettura della planimetria come la fa l'app: geometria, appartamenti, locali e pareti.

È una traduzione fedele del codice dell'app (stessi nomi, stessi passi, stessi numeri), così i locali
("Camera 2") e le pareti ("Parete 3") che si vedono nella pagina web sono gli stessi delle cartelle del
telefono (pareti/<DWG>/<APP n>/<locale>/Parete N/):
- core/dxf_floorplan.cc + core/floorplan_builder.cc   → leggi_dxf()
- shared/FloorPlanAnalyzer.kt                         → analizza()
- shared/PlanLayers.kt                                → layer_visibili(), colori_layer()
- shared/RoomWallExtractor.kt                         → pareti_appartamento()
Le coordinate del disegno sono conservate in float32 come nell'app (FloatArray), i calcoli delle pareti
in double come in Kotlin. Solo DXF ASCII (i DWG si leggono nell'app con LibreDWG).
"""
from __future__ import annotations

import math
import re
from array import array
from pathlib import Path

try:  # la trasformata della distanza è molto più veloce con numpy; senza, la stessa in Python puro
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None


class DxfNonLeggibile(ValueError):
    pass


def _f32(v: float) -> float:
    return array("f", (v,))[0]


_SPAZI_JAVA = "".join(chr(i) for i in range(33))


def _trim(s: str) -> str:
    """String.trim() di Java/Kotlin (trimJava): toglie i caratteri <= ' '."""
    return s.strip(_SPAZI_JAVA)


def _round_java(v: float) -> int:
    return 0 if math.isnan(v) else math.floor(v + 0.5)


# =====================================================================================================
# DXF → FloorPlan (core/dxf_floorplan.cc, core/floorplan_builder.cc)
# =====================================================================================================

K_MAX_SEGMENTS = 2_000_000
K_MAX_INSERT_DEPTH = 6  # FloorPlanBuilder::kMaxInsertDepth


class Piano:
    """DwgFloorPlan: layers, segmenti (x1, y1, x2, y2, layer) e testi (testo, x, y, altezza, layer)."""

    def __init__(self):
        self.layers: list[str] = []
        self._indice: dict[str, int] = {}
        self.seg = array("f")       # x1, y1, x2, y2 per segmento
        self.seg_layer = array("i")
        self.testi: list[str] = []
        self.tdati = array("f")     # x, y, altezza per testo
        self.t_layer = array("i")

    def n_segmenti(self) -> int:
        return len(self.seg_layer)

    def layer(self, nome: str) -> int:
        i = self._indice.get(nome)
        if i is None:
            i = len(self.layers)
            self.layers.append(nome)
            self._indice[nome] = i
        return i

    def tx(self, i): return self.tdati[i * 3]
    def ty(self, i): return self.tdati[i * 3 + 1]
    def th(self, i): return self.tdati[i * 3 + 2]


class _Affine:
    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    def apply(self, x, y):
        return self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f

    def then(self, o: "_Affine") -> "_Affine":
        """this ∘ o: prima o, poi this (come Affine::Then del C++)."""
        return _Affine(self.a * o.a + self.c * o.b, self.b * o.a + self.d * o.b,
                       self.a * o.c + self.c * o.d, self.b * o.c + self.d * o.d,
                       self.a * o.e + self.c * o.f + self.e, self.b * o.e + self.d * o.f + self.f)


def _ocs_mirror(z: float) -> _Affine:
    return _Affine(a=-1.0) if z < 0 else _Affine()


def _insert_transform(ix, iy, sx, sy, rot, bx, by) -> _Affine:
    sx = sx if sx != 0 else 1.0
    sy = sy if sy != 0 else 1.0
    cr, sr = math.cos(rot), math.sin(rot)
    m = _Affine(cr * sx, sr * sx, -sr * sy, cr * sy)
    m.e = ix - (m.a * bx + m.c * by)
    m.f = iy - (m.b * bx + m.d * by)
    return m


def _strtod(s: str) -> float:
    """strtod: legge il numero all'inizio della stringa, 0 se non c'è."""
    m = re.match(r"\s*[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", s)
    return float(m.group(0)) if m else 0.0


def _strtol(s: str) -> int:
    m = re.match(r"\s*[+-]?\d+", s)
    return int(m.group(0)) if m else 0


def _strip_mtext(s: str) -> str:
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "{}":
            i += 1
            continue
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        i += 1
        code = s[i]
        if code in "PX":
            out.append("\n")
        elif code == "~":
            out.append(" ")
        elif code in "\\{}":
            out.append(code)
        elif code in "LlOoKk":
            pass
        else:
            end = s.find(";", i)
            if end != -1 and end - i < 128:
                i = end
        i += 1
    return "".join(out)


def _decodifica(raw: bytes) -> str:
    try:
        testo = raw.decode("utf-8")
    except UnicodeDecodeError:
        testo = raw.decode("latin-1")
    return re.sub(r"\\[Uu]\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), testo)


class _Entita:
    __slots__ = ("tipo", "gruppi", "vertici")

    def __init__(self, tipo):
        self.tipo, self.gruppi, self.vertici = tipo, [], []

    def trova(self, code):
        for c, v in self.gruppi:
            if c == code:
                return v
        return None

    def num(self, code, fallback=0.0):
        v = self.trova(code)
        return _strtod(v.decode("latin-1")) if v is not None else fallback

    def intero(self, code, fallback=0):
        v = self.trova(code)
        return _strtol(v.decode("latin-1")) if v is not None else fallback


def leggi_dxf(percorso: Path | str) -> Piano:
    dati = Path(percorso).read_bytes()
    if dati.startswith(b"\xef\xbb\xbf"):
        dati = dati[3:]
    if dati.startswith(b"AutoCAD Binary DXF"):
        raise DxfNonLeggibile("DXF binario non supportato: salvalo come DXF ASCII")
    righe = dati.split(b"\n")
    gruppi = []
    for k in range(0, len(righe) - 1, 2):
        codice = righe[k].strip()
        valore = righe[k + 1]
        if valore.endswith(b"\r"):
            valore = valore[:-1]
        try:
            gruppi.append((int(codice), valore))
        except ValueError:
            raise DxfNonLeggibile("File DXF non valido")
    if not gruppi:
        raise DxfNonLeggibile("File DXF vuoto")

    def marker(i, v):
        return gruppi[i][0] == 0 and gruppi[i][1].strip() == v

    def salta(i):
        i += 1
        while i < len(gruppi) and gruppi[i][0] != 0:
            i += 1
        return i

    def record(i):
        e = _Entita(gruppi[i][1].strip().decode("latin-1"))
        i += 1
        while i < len(gruppi) and gruppi[i][0] != 0:
            e.gruppi.append(gruppi[i])
            i += 1
        return e, i

    def entita(i, stop, out):
        while i < len(gruppi) and not marker(i, stop) and not marker(i, b"ENDSEC"):
            e, i = record(i)
            if e.tipo == "POLYLINE":
                while i < len(gruppi) and marker(i, b"VERTEX"):
                    v, i = record(i)
                    e.vertici.append(v)
                if i < len(gruppi) and marker(i, b"SEQEND"):
                    _, i = record(i)
            out.append(e)
        return i

    entities, blocchi = [], {}
    i = 0
    while i < len(gruppi):
        if not marker(i, b"SECTION"):
            i += 1
            continue
        i += 1
        nome = gruppi[i][1].strip() if i < len(gruppi) and gruppi[i][0] == 2 else b""
        if nome == b"ENTITIES":
            i = entita(salta(i), b"ENDSEC", entities)
        elif nome == b"BLOCKS":
            i = salta(i)
            while i < len(gruppi) and not marker(i, b"ENDSEC"):
                if not marker(i, b"BLOCK"):
                    i = salta(i)
                    continue
                testa, i = record(i)
                corpo = []
                i = entita(i, b"ENDBLK", corpo)
                if i < len(gruppi) and marker(i, b"ENDBLK"):
                    _, i = record(i)
                n = testa.trova(2)
                if n is not None:
                    blocchi[n.strip()] = (testa.num(10), testa.num(20), corpo)
        while i < len(gruppi) and not marker(i, b"ENDSEC"):
            i += 1
    del gruppi

    piano = Piano()

    def pieno():
        return len(piano.seg_layer) >= K_MAX_SEGMENTS

    def segmento(xf, x1, y1, x2, y2, layer):
        if not all(map(math.isfinite, (x1, y1, x2, y2))):
            return
        a, b = xf.apply(x1, y1)
        c, d = xf.apply(x2, y2)
        piano.seg.extend((a, b, c, d))
        piano.seg_layer.append(layer)

    def arco(xf, cx, cy, r, start, sweep, layer):
        if not (r > 0) or not math.isfinite(r):
            return
        steps = int(math.ceil(abs(sweep) / (math.pi / 18.0)))
        steps = max(4, min(72, steps))
        px, py = cx + r * math.cos(start), cy + r * math.sin(start)
        for k in range(1, steps + 1):
            ang = start + sweep * k / steps
            nx, ny = cx + r * math.cos(ang), cy + r * math.sin(ang)
            segmento(xf, px, py, nx, ny, layer)
            px, py = nx, ny

    def bulge(xf, x0, y0, x1, y1, b, layer):
        dx, dy = x1 - x0, y1 - y0
        corda = math.sqrt(dx * dx + dy * dy)
        if abs(b) < 1e-9 or corda < 1e-12:
            segmento(xf, x0, y0, x1, y1, layer)
            return
        theta = 4.0 * math.atan(b)
        h = (corda / 2.0) / math.tan(theta / 2.0)
        cx = (x0 + x1) / 2.0 - h * dy / corda
        cy = (y0 + y1) / 2.0 + h * dx / corda
        r = abs(corda / (2.0 * math.sin(theta / 2.0)))
        arco(xf, cx, cy, r, math.atan2(y0 - cy, x0 - cx), theta, layer)

    def polilinea(xf, xs, ys, bs, chiusa, layer):
        n = len(xs)
        if n < 2:
            return
        for k in range(n if chiusa else n - 1):
            j = (k + 1) % n
            bulge(xf, xs[k], ys[k], xs[j], ys[j], bs[k], layer)

    def testo(xf, x, y, h, valore, layer_nome):
        if not valore or not math.isfinite(x) or not math.isfinite(y):
            return
        wx, wy = xf.apply(x, y)
        scala = math.sqrt(abs(xf.a * xf.d - xf.b * xf.c))
        piano.testi.append(valore)
        piano.tdati.extend((wx, wy, h * scala))
        piano.t_layer.append(piano.layer(layer_nome))

    def gestisci(e, xf, layer_ereditato, profondita):
        v = e.trova(8)
        layer_nome = v.strip().decode("latin-1") if v is not None else "0"
        if layer_nome == "0" and layer_ereditato:
            layer_nome = layer_ereditato
        ocs = xf.then(_ocs_mirror(e.num(230, 1.0)))
        t = e.tipo
        if t == "LINE":
            segmento(xf, e.num(10), e.num(20), e.num(11), e.num(21), piano.layer(layer_nome))
        elif t == "LWPOLYLINE":
            xs, ys, bs = [], [], []
            for c, val in e.gruppi:
                if c == 10:
                    xs.append(_strtod(val.decode("latin-1"))); ys.append(0.0); bs.append(0.0)
                elif c == 20 and ys:
                    ys[-1] = _strtod(val.decode("latin-1"))
                elif c == 42 and bs:
                    bs[-1] = _strtod(val.decode("latin-1"))
            polilinea(ocs, xs, ys, bs, (e.intero(70) & 1) != 0, piano.layer(layer_nome))
        elif t == "POLYLINE":
            flags = e.intero(70)
            if flags & (8 | 16 | 64):
                return
            polilinea(ocs, [v.num(10) for v in e.vertici], [v.num(20) for v in e.vertici],
                      [v.num(42) for v in e.vertici], (flags & 1) != 0, piano.layer(layer_nome))
        elif t == "ARC":
            start = e.num(50) * math.pi / 180.0
            sweep = e.num(51) * math.pi / 180.0 - start
            while sweep <= 0:
                sweep += 2 * math.pi
            arco(ocs, e.num(10), e.num(20), e.num(40), start, sweep, piano.layer(layer_nome))
        elif t == "CIRCLE":
            arco(ocs, e.num(10), e.num(20), e.num(40), 0, 2 * math.pi, piano.layer(layer_nome))
        elif t == "TEXT":
            val = e.trova(1)
            if val is None:
                return
            allineato = e.intero(72) != 0 or e.intero(73) != 0
            x = e.num(11) if allineato else e.num(10)
            y = e.num(21) if allineato else e.num(20)
            testo(ocs, x, y, e.num(40), _decodifica(val), layer_nome)
        elif t == "MTEXT":
            raw = b"".join(val for c, val in e.gruppi if c == 3)
            ultimo = e.trova(1)
            if ultimo is not None:
                raw += ultimo
            testo(xf, e.num(10), e.num(20), e.num(40), _strip_mtext(_decodifica(raw)), layer_nome)
        elif t == "INSERT":
            if profondita >= K_MAX_INSERT_DEPTH:
                return
            n = e.trova(2)
            blocco = blocchi.get(n.strip()) if n is not None else None
            if blocco is None:
                return
            bx, by, corpo = blocco
            ins = _insert_transform(e.num(10), e.num(20), e.num(41, 1.0), e.num(42, 1.0),
                                    e.num(50) * math.pi / 180.0, bx, by)
            figlio = ocs.then(ins)
            for c in corpo:
                if pieno():
                    return
                gestisci(c, figlio, layer_nome, profondita + 1)

    for e in entities:
        if pieno():
            break
        if e.intero(67) == 1:  # paper space
            continue
        gestisci(e, _Affine(), "", 0)
    if not piano.seg_layer:
        raise DxfNonLeggibile("Nessuna geometria trovata nel DXF")
    return piano


# =====================================================================================================
# FloorPlanAnalyzer.kt
# =====================================================================================================

_LAYER_APPARTAMENTI = ("SUP COMMERCIAL", "SUPERFICI COMMERCIAL", "SUPERFICIE COMMERCIAL", "APPARTAMENT",
                       "ALLOGGI", "UNITA ABITATIV", "UNITÀ ABITATIV")
_ETICHETTA_APP = re.compile(r"^\s*(?:u\.?\s*[ai]\.?|app\.?|appartamento|alloggio|unit[aà](?:\s+abitativa)?"
                            r"|interno|int\.?)\s*(?:n\.?|n°|nr\.?)?\s*0*(\d+)\s*$", re.I)
_AREA = re.compile(r"(\d+(?:[.,]\d+)?)\s*m(?:q|²|2)\b", re.I)
_LETTERE = re.compile(r"[^\W\d_]{2}")
_ALTEZZA = re.compile(r"(?:^|[\s(])(?:h|altezza|hm)\.?(?:\s*nett[ao])?\s*[=:.]?\s*(\d+(?:[.,]\d+)?)\s*(cm|m)?"
                      r"(?![\d.,]*\s*m(?:q|²|2))", re.I)
_TITOLO_PIANO = re.compile(r"^\s*piano\b.*", re.I)
_EPS = 1e-3


def normalizza(v: str) -> str:
    r = _trim(v).upper()
    while r.startswith("-") or r.startswith("_"):
        r = _trim(r[1:])
    return r


def area_con_segno(p: list[float]) -> float:
    n = len(p) // 2
    s = 0.0
    j = n - 1
    for i in range(n):
        s += p[j * 2] * p[i * 2 + 1] - p[i * 2] * p[j * 2 + 1]
        j = i
    return _f32(s / 2)


def contiene(p: list[float], x: float, y: float) -> bool:
    n = len(p) // 2
    dentro = False
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = p[i * 2], p[i * 2 + 1], p[j * 2], p[j * 2 + 1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def _centroide(p):
    n = len(p) // 2
    return [_f32(sum(p[0::2]) / n), _f32(sum(p[1::2]) / n)]


def _distanza_poligono(x, y, p) -> float:
    if contiene(p, x, y):
        return 0.0
    n = len(p) // 2
    best = float("inf")
    j = n - 1
    for i in range(n):
        ax, ay, bx, by = p[j * 2], p[j * 2 + 1], p[i * 2], p[i * 2 + 1]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else ((x - ax) * dx + (y - ay) * dy) / l2
        t = max(0.0, min(1.0, t))
        best = min(best, _f32(math.hypot(x - ax - t * dx, y - ay - t * dy)))
        j = i
    return best


def punto_etichetta(p: list[float]) -> list[float]:
    n = len(p) // 2
    if n < 3:
        return _centroide(p) if n > 0 else [0.0, 0.0]
    area = cx = cy = 0.0
    j = n - 1
    for i in range(n):
        xj, yj, xi, yi = p[j * 2], p[j * 2 + 1], p[i * 2], p[i * 2 + 1]
        cr = xj * yi - xi * yj
        area += cr
        cx += (xj + xi) * cr
        cy += (yj + yi) * cr
        j = i
    centro = _centroide(p) if abs(area) < 1e-9 else [_f32(cx / (3 * area)), _f32(cy / (3 * area))]
    if contiene(p, centro[0], centro[1]):
        return centro
    y = centro[1]
    incroci = []
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = p[i * 2], p[i * 2 + 1], p[j * 2], p[j * 2 + 1]
        if (yi > y) != (yj > y):
            incroci.append((xj - xi) * (y - yi) / (yj - yi) + xi)
        j = i
    incroci.sort()
    best = -1.0
    for k in range(0, len(incroci) - 1, 2):
        w = incroci[k + 1] - incroci[k]
        if w > best:
            best = w
            centro = [(incroci[k] + incroci[k + 1]) / 2, y]
    return centro


def tipo_locale(nome: str) -> str:
    n = _trim(normalizza(nome).replace(".", " "))
    if any(k in n for k in ("GIORNO", "SOGGIORNO", "LIVING", "SALONE", "SALOTTO")):
        return "Soggiorno"
    if "CUCIN" in n or "COTTURA" in n:
        return "Cucina"
    if "MATRIMONIAL" in n:
        return "Camera matrimoniale"
    if "STANZA" in n or "CAMERA" in n or "LETTO" in n:
        return "Camera"
    if "ANTIBAGNO" in n:
        return "Antibagno"
    if "BAGNO" in n or n == "WC" or "SERVIZIO" in n:
        return "Bagno"
    if n.startswith("DISIMP") or n.startswith("DISB") or "CORRIDOIO" in n or "DISTRIBU" in n:
        return "Disimpegno"
    if n.startswith("RIP") or "RIPOST" in n or "ARMADIO" in n:
        return "Ripostiglio"
    if "INGRESSO" in n or "ATRIO" in n:
        return "Ingresso"
    if "STUDIO" in n:
        return "Studio"
    if "LAVANDERIA" in n:
        return "Lavanderia"
    if "TERRAZZ" in n or "BALCON" in n or "LOGGIA" in n:
        return "Balcone"
    low = _trim(nome).lower()
    return nome if not low else low[0].upper() + low[1:]


_ORDINE_TIPI = {"Soggiorno": 0, "Cucina": 1, "Camera matrimoniale": 2, "Camera": 3, "Bagno": 4, "Antibagno": 5,
                "Studio": 6, "Ingresso": 7, "Disimpegno": 8, "Ripostiglio": 9}


class Locale:
    def __init__(self, sorgente, tipo, area, x, y):
        self.sorgente, self.tipo, self.area, self.x, self.y = sorgente, tipo, area, x, y
        self.nome = tipo  # displayName
        self.altezza = float("nan")


class Appartamento:
    def __init__(self, numero, etichetta, poligono, lx, ly):
        self.numero, self.etichetta, self.poligono, self.lx, self.ly = numero, etichetta, poligono, lx, ly
        self.locali: list[Locale] = []

    @property
    def nome(self) -> str:
        """WallIds.dwgApartmentFolderName."""
        return f"APP {self.numero}"


def _perimetri(piano: Piano) -> list[list[float]]:
    app_layer = [any(k in normalizza(l) for k in _LAYER_APPARTAMENTI) for l in piano.layers]
    if not any(app_layer):
        return []
    seg, catene, corrente, layer_corrente = piano.seg, [], None, -1
    for i in range(piano.n_segmenti()):
        layer = piano.seg_layer[i]
        if layer < 0 or layer >= len(app_layer) or not app_layer[layer]:
            corrente = None
            continue
        x1, y1, x2, y2 = seg[i * 4], seg[i * 4 + 1], seg[i * 4 + 2], seg[i * 4 + 3]
        if (corrente is not None and layer == layer_corrente and abs(corrente[-2] - x1) < _EPS
                and abs(corrente[-1] - y1) < _EPS):
            corrente.extend((x2, y2))
        else:
            corrente = [x1, y1, x2, y2]
            layer_corrente = layer
            catene.append(corrente)
    candidati, area_max = [], 0.0
    for c in catene:
        if len(c) < 6:
            continue
        candidati.append(c)
        area_max = max(area_max, abs(area_con_segno(c)))
    perimetri = []
    for p in candidati:
        a = abs(area_con_segno(p))
        if a < _f32(area_max * _f32(0.05)):
            continue
        doppio = False
        for o in perimetri:
            ao = abs(area_con_segno(o))
            co, cp = _centroide(o), _centroide(p)
            if abs(a - ao) <= 0.01 * a and math.hypot(cp[0] - co[0], cp[1] - co[1]) <= 0.01 * math.sqrt(a):
                doppio = True
                break
        if not doppio:
            perimetri.append(p)
    return perimetri


def _assegna_etichette(piano: Piano, perimetri) -> list[Appartamento]:
    etichette, visti = [], set()
    for i, t in enumerate(piano.testi):
        m = _ETICHETTA_APP.fullmatch(t)
        if not m:
            continue
        numero = int(m.group(1))
        if numero not in visti:
            visti.add(numero)
            etichette.append((i, numero))
    coppie = []
    for l, (ti, _) in enumerate(etichette):
        for p, per in enumerate(perimetri):
            coppie.append((_distanza_poligono(piano.tx(ti), piano.ty(ti), per), l, p))
    coppie.sort(key=lambda c: c[0])
    usato_p, usato_l, apps = [False] * len(perimetri), [False] * len(etichette), []
    for _, l, p in coppie:
        if usato_l[l] or usato_p[p]:
            continue
        usato_l[l] = usato_p[p] = True
        ti = etichette[l][0]
        apps.append(Appartamento(etichette[l][1], _trim(piano.testi[ti]), perimetri[p], piano.tx(ti), piano.ty(ti)))
    prossimo = 1
    for a in apps:
        prossimo = max(prossimo, a.numero + 1)
    for l, (ti, numero) in enumerate(etichette):
        if not usato_l[l]:
            apps.append(Appartamento(numero, _trim(piano.testi[ti]), [], piano.tx(ti), piano.ty(ti)))
    for p, per in enumerate(perimetri):
        if not usato_p[p]:
            c = _centroide(per)
            apps.append(Appartamento(prossimo, None, per, c[0], c[1]))
            prossimo += 1
    return apps


def _altezza_locale(piano: Piano, ti: int) -> float:
    size = max(piano.th(ti), 1e-6)
    best, best_d = float("nan"), float("inf")
    for i, t in enumerate(piano.testi):
        d = math.hypot(piano.tx(i) - piano.tx(ti), piano.ty(i) - piano.ty(ti))
        if d > 6 * size or d >= best_d:
            continue
        m = _ALTEZZA.search(t)
        if not m:
            continue
        v = _f32(float(m.group(1).replace(",", ".")))
        if (m.group(2) or "").lower() == "cm" or v > 10.0:
            v = _f32(v / 100.0)
        if v < _f32(1.8) or v > 6.0:
            continue
        best, best_d = v, d
    return best


def _trova_locali(piano: Piano) -> list[Locale]:
    aree, nomi = [], []
    for i, t0 in enumerate(piano.testi):
        t = _trim(t0)
        if _AREA.search(t):
            aree.append(i)
        elif len(t) <= 30 and "\n" not in t and _LETTERE.search(t) and not _ETICHETTA_APP.fullmatch(t):
            nomi.append(i)
    coppie = []
    for n in nomi:
        h = max(piano.th(n), 1e-6)
        for a in aree:
            dy = _f32(piano.ty(n) - piano.ty(a))
            dx = abs(_f32(piano.tx(n) - piano.tx(a)))
            if 0 < dy <= 4 * h and dx <= 4 * h:
                coppie.append((_f32(math.hypot(dx, dy)), n, a))
    coppie.sort(key=lambda c: c[0])
    usati_n, usati_a, locali = set(), set(), []
    for _, n, a in coppie:
        if n in usati_n or a in usati_a:
            continue
        m = _AREA.search(piano.testi[a])
        if not m:
            continue
        valore = _f32(float(m.group(1).replace(",", ".")))
        usati_n.add(n)
        usati_a.add(a)
        sorgente = _trim(piano.testi[n])
        loc = Locale(sorgente, tipo_locale(sorgente), valore, piano.tx(n), piano.ty(n))
        loc.altezza = _altezza_locale(piano, n)
        locali.append(loc)
    return locali


def _distribuisci(apps: list[Appartamento], locali: list[Locale]) -> None:
    altri = None
    for loc in locali:
        prop = None
        for a in apps:
            if a.poligono and contiene(a.poligono, loc.x, loc.y):
                prop = a
                break
        if prop is None:
            best = float("inf")
            for a in apps:
                if a.poligono:
                    continue
                d = _f32(math.hypot(a.lx - loc.x, a.ly - loc.y))
                if d < best:
                    best, prop = d, a
        if prop is None:
            if altri is None:
                altri = Appartamento(2 ** 31 - 1, None, [], loc.x, loc.y)
                apps.append(altri)
            prop = altri
        prop.locali.append(loc)


def _numera_locali(a: Appartamento) -> None:
    import functools

    def confronto(x, y):
        o = (_ORDINE_TIPI.get(x.tipo, 10) > _ORDINE_TIPI.get(y.tipo, 10)) - (_ORDINE_TIPI.get(x.tipo, 10) < _ORDINE_TIPI.get(y.tipo, 10))
        if o:
            return o
        o = (x.tipo > y.tipo) - (x.tipo < y.tipo)  # String.compareTo: ordine dei caratteri UTF-16
        if o:
            return o
        return (y.area > x.area) - (y.area < x.area)

    a.locali.sort(key=functools.cmp_to_key(confronto))
    totali: dict[str, int] = {}
    for loc in a.locali:
        totali[loc.tipo] = totali.get(loc.tipo, 0) + 1
    visti: dict[str, int] = {}
    for loc in a.locali:
        if totali[loc.tipo] > 1:
            visti[loc.tipo] = visti.get(loc.tipo, 0) + 1
            loc.nome = f"{loc.tipo} {visti[loc.tipo]}"


def analizza(piano: Piano) -> tuple[list[Appartamento], str | None]:
    apps = _assegna_etichette(piano, _perimetri(piano))
    _distribuisci(apps, _trova_locali(piano))
    risultato = []
    for a in apps:
        if a.locali or a.poligono:
            _numera_locali(a)
            risultato.append(a)
    risultato.sort(key=lambda a: a.numero)
    titolo, h_best = None, 0.0
    for i, t0 in enumerate(piano.testi):
        t = _trim(t0)
        if _TITOLO_PIANO.fullmatch(t) and len(t) <= 30 and piano.th(i) > h_best:
            titolo, h_best = t, piano.th(i)
    return risultato, titolo


# =====================================================================================================
# PlanLayers.kt
# =====================================================================================================

_LAYER_MURI = {"LINEA SOTTILE", "STRUTTURE", "LINEA MEDIA", "MURI SOPRA"}
_PAROLE_MURI = ("MUR", "WALL", "PARET", "STRUTT")
_PALETTE = ["#FFFFFF", "#FFB74D", "#80DEEA", "#9E9E9E", "#E57373", "#AED581", "#BA68C8", "#FFF176",
            "#4FC3F7", "#F06292", "#A1887F", "#90A4AE"]


def layer_visibili(piano: Piano) -> list[bool]:
    vis = [normalizza(l) in _LAYER_MURI for l in piano.layers]
    if not any(vis):
        vis = [any(k in normalizza(l) for k in _PAROLE_MURI) for l in piano.layers]
    if not any(vis):
        vis = [True] * len(piano.layers)
    return vis


def colori_layer(piano: Piano) -> list[str]:
    fissi = {"LINEA SOTTILE": 0, "STRUTTURE": 1, "LINEA MEDIA": 2, "MURI SOPRA": 3}
    colori, k = [], 0
    for l in piano.layers:
        n = normalizza(l)
        if n in fissi:
            colori.append(_PALETTE[fissi[n]])
        else:
            colori.append(_PALETTE[4 + (k % (len(_PALETTE) - 4))])
            k += 1
    return colori


# =====================================================================================================
# RoomWallExtractor.kt
# =====================================================================================================

CELL = 0.025
MAX_CELLS = 2_000_000
RADII = (0.6, 0.5, 0.45, 0.4, 0.35, 0.3, 0.25, 0.2)
OPENING = 0.3
SEED_SEARCH = 1.0
MAX_ROOM_AREA = 150.0
SIMPLIFY_TOLERANCE = 0.05
MIN_WALL = 0.25
SNAP_ANGLE = 22.0 * (math.pi / 180.0)
AREA_TOLERANCE = 0.1
_BARRIERE_EXTRA = ("FINESTR", "WINDOW")


class Parete:
    def __init__(self, numero, x1, y1, x2, y2, nx, ny):
        self.numero = numero
        self.x1, self.y1, self.x2, self.y2 = _f32(x1), _f32(y1), _f32(x2), _f32(y2)
        self.nx, self.ny = _f32(nx), _f32(ny)

    def lunghezza(self) -> float:
        return _f32(math.hypot(self.x2 - self.x1, self.y2 - self.y1))


def _edt_python(feature: list[bool], w: int, h: int) -> array:
    inf = 1e20
    grid = [0.0 if f else inf for f in feature]
    size = max(w, h)
    f = [0.0] * size
    d = [0.0] * size
    z = [0.0] * (size + 1)
    v = [0] * size

    def edt1d(n):
        k = 0
        v[0] = 0
        z[0] = -1.7976931348623157e308
        z[1] = 1.7976931348623157e308
        for q in range(1, n):
            while True:
                p = v[k]
                s = ((f[q] + q * q) - (f[p] + p * p)) / (2.0 * q - 2.0 * p)
                if s <= z[k] and k > 0:
                    k -= 1
                else:
                    break
            if s <= z[k]:
                v[0] = q
                z[0] = -1.7976931348623157e308
                z[1] = 1.7976931348623157e308
                k = 0
                continue
            k += 1
            v[k] = q
            z[k] = s
            z[k + 1] = 1.7976931348623157e308
        k = 0
        for q in range(n):
            while z[k + 1] < q:
                k += 1
            dq = q - v[k]
            d[q] = dq * dq + f[v[k]]

    for x in range(w):
        for y in range(h):
            f[y] = grid[y * w + x]
        edt1d(h)
        for y in range(h):
            grid[y * w + x] = d[y]
    for y in range(h):
        f[:w] = grid[y * w:y * w + w]
        edt1d(w)
        grid[y * w:y * w + w] = d[:w]
    return array("f", (math.sqrt(g) for g in grid))


def _edt_numpy(feature, w: int, h: int):
    """Stessa trasformata esatta (distanze al quadrato intere, poi radice in float32)."""
    f = _np.asarray(feature, dtype=bool).reshape(h, w)
    inf = 1e20
    # 1) lungo le colonne: distanza dalla cella "feature" più vicina sopra o sotto
    idx = _np.arange(h, dtype=_np.float64)[:, None]
    sopra = _np.where(f, idx, -_np.inf)
    sopra = _np.maximum.accumulate(sopra, axis=0)
    sotto = _np.where(f, idx, _np.inf)
    sotto = _np.minimum.accumulate(sotto[::-1], axis=0)[::-1]
    d1 = _np.minimum((idx - sopra) ** 2, (sotto - idx) ** 2)
    g = _np.where(_np.isfinite(d1), d1, inf)
    # 2) lungo le righe: min_x' ((x - x')² + g[x']), a blocchi di righe
    xs = _np.arange(w, dtype=_np.float64)
    quad = (xs[:, None] - xs[None, :]) ** 2  # [x, x']
    out = _np.empty((h, w), dtype=_np.float64)
    passo = max(1, 4_000_000 // max(1, w * w))
    for r0 in range(0, h, passo):
        blocco = g[r0:r0 + passo]                          # [r, x']
        out[r0:r0 + passo] = (quad[None, :, :] + blocco[:, None, :]).min(axis=2)
    return _np.sqrt(out).astype(_np.float32).ravel().tolist()  # lista: più veloce da leggere a una a una


def _edt(feature, w, h):
    if _np is not None and w * h > 64:
        return _edt_numpy(feature, w, h)
    return _edt_python(list(feature), w, h)


def _area_lista(poly) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        p, q = poly[(i + n - 1) % n], poly[i]
        s += p[0] * q[1] - q[0] * p[1]
    return s / 2


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _semplice(poly) -> bool:
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = poly[j], poly[(j + 1) % n]
            if ((_cross(c, d, a) > 0) != (_cross(c, d, b) > 0)) and ((_cross(a, b, c) > 0) != (_cross(a, b, d) > 0)):
                return False
    return True


def _intersect(a, b):
    den = a[2] * b[3] - a[3] * b[2]
    norme = math.hypot(a[2], a[3]) * math.hypot(b[2], b[3])
    if abs(den) <= 1e-3 * norme:
        return None
    t = ((b[0] - a[0]) * b[3] - (b[1] - a[1]) * b[2]) / den
    return [a[0] + t * a[2], a[1] + t * a[3]]


def _linee_poligono(linee):
    poly = []
    n = len(linee)
    for i in range(n):
        prev, cur = linee[(i + n - 1) % n], linee[i]
        p = _intersect(prev, cur)
        if p is None:
            p = [(prev[0] + prev[2] + cur[0]) / 2, (prev[1] + prev[3] + cur[1]) / 2]
        poly.append(p)
    return poly


def _togli_lato(linee, i, lunghezza):
    n = len(linee)
    if n <= 4:
        return None
    prev, seg = (i + n - 1) % n, (i + 1) % n
    a, b, lato = linee[prev], linee[seg], linee[i]
    mid = [lato[0] + lato[2] / 2, lato[1] + lato[3] / 2]
    res = list(linee)
    p = _intersect(a, b)
    if p is not None:
        if math.hypot(p[0] - mid[0], p[1] - mid[1]) > lunghezza + 0.5:
            return None
        del res[i]
        return _linee_poligono(res)
    if abs(a[2] * b[2] + a[3] * b[3]) <= 0:
        return None
    la, lb = math.hypot(a[2], a[3]), math.hypot(b[2], b[3])
    ma = 1.0 / (la + lb)
    unita = [((a[0] + a[2] / 2) * la + (b[0] + b[2] / 2) * lb) * ma,
             ((a[1] + a[3] / 2) * la + (b[1] + b[3] / 2) * lb) * ma,
             (a[2] / la if la >= lb else b[2] / lb) * (la + lb),
             (a[3] / la if la >= lb else b[3] / lb) * (la + lb)]
    unita[0] -= unita[2] / 2
    unita[1] -= unita[3] / 2
    if a[2] * b[2] + a[3] * b[3] < 0:
        return None
    togli = sorted((prev, i, seg))
    for r in (2, 1, 0):
        del res[togli[r]]
    res.insert(min(togli[0], len(res)), unita)
    return _linee_poligono(res) if len(res) >= 3 else None


def _collineare(poly) -> int:
    n = len(poly)
    for i in range(n):
        a, b, c = poly[(i + n - 1) % n], poly[i], poly[(i + 1) % n]
        ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], c[0] - b[0], c[1] - b[1]
        lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
        if lu < 1e-6 or lv < 1e-6:
            return i
        if abs(ux * vy - uy * vx) / (lu * lv) < 0.05 and ux * vx + uy * vy > 0:
            return i
    return -1


def _senza_degeneri(poly):
    res = []
    for i, p in enumerate(poly):
        prev = poly[(i + len(poly) - 1) % len(poly)]
        if math.hypot(p[0] - prev[0], p[1] - prev[1]) > 1e-6:
            res.append(p)
    return res


def _sposta(poly, quanto):
    segno = 1.0 if _area_lista(poly) > 0 else -1.0
    linee = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        l = [p[0], p[1], q[0] - p[0], q[1] - p[1]]
        lung = math.hypot(l[2], l[3])
        nx, ny = l[3] / lung * segno, -l[2] / lung * segno
        l[0] += nx * quanto
        l[1] += ny * quanto
        linee.append(l)
    return _linee_poligono(linee)


def _semplifica(punti, da, a, tol, out):
    pa, pb = punti[da], punti[a]
    abx, aby = pb[0] - pa[0], pb[1] - pa[1]
    lung = math.hypot(abx, aby)
    indice, dmax = -1, tol
    for i in range(da + 1, a):
        p = punti[i]
        d = (abs(abx * (p[1] - pa[1]) - aby * (p[0] - pa[0])) / lung) if lung > 0 else math.hypot(p[0] - pa[0], p[1] - pa[1])
        if d > dmax:
            dmax, indice = d, i
    if indice < 0:
        out.append(pa)
        return
    _semplifica(punti, da, indice, tol, out)
    _semplifica(punti, indice, a, tol, out)


def _douglas_peucker(chiuso, tol):
    if len(chiuso) < 4:
        return chiuso
    lontano, dl = 0, -1.0
    primo = chiuso[0]
    for i in range(1, len(chiuso)):
        d = math.hypot(chiuso[i][0] - primo[0], chiuso[i][1] - primo[1])
        if d > dl:
            dl, lontano = d, i
    res = []
    _semplifica(chiuso, 0, lontano, tol, res)
    dietro = list(chiuso[lontano:]) + [primo]
    secondo = []
    _semplifica(dietro, 0, len(dietro) - 1, tol, secondo)
    return res + secondo


def _in_pareti(contorno) -> list[Parete]:
    poly = list(contorno)
    if _area_lista(poly) > 0:
        poly.reverse()
    n = len(poly)
    start, top_best, left_best = 0, -1.7976931348623157e308, 1.7976931348623157e308
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        top, left = (p[1] + q[1]) / 2, (p[0] + q[0]) / 2
        if top > top_best + 0.05 or (abs(top - top_best) <= 0.05 and left < left_best):
            top_best, left_best, start = top, left, i
    pareti = []
    for k in range(n):
        p, q = poly[(start + k) % n], poly[(start + k + 1) % n]
        lung = math.hypot(q[0] - p[0], q[1] - p[1])
        pareti.append(Parete(len(pareti) + 1, p[0], p[1], q[0], q[1], (q[1] - p[1]) / lung, -(q[0] - p[0]) / lung))
    return pareti


class _Estrattore:
    def __init__(self, w, h, ox, oy, cell, muri, theta):
        self.w, self.h, self.ox, self.oy, self.cell, self.theta = w, h, ox, oy, cell, theta
        self.dist = _edt(muri, w, h)

    def contorno_locale(self, loc: Locale, tutti):
        best, best_score = None, 1.7976931348623157e308
        for r in RADII:
            c = self._contorno_raggio(loc, tutti, r)
            if c is None or not _semplice(c):
                continue
            area = abs(_area_lista(c))
            err = abs(area - loc.area) / loc.area if loc.area > 0 else 0.0
            score = max(0.0, err - AREA_TOLERANCE / 2) + 0.5 * self._frazione_diagonale(c)
            if score < best_score - 1e-6:
                best_score, best = score, c
        return best

    def _frazione_diagonale(self, poly):
        tot = diag = 0.0
        n = len(poly)
        for i in range(n):
            p, q = poly[i], poly[(i + 1) % n]
            l = math.hypot(q[0] - p[0], q[1] - p[1])
            ang = math.atan2(q[1] - p[1], q[0] - p[0]) - self.theta
            dev = abs(ang - _round_java(ang / (math.pi / 2)) * math.pi / 2)
            tot += l
            if dev > 2.0 * (math.pi / 180.0):
                diag += l
        return diag / tot if tot > 0 else 1.0

    def _cella_vicina(self, x, y, soglia):
        w, h, cell, dist = self.w, self.h, self.cell, self.dist
        cx = int(math.floor((x - self.ox) / cell))
        cy = int(math.floor((y - self.oy) / cell))
        raggio = int(math.ceil(SEED_SEARCH / cell))
        best, bd = -1, None
        for y0 in range(max(1, cy - raggio), min(h - 2, cy + raggio) + 1):
            riga = y0 * w
            for x0 in range(max(1, cx - raggio), min(w - 2, cx + raggio) + 1):
                if dist[riga + x0] <= soglia:
                    continue
                d = (x0 - cx) * (x0 - cx) + (y0 - cy) * (y0 - cy)
                if bd is None or d < bd:
                    bd, best = d, riga + x0
        return best

    def _riempi(self, seme, soglia, segno, coda, max_celle):
        w, h, dist = self.w, self.h, self.dist
        testa, fine = 0, 1
        coda[0] = seme
        segno[seme] = 1
        while testa < fine:
            c = coda[testa]
            testa += 1
            x, y = c % w, c // w
            if x == 0 or y == 0 or x == w - 1 or y == h - 1:
                return -1
            if fine > max_celle:
                return -1
            for n in (c + 1, c - 1, c + w, c - w):
                if segno[n] == 0 and dist[n] > soglia:
                    segno[n] = 1
                    coda[fine] = n
                    fine += 1
        return fine

    def _dividi(self, segno, celle, size, semi):
        w = self.w
        co, si = math.cos(self.theta), math.sin(self.theta)
        s0 = semi[0]
        ax, ay = float(s0 % w), float(s0 // w)
        assi = []
        for s in semi[1:]:
            bx, by = float(s % w), float(s // w)
            du = (bx - ax) * co + (by - ay) * si
            dv = -(bx - ax) * si + (by - ay) * co
            sg = lambda v: (v > 0) - (v < 0)
            if abs(du) >= abs(dv):
                assi.append((co * sg(du), si * sg(du), abs(du) / 2))
            else:
                assi.append((-si * sg(dv), co * sg(dv), abs(dv) / 2))
        for i in range(size):
            c = celle[i]
            x, y = c % w - ax, c // w - ay
            for axx, axy, lim in assi:
                if x * axx + y * axy > lim:
                    segno[c] = 2
                    break

    def _contorno_raggio(self, loc, tutti, raggio):
        w, h, cell = self.w, self.h, self.cell
        soglia = _f32(raggio / cell)
        seme = self._cella_vicina(loc.x, loc.y, soglia)
        if seme < 0:
            return None
        max_celle = int(MAX_ROOM_AREA / (cell * cell))
        comp = bytearray(w * h)
        coda = array("i", bytes(4 * w * h))
        size = self._riempi(seme, soglia, comp, coda, max_celle)
        if size < 0:
            return None
        semi = [seme]
        for o in tutti:
            if o is loc:
                continue
            s = self._cella_vicina(o.x, o.y, soglia)
            if s >= 0 and comp[s] == 1 and s != seme:
                semi.append(s)
        if len(semi) > 1:
            self._dividi(comp, coda, size, semi)
        min_x, max_x, min_y, max_y = w, -1, h, -1
        for i in range(size):
            c = coda[i]
            if comp[c] != 1:
                continue
            x, y = c % w, c // w
            if x < min_x: min_x = x
            if x > max_x: max_x = x
            if y < min_y: min_y = y
            if y > max_y: max_y = y
        if max_x < 0:
            return None
        pad = int(math.ceil(OPENING / cell)) + 2
        ox, oy = min_x - pad, min_y - pad
        lw, lh = max_x - min_x + 1 + 2 * pad, max_y - min_y + 1 + 2 * pad
        core = bytearray(lw * lh)
        for i in range(size):
            c = coda[i]
            if comp[c] == 1:
                core[(c // w - oy) * lw + (c % w - ox)] = 1
        core = _apri(core, lw, lh, _f32(OPENING / cell))
        ls = (seme // w - oy) * lw + (seme % w - ox)
        if not core[ls]:
            ls = _vero_vicino(core, lw, lh, ls)
            if ls < 0:
                return None
        core = _componente(core, lw, lh, ls)
        contorno = _traccia(core, lw, lh, self.ox + ox * cell, self.oy + oy * cell, cell)
        contorno = _douglas_peucker(contorno, SIMPLIFY_TOLERANCE)
        if len(contorno) < 3:
            return None
        contorno = self._ortogonalizza(contorno)
        if len(contorno) < 3:
            return None
        contorno = _sposta(contorno, raggio + cell / 2)
        contorno = self._ortogonalizza(contorno)
        return contorno if len(contorno) >= 3 else None

    def _ortogonalizza(self, poly):
        theta = self.theta
        for _ in range(200):
            poly = _senza_degeneri(poly)
            n = len(poly)
            if n < 4:
                return poly
            linee, agganciato = [], [False] * n
            for i in range(n):
                p, q = poly[i], poly[(i + 1) % n]
                l = math.hypot(q[0] - p[0], q[1] - p[1])
                ang = math.atan2(q[1] - p[1], q[0] - p[0]) - theta
                k = _round_java(ang / (math.pi / 2))
                ux, uy = q[0] - p[0], q[1] - p[1]
                if abs(ang - k * math.pi / 2) < SNAP_ANGLE:
                    a = theta + k * math.pi / 2
                    ux, uy = math.cos(a) * l, math.sin(a) * l
                    agganciato[i] = True
                mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
                linee.append([mx - ux / 2, my - uy / 2, ux, uy])
            agg = _linee_poligono(linee)
            lung = [math.hypot(agg[(i + 1) % n][0] - agg[i][0], agg[(i + 1) % n][1] - agg[i][1]) for i in range(n)]
            candidati = [i for i in range(n) if lung[i] < MIN_WALL or (not agganciato[i] and lung[i] < 1.5)]
            candidati.sort(key=lambda i: lung[i])
            prossimo = None
            for i in candidati:
                prossimo = _togli_lato(linee, i, lung[i])
                if prossimo is not None:
                    break
            if prossimo is not None:
                poly = prossimo
                continue
            c = _collineare(agg)
            if c < 0:
                return agg
            del agg[c]
            poly = agg
        return poly


def _apri(region, w, h, raggio):
    """Apertura morfologica: toglie le sporgenze più sottili di 2 * raggio (nicchie delle finestre)."""
    fuori = [not v for v in region]
    dentro = _edt(fuori, w, h)
    eroso = [dentro[i] > raggio for i in range(len(region))]
    if not any(eroso):
        return region
    verso = _edt(eroso, w, h)
    limite = _f32(raggio + 1)
    return bytearray(1 if (region[i] and verso[i] <= limite) else 0 for i in range(len(region)))


def _vero_vicino(region, w, h, da):
    fx, fy = da % w, da // w
    best, bd = -1, None
    for i, v in enumerate(region):
        if not v:
            continue
        dx, dy = i % w - fx, i // w - fy
        d = dx * dx + dy * dy
        if bd is None or d < bd:
            bd, best = d, i
    return best


def _componente(region, w, h, seme):
    res = bytearray(len(region))
    coda = [seme]
    res[seme] = 1
    testa = 0
    while testa < len(coda):
        c = coda[testa]
        testa += 1
        x, y = c % w, c // w
        if x > 0 and region[c - 1] and not res[c - 1]:
            res[c - 1] = 1; coda.append(c - 1)
        if x < w - 1 and region[c + 1] and not res[c + 1]:
            res[c + 1] = 1; coda.append(c + 1)
        if y > 0 and region[c - w] and not res[c - w]:
            res[c - w] = 1; coda.append(c - w)
        if y < h - 1 and region[c + w] and not res[c + w]:
            res[c + w] = 1; coda.append(c + w)
    return res


def _cella(region, w, h, cx, cy, hx, hy):
    x = cx if hx > 0 else cx - 1
    y = cy if hy > 0 else cy - 1
    return 0 <= x < w and 0 <= y < h and bool(region[y * w + x])


def _traccia(region, w, h, x0, y0, cell):
    start = next((i for i, v in enumerate(region) if v), -1)
    punti = []
    if start < 0:
        return punti
    sx, sy = start % w, start // w
    cx, cy, dx, dy = sx, sy, 1, 0
    limite = 4 * len(region)
    while True:
        punti.append([x0 + cx * cell, y0 + cy * cell])
        cx += dx
        cy += dy
        lx, ly = -dy, dx
        avanti = _cella(region, w, h, cx, cy, dx + lx, dy + ly)
        destra = _cella(region, w, h, cx, cy, dx - lx, dy - ly)
        if destra:
            dx, dy = -lx, -ly
        elif not avanti:
            dx, dy = lx, ly
        if not ((cx != sx or cy != sy or dx != 1 or dy != 0) and len(punti) < limite):
            break
    return punti


def _barriere(piano: Piano, muri: list[bool]) -> list[bool]:
    res = []
    for l, nome in enumerate(piano.layers):
        if l < len(muri) and muri[l]:
            res.append(True)
            continue
        n = normalizza(nome)
        res.append(any(k in n for k in _BARRIERE_EXTRA))
    return res


def pareti_appartamento(piano: Piano, muri: list[bool], app: Appartamento) -> dict[int, list[Parete]]:
    """RoomWallExtractor.extract: {indice del locale in app.locali: pareti}; i locali senza contorno chiuso mancano."""
    risultato: dict[int, list[Parete]] = {}
    if not app.locali:
        return risultato
    barriera = _barriere(piano, muri)
    if len(app.poligono) >= 6:
        xs, ys = app.poligono[0::2], app.poligono[1::2]
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        margine = 1.0
    else:
        min_x = min(l.x for l in app.locali); max_x = max(l.x for l in app.locali)
        min_y = min(l.y for l in app.locali); max_y = max(l.y for l in app.locali)
        margine = 6.0
    min_x -= margine; min_y -= margine; max_x += margine; max_y += margine
    cell = CELL
    while (max_x - min_x) / cell * (max_y - min_y) / cell > MAX_CELLS:
        cell *= 1.5
    w = int(math.ceil((max_x - min_x) / cell)) + 1
    h = int(math.ceil((max_y - min_y) / cell)) + 1
    griglia = bytearray(w * h)

    def linea(x1, y1, x2, y2):
        ax, ay = (x1 - min_x) / cell, (y1 - min_y) / cell
        bx, by = (x2 - min_x) / cell, (y2 - min_y) / cell
        steps = int(math.ceil(max(abs(bx - ax), abs(by - ay)) * 2)) + 1
        for s in range(steps + 1):
            t = s / steps
            x = int(math.floor(ax + (bx - ax) * t))
            y = int(math.floor(ay + (by - ay) * t))
            if 0 <= x < w and 0 <= y < h:
                griglia[y * w + x] = 1

    istogramma = [0.0] * 90
    seg = piano.seg
    for i in range(piano.n_segmenti()):
        layer = piano.seg_layer[i]
        if layer < 0 or layer >= len(barriera) or not barriera[layer]:
            continue
        x1, y1, x2, y2 = seg[i * 4], seg[i * 4 + 1], seg[i * 4 + 2], seg[i * 4 + 3]
        if max(x1, x2) < min_x or min(x1, x2) > max_x:
            continue
        if max(y1, y2) < min_y or min(y1, y2) > max_y:
            continue
        linea(x1, y1, x2, y2)
        lung = math.hypot(x2 - x1, y2 - y1)
        if lung > 0.1:
            ang = math.atan2(y2 - y1, x2 - x1) * (180.0 / math.pi)
            b = int(math.floor(math.fmod(math.fmod(ang, 90) + 90, 90) + 0.5)) % 90
            istogramma[b] += lung
    p = app.poligono
    for i in range(0, len(p) - 1, 2):
        j = (i + 2) % len(p)
        linea(p[i], p[i + 1], p[j], p[j + 1])
    best_bin = 0
    for b in range(1, 90):
        if istogramma[b] > istogramma[best_bin]:
            best_bin = b
    estr = _Estrattore(w, h, min_x, min_y, cell, griglia, best_bin * (math.pi / 180.0))
    for k, loc in enumerate(app.locali):
        c = estr.contorno_locale(loc, app.locali)
        if c is not None:
            risultato[k] = _in_pareti(c)
    return risultato

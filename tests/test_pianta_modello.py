"""La lettura della planimetria dà gli stessi risultati dell'app (shared/FloorPlanTest.kt, TestPlans.kt)."""
import math

import pytest

from app import pianta_modello as pm


def piano_due_locali() -> pm.Piano:
    """TestPlans.twoRoomApartment: APP 3, 10 × 8 m, muro a x = 5, cucina con «h 2,80» e camera."""
    p = pm.Piano()
    per, muri, testi = p.layer("SUP COMMERCIALI"), p.layer("MURI"), p.layer("TESTI")

    def seg(x1, y1, x2, y2, l):
        p.seg.extend((x1, y1, x2, y2)); p.seg_layer.append(l)

    for l in (per, muri):
        seg(0, 0, 10, 0, l); seg(10, 0, 10, 8, l); seg(10, 8, 0, 8, l); seg(0, 8, 0, 0, l)
    seg(5, 0, 5, 8, muri)

    def testo(v, x, y, h):
        p.testi.append(v); p.tdati.extend((x, y, h)); p.t_layer.append(testi)

    testo("App. 3", 1, 7.5, 0.3)
    testo("PIANO PRIMO", 5, 9, 0.5)
    testo("CUCINA", 2.5, 4.5, 0.3)
    testo("Sup. 40,00 mq", 2.5, 4.0, 0.2)
    testo("h 2,80", 2.5, 3.6, 0.2)
    testo("CAMERA", 7.5, 4.5, 0.3)
    testo("Sup. 40,00 mq", 7.5, 4.0, 0.2)
    return p


def test_analisi_come_app():
    apps, titolo = pm.analizza(piano_due_locali())
    assert titolo == "PIANO PRIMO"
    assert len(apps) == 1
    a = apps[0]
    assert a.numero == 3 and a.etichetta == "App. 3" and a.nome == "APP 3"
    assert abs(pm.area_con_segno(a.poligono)) == 80
    assert [l.nome for l in a.locali] == ["Cucina", "Camera"]
    assert a.locali[0].altezza == pm._f32(2.8) and math.isnan(a.locali[1].altezza)
    assert a.locali[0].area == 40


def test_tipi_locale():
    assert pm.tipo_locale("LETTO") == "Camera"
    assert pm.tipo_locale("WC") == "Bagno"
    assert pm.tipo_locale("DISIMP.") == "Disimpegno"
    assert pm.tipo_locale("cantina") == "Cantina"
    assert pm.tipo_locale("TERRAZZO") == "Balcone"


@pytest.mark.parametrize("numpy", [True, False])
def test_pareti_come_app(numpy, monkeypatch):
    if not numpy:
        monkeypatch.setattr(pm, "_np", None)
    piano = piano_due_locali()
    a = pm.analizza(piano)[0][0]
    pareti = pm.pareti_appartamento(piano, [False, True, False], a)
    assert len(pareti) == 2
    for k, loc in enumerate(a.locali):
        pp = pareti[k]
        assert [x.numero for x in pp] == [1, 2, 3, 4], loc.nome
        lung = sorted(x.lunghezza() for x in pp)
        assert abs(lung[0] - 5) < 0.1 and abs(lung[1] - 5) < 0.1, lung
        assert abs(lung[2] - 8) < 0.1 and abs(lung[3] - 8) < 0.1, lung
        assert abs((pp[0].y1 + pp[0].y2) / 2 - 8) < 0.1  # Parete 1 in alto (numerate in senso orario)

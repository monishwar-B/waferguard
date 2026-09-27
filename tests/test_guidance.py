from waferguard.knowledge import GUIDE, guidance
from waferguard.taxonomy import WM811K_CLASSES

from .conftest import png_bytes


def test_every_class_has_guidance():
    for c in WM811K_CLASSES:
        g = GUIDE[c]
        assert g["summary"] and g["mechanism"]
        if c != "none":
            assert len(g["causes"]) >= 3 and g["investigate"] and g["prevent"]
            assert all({"cause", "area", "check"} <= set(x) for x in g["causes"])
    for c in ("scratch", "particle", "void", "pattern", "bridge", "open", "short"):
        assert GUIDE[c]["causes"]


def test_guidance_severity_review_and_recurrence():
    g = guidance("Center", "Critical")
    assert g["actions"]["immediate"][0].startswith("Put the lot on hold")
    g = guidance("Center", "Minor", needs_review=True)
    assert "confirm the classification" in g["actions"]["immediate"][0]
    rec = {"equipment": {"id": "CMP-02", "total": 20, "same": 5}, "lot": {"id": "L1", "total": 8, "same": 3}}
    g = guidance("Center", "Major", recurrence=rec)
    assert g["actions"]["immediate"][0] == "Check tool CMP-02: this pattern is repeating on it."
    g2 = guidance("Center", "Major", needs_review=True, recurrence=rec)
    assert "confirm the classification" in g2["actions"]["immediate"][0] and "CMP-02" in g2["actions"]["immediate"][1]
    assert len(g["recurrence_notes"]) == 2 and "5 of the last 20" in g["recurrence_notes"][0]
    assert guidance("Center", "Minor", recurrence=rec)["actions"]["immediate"][0].startswith("Log it")
    unknown = guidance("mystery", "Major")
    assert unknown["causes"] == [] and "No guidance" in unknown["mechanism"]


def test_api_returns_guidance_and_recurrence(client, admin):
    ids = []
    for seed in range(3):
        r = client.post("/api/v1/inspections", headers=admin, files={"file": ("n.png", png_bytes("Near-full", seed))},
                        data={"lot_id": "LOTG", "equipment_id": "ETCH-9"}).json()
        ids.append(r["id"])
    assert r["guidance"]["label"] == r["label"] and r["guidance"]["actions"]["immediate"]
    if r["label"] != "none":
        assert r["recurrence"]["equipment"]["id"] == "ETCH-9" and r["recurrence"]["equipment"]["total"] == 3
    first = client.get(f"/api/v1/inspections/{ids[0]}", headers=admin).json()
    assert first["guidance"]["causes"] is not None and first["recurrence"]["label"] == first["label"]
    if first["label"] != "none":
        assert first["recurrence"]["equipment"]["total"] == 1  # only wafers up to its own time
    reviewed = client.post(f"/api/v1/inspections/{ids[0]}/review", json={"label": "Scratch"}, headers=admin).json()
    assert reviewed["guidance"]["label"] == "Scratch"
    pdf = client.get(f"/api/v1/export/inspections/{ids[0]}/report.pdf", headers=admin)
    assert pdf.content[:4] == b"%PDF" and len(pdf.content) > 3000
    clean = client.post("/api/v1/inspections", headers=admin, files={"file": ("c.png", png_bytes("none", 1))}).json()
    if clean["label"] == "none":
        assert clean["recurrence"]["equipment"] is None and clean["guidance"]["causes"] == []

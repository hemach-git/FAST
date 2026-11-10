import pytest

from app.services import retriever, router


@pytest.mark.parametrize("q,primary", [
    ("CNC-04 spindle overheating alarm 2041", "maintenance"),
    ("What is the torque spec for M10 bolts?", "quality"),
    ("What PPE do I need in the paint booth?", "safety"),
    ("Weld porosity acceptance criteria", "quality"),
    ("Plant air pressure dropping on compressor AC-2", "maintenance"),
])
def test_primary_route(q, primary):
    d, _ = router.route(q)
    assert d.primary == primary


def test_hazard_forces_safety():
    d, _ = router.route("How do I clear a jam on Conveyor C3?")
    assert d.safety_flag and "safety" in d.categories and d.primary == "maintenance"


def test_low_confidence_broad_search():
    d, _ = router.route("what is for lunch")
    assert d.broad_search and set(d.categories) == {"safety", "maintenance", "quality"}


def test_retrieval_only_published():
    hits = retriever.search("M10 bolt torque specification", ["quality"], top_k=10)
    assert hits and all(h["doc_id"] != "QC-TORQ-01-v2" for h in hits)


def test_retrieval_category_scoped():
    hits = retriever.search("spindle alarm", ["quality"], top_k=5)
    assert all(h["category"] == "quality" for h in hits)

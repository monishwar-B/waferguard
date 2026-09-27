import asyncio
import json
import threading

import fakeredis
import numpy as np
import pytest

from waferguard.api.db import Database, Inspection, Job
from waferguard.api.services import spc
from waferguard.api.services.alerts import AlertEngine, Notifier
from waferguard.api.services.events import EventBus
from waferguard.api.services.jobs import QUEUE_KEY, RedisQueue, consume_forever
from waferguard.api.settings import NotifierSettings, Settings


def test_p_chart_limits_and_rules():
    groups = [(f"L{i}", 5, 100) for i in range(10)] + [("spike", 30, 100)]
    out = spc.p_chart(groups)
    assert out["center"] == pytest.approx(80 / 1100)
    last = out["points"][-1]
    assert last["out_of_control"] and 1 in last["rules"]
    assert spc.p_chart([])["points"] == [] and spc.p_chart([("x", 0, 0)])["center"] is None


def test_capability_known_values():
    x = [0.05, 0.06, 0.05, 0.07, 0.06, 0.05, 0.06, 0.07]
    cap = spc.capability(x, usl=0.15)
    mr = np.abs(np.diff(x)).mean() / 1.128
    assert cap["cpk"] == pytest.approx((0.15 - np.mean(x)) / (3 * mr))
    assert cap["ppk"] == pytest.approx((0.15 - np.mean(x)) / (3 * np.std(x, ddof=1)))
    assert cap["rating"] == "excellent"
    assert spc.capability([0.1], 0.15)["cpk"] is None
    assert spc.capability([0.1, 0.1], 0.15)["cpk"] is None
    two_sided = spc.capability([0.4, 0.6, 0.5, 0.55], usl=1.0, lsl=0.0)
    assert two_sided["cpk"] > 0
    assert spc._rating(1.4) == "capable" and spc._rating(1.1) == "marginal" and spc._rating(0.5) == "not capable"


def test_imr_and_western_electric():
    vals = [0.1] * 8 + [0.12] * 8
    imr = spc.imr_chart(vals)
    assert imr["center"] == pytest.approx(0.11) and len(imr["points"]) == 16
    assert spc.imr_chart([0.2])["points"] == []
    rules = {v["rule"] for v in spc.western_electric([1, 1, 1, 1, 1, 1, 1, 1], 0, [0.3] * 8)}
    assert {1, 2, 3, 4} <= rules
    assert spc.western_electric([0, 0], 0, [0, 0]) == []


def test_pareto_and_trend():
    p = spc.pareto(["none", "Center", "Center", "Scratch"])
    assert p[0] == {"label": "Center", "count": 2, "share": 2 / 3, "cumulative": 2 / 3}
    t = spc.trend([("d1", "none"), ("d1", "Center"), ("d2", "none")])
    assert t[0]["defect_rate"] == 0.5 and t[1]["defect_rate"] == 0.0


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def plan(self, level):
        return ["fake"]

    def send(self, alert):
        self.sent.append(alert)
        return {"fake": "sent"}


class Bus:
    def __init__(self):
        self.events = []

    def publish(self, t, d):
        self.events.append((t, d))


def _insp(db, label, eq="EQ1", severity="Minor"):
    with db.Session() as s:
        i = Inspection(operator="t", equipment_id=eq, label=label, confidence=0.9, severity=severity, fail_ratio=0.4)
        s.add(i)
        s.commit()
        return i


def test_alert_rules(tmp_path):
    st = Settings(database_url=f"sqlite:///{tmp_path}/a.db",
                  alerts={"window": 10, "min_samples": 5, "spike_rate": 0.5, "consecutive_defects": 3, "cooldown_minutes": 0})
    db = Database(st.database_url)
    db.create_all()
    bus, fake = Bus(), FakeNotifier()
    eng = AlertEngine(db, st, bus, fake)
    for _ in range(2):
        assert eng.evaluate(_insp(db, "none")) == []
    raised = []
    for _ in range(4):
        raised += eng.evaluate(_insp(db, "Center"))
    rules = {a["rule"] for a in raised}
    assert {"consecutive_defects", "defect_rate_spike"} <= rules
    crit = eng.evaluate(_insp(db, "Near-full", severity="Critical"))
    assert any(a["rule"] == "critical_defect" and a["level"] == "critical" for a in crit)
    assert any(t == "alert.raised" for t, _ in bus.events)
    st.alerts.cooldown_minutes = 60
    eng.evaluate(_insp(db, "Center", eq="EQ2"))
    eng.evaluate(_insp(db, "Center", eq="EQ2"))
    first = eng.evaluate(_insp(db, "Center", eq="EQ2"))
    second = eng.evaluate(_insp(db, "Center", eq="EQ2"))
    assert any(a["rule"] == "consecutive_defects" for a in first) and not any(a["rule"] == "consecutive_defects" for a in second)


def test_notifier_channels(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr("httpx.post", lambda url, **kw: calls.append((url, kw)) or Resp())

    class SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def starttls(self):
            calls.append(("tls", {}))

        def login(self, u, p):
            calls.append(("login", {}))

        def send_message(self, m):
            calls.append(("smtp", {"subject": m["Subject"]}))

    monkeypatch.setattr("smtplib.SMTP", SMTP)
    cfg = NotifierSettings(email_enabled=True, email_to=["qa@fab"], smtp_user="u", teams_webhook_url="https://teams/x",
                           sms_enabled=True, sms_to=["+100"], twilio_account_sid="AC1", twilio_from="+1",
                           webhook_url="https://mes/hook")
    n = Notifier(cfg)
    assert n.plan("info") == [] and n.plan("critical") == ["email", "teams", "sms", "webhook"]
    status = n.send({"level": "critical", "rule": "r", "equipment_id": "E", "message": "m"})
    assert status == {"email": "sent", "teams": "sent", "sms": "sent", "webhook": "sent"}
    assert any("twilio" in c[0] for c in calls if isinstance(c[0], str))

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("httpx.post", boom)
    status = Notifier(NotifierSettings(teams_webhook_url="https://t")).send(
        {"level": "warning", "rule": "r", "equipment_id": None, "message": "m"})
    assert status["teams"].startswith("error")


def test_event_bus_local_and_redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe("wg:events")
    bus = EventBus(r)

    async def run():
        bus._loop = asyncio.get_running_loop()  # bind without starting the listener thread
        q = bus.subscribe(maxsize=2)
        for i in range(3):
            bus.publish("x", {"i": i})
        await asyncio.sleep(0.05)
        got = [q.get_nowait() for _ in range(q.qsize())]
        bus.unsubscribe(q)
        return got

    got = asyncio.run(run())
    assert [g["data"]["i"] for g in got] == [1, 2]  # oldest dropped for slow client
    msg = None
    for _ in range(20):
        msg = ps.get_message(timeout=0.1)
        if msg:
            break
    assert msg and json.loads(msg["data"])["type"] == "x"
    assert bus.subscriber_count == 0
    bus.close()


def test_redis_queue_and_worker(tmp_path, settings):
    from PIL import Image

    from waferguard.api.state import AppState
    from waferguard.data import synthetic
    r = fakeredis.FakeRedis(decode_responses=True)
    state = AppState(settings, redis_client=r)
    assert isinstance(state.queue, RedisQueue)
    with state.db.Session() as s:
        j = Job(created_by="t", total=2)
        s.add(j)
        s.commit()
        jid = j.id
    items = []
    for i, lab in enumerate(["Center", "none"]):
        p = tmp_path / f"{lab}.png"
        Image.fromarray(synthetic.to_rgb(synthetic.make(lab, 40, np.random.default_rng(i)))).save(p)
        items.append({"path": str(p), "filename": p.name})
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    items.append({"path": str(bad), "filename": "bad.png"})
    state.queue.submit(jid, items, {"operator": "t", "lot_id": "L1"})
    assert state.queue.depth() == 1
    stop = threading.Event()
    t = threading.Thread(target=consume_forever, args=(state, r, stop, 1))
    t.start()
    for _ in range(100):
        with state.db.Session() as s:
            if s.get(Job, jid).status in ("done", "failed"):
                break
        stop.wait(0.1)
    stop.set()
    t.join(5)
    with state.db.Session() as s:
        j = s.get(Job, jid)
        assert j.status == "done" and j.done == 2 and j.failed == 1 and "bad.png" in j.error
    assert r.llen(QUEUE_KEY) == 0
    r.rpush(QUEUE_KEY, json.dumps({"job_id": "missing", "items": [], "meta": {}}))
    stop2 = threading.Event()
    t2 = threading.Thread(target=consume_forever, args=(state, r, stop2, 1))
    t2.start()
    stop2.wait(0.5)
    stop2.set()
    t2.join(5)  # crashed job is logged, loop survives
    state.shutdown()

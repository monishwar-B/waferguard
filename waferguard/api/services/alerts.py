"""Automated alerting.

Rules (evaluated after every inspection, per equipment):
  * ``defect_rate_spike``   rolling defect rate over the last ``window`` wafers exceeds
                            ``spike_rate`` or ``spike_factor`` x the long-run baseline
  * ``consecutive_defects`` N defective wafers in a row
  * ``critical_defect``     any Critical-severity wafer
A cooldown per (rule, equipment) prevents alert storms.

Channels: always the database + WebSocket; optionally email (SMTP), Microsoft
Teams (incoming webhook), SMS (Twilio REST API) and a generic JSON webhook.
Sending happens on a background thread so the inspection path never blocks.
"""
from __future__ import annotations

import datetime as dt
import logging
import smtplib
import threading
from email.message import EmailMessage

import httpx
from sqlalchemy import desc, select

from waferguard.api.db import Alert, Inspection, utcnow

log = logging.getLogger("waferguard.alerts")
LEVELS = ["info", "warning", "critical"]


class Notifier:
    def __init__(self, cfg):
        self.cfg = cfg

    def _email(self, subject: str, body: str) -> str:
        c = self.cfg
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, c.email_from, ", ".join(c.email_to)
        msg.set_content(body)
        with smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=10) as s:
            if c.smtp_tls:
                s.starttls()
            if c.smtp_user:
                s.login(c.smtp_user, c.smtp_password or "")
            s.send_message(msg)
        return "sent"

    def _teams(self, subject: str, body: str, level: str) -> str:
        color = {"critical": "E63946", "warning": "F4A261"}.get(level, "2A9D8F")
        card = {"@type": "MessageCard", "@context": "https://schema.org/extensions", "themeColor": color,
                "summary": subject, "title": subject, "text": body.replace("\n", "<br>")}
        r = httpx.post(self.cfg.teams_webhook_url, json=card, timeout=10)
        r.raise_for_status()
        return "sent"

    def _sms(self, text: str) -> str:
        c = self.cfg
        url = f"https://api.twilio.com/2010-04-01/Accounts/{c.twilio_account_sid}/Messages.json"
        for to in c.sms_to:
            r = httpx.post(url, data={"From": c.twilio_from, "To": to, "Body": text[:1500]},
                           auth=(c.twilio_account_sid, c.twilio_auth_token or ""), timeout=10)
            r.raise_for_status()
        return "sent"

    def _webhook(self, payload: dict) -> str:
        r = httpx.post(self.cfg.webhook_url, json=payload, timeout=10)
        r.raise_for_status()
        return "sent"

    def plan(self, level: str) -> list[str]:
        c = self.cfg
        if LEVELS.index(level) < LEVELS.index(c.min_level):
            return []
        ch = []
        if c.email_enabled and c.email_to:
            ch.append("email")
        if c.teams_webhook_url:
            ch.append("teams")
        if c.sms_enabled and c.sms_to and c.twilio_account_sid:
            ch.append("sms")
        if c.webhook_url:
            ch.append("webhook")
        return ch

    def send(self, alert: dict) -> dict:
        subject = f"[WaferGuard {alert['level'].upper()}] {alert['rule']} on {alert.get('equipment_id') or 'line'}"
        body = alert["message"]
        status = {}
        for ch in self.plan(alert["level"]):
            try:
                if ch == "email":
                    status[ch] = self._email(subject, body)
                elif ch == "teams":
                    status[ch] = self._teams(subject, body, alert["level"])
                elif ch == "sms":
                    status[ch] = self._sms(f"{subject}: {body}")
                elif ch == "webhook":
                    status[ch] = self._webhook(alert)
            except Exception as exc:  # noqa: BLE001
                log.error("alert channel %s failed: %s", ch, exc)
                status[ch] = f"error: {exc}"
        return status


class AlertEngine:
    def __init__(self, db, settings, events, notifier: Notifier | None = None):
        self.db, self.cfg, self.events = db, settings.alerts, events
        self.notifier = notifier or Notifier(settings.notifiers)
        self._last: dict[tuple, dt.datetime] = {}
        self._lock = threading.Lock()

    def _cooling(self, rule: str, eq: str | None) -> bool:
        with self._lock:
            t = self._last.get((rule, eq))
            if t and utcnow() - t < dt.timedelta(minutes=self.cfg.cooldown_minutes):
                return True
            self._last[(rule, eq)] = utcnow()
            return False

    def evaluate(self, insp: Inspection) -> list[dict]:
        eq = insp.equipment_id
        raised = []
        if self.cfg.alert_on_critical and insp.severity == "Critical" and not self._cooling("critical_defect", eq):
            raised.append(self._raise("critical_defect", "critical", eq,
                                      f"Critical {insp.label} on wafer {insp.wafer_id or insp.id} "
                                      f"(lot {insp.lot_id or '-'}, fail ratio {insp.fail_ratio:.1%}).",
                                      {"inspection_id": insp.id, "label": insp.label}))
        with self.db.Session() as s:
            q = select(Inspection.label, Inspection.review_label).order_by(desc(Inspection.created_at))
            if eq:
                q = q.where(Inspection.equipment_id == eq)
            recent = [(rl or l) != "none" for l, rl in s.execute(q.limit(max(self.cfg.window * 10, 200))).all()]
        if len(recent) >= self.cfg.consecutive_defects and all(recent[: self.cfg.consecutive_defects]) \
                and not self._cooling("consecutive_defects", eq):
            raised.append(self._raise("consecutive_defects", "warning", eq,
                                      f"{self.cfg.consecutive_defects} consecutive defective wafers on {eq or 'the line'}.",
                                      {"last_inspection": insp.id}))
        window = recent[: self.cfg.window]
        if len(window) >= self.cfg.min_samples:
            rate = sum(window) / len(window)
            older = recent[self.cfg.window:]
            baseline = sum(older) / len(older) if len(older) >= self.cfg.min_samples else None
            spike = rate >= self.cfg.spike_rate or (baseline is not None and baseline > 0 and rate >= self.cfg.spike_factor * baseline)
            if spike and not self._cooling("defect_rate_spike", eq):
                b = f"{baseline:.1%}" if baseline is not None else "n/a"
                raised.append(self._raise("defect_rate_spike", "critical" if rate >= 2 * self.cfg.spike_rate else "warning", eq,
                                          f"Defect rate {rate:.1%} over last {len(window)} wafers on {eq or 'the line'} "
                                          f"(baseline {b}, threshold {self.cfg.spike_rate:.0%}).",
                                          {"rate": rate, "baseline": baseline, "window": len(window)}))
        return raised

    def _raise(self, rule, level, eq, message, context) -> dict:
        with self.db.Session() as s:
            a = Alert(rule=rule, level=level, equipment_id=eq, message=message, context=context,
                      channels={c: "pending" for c in self.notifier.plan(level)})
            s.add(a)
            s.commit()
            data = alert_dict(a)
        self.events.publish("alert.raised", data)
        threading.Thread(target=self._dispatch, args=(data,), daemon=True).start()
        return data

    def _dispatch(self, data: dict) -> None:
        status = self.notifier.send(data)
        if status:
            with self.db.Session() as s:
                a = s.get(Alert, data["id"])
                a.channels = status
                s.commit()


def alert_dict(a: Alert) -> dict:
    return {"id": a.id, "created_at": a.created_at.isoformat(), "rule": a.rule, "level": a.level,
            "equipment_id": a.equipment_id, "message": a.message, "context": a.context, "channels": a.channels,
            "acknowledged_by": a.acknowledged_by,
            "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None}

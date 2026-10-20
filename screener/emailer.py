"""Sends the daily additions/removals email through Gmail."""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _rows(items, removed=False):
    if not items:
        return "<p style='color:#5b6b80;margin:4px 0 12px'>None today.</p>"
    lines = []
    for c in items:
        price = f"Rs {c['price']:,.2f}" if c.get("price") else "-"
        extra = c.get("reason", "") if removed else f"Price {price}"
        lines.append(
            f"<tr><td style='padding:6px 10px;border-bottom:1px solid #e3edf8'>"
            f"<a href='{c['screener_url']}' style='color:#0b5cad;text-decoration:none'>"
            f"<b>{c['name']}</b></a> <span style='color:#5b6b80'>({c['symbol']})</span></td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e3edf8;color:#33475b'>{extra}</td></tr>"
        )
    return "<table style='border-collapse:collapse;width:100%;font-size:14px'>" + "".join(lines) + "</table>"


def build_email(latest, site_url):
    ch = latest["changes"]
    added, removed = ch["added"], ch["removed"]
    date = latest["run_date"]
    if latest.get("baseline"):
        subject = (f"Stock Screener: new rules applied, {len(latest['shortlist'])} companies ({date})"
                   if latest.get("rules_changed") else
                   f"Stock Screener: first list ready, {len(latest['shortlist'])} companies ({date})")
        intro = ("This is the first run, so today's list is your starting point. "
                 "From tomorrow you'll get only additions and removals.")
        added = latest["shortlist"]
    else:
        subject = f"Stock Screener {date}: {len(added)} added, {len(removed)} removed"
        intro = f"Your shortlist now has {len(latest['shortlist'])} companies."
    health = latest.get("data_checks", {}).get("health_text", "")
    new_missing = [m for m in latest.get("missing_returns", []) if m.get("new")]
    missing_html = ""
    if new_missing:
        items = "".join(
            f"<li><a href='{m['screener_url']}' style='color:#0b5cad'>{m['name']}</a> "
            f"<span style='color:#5b6b80'>({m['symbol']}, {m['missing']} missing)</span></li>"
            for m in new_missing
        )
        missing_html = (f"<h3 style='color:#8a5a00;margin:16px 0 6px'>New in 'Missing data' "
                        f"({len(new_missing)}), please check by hand</h3><ul style='margin:0;padding-left:18px'>{items}</ul>")
    html = f"""
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:auto;color:#14263d">
  <div style="background:#0b5cad;color:#fff;padding:16px 20px;border-radius:10px 10px 0 0">
    <div style="font-size:20px;font-weight:600">Stock Screener</div>
    <div style="opacity:.85;font-size:13px">Daily update for {date}</div>
  </div>
  <div style="border:1px solid #d6e6f7;border-top:0;padding:16px 20px;border-radius:0 0 10px 10px;background:#f7fbff">
    <p style="margin-top:0">{intro}</p>
    <h3 style="color:#0f7a43;margin:16px 0 6px">Added ({len(added)})</h3>
    {_rows(added)}
    <h3 style="color:#b3261e;margin:16px 0 6px">Removed ({len(removed)})</h3>
    {_rows(removed, removed=True)}
    {missing_html}
    <p style="font-size:13px;color:#5b6b80">{health}</p>
    <p><a href="{site_url}" style="background:#0b5cad;color:#fff;padding:9px 16px;border-radius:6px;text-decoration:none">Open the website</a></p>
    <p style="font-size:12px;color:#7a8a9e">Data for research only, not investment advice.</p>
  </div>
</div>"""
    return subject, html


def send(latest, site_url):
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("EMAIL_TO") or user
    if not (user and password and to):
        print("Email skipped: Gmail secrets not set yet.")
        return False
    subject, html = build_email(latest, site_url)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Stock Screener <{user}>"
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, password.replace(" ", ""))
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    print(f"Email sent to {to}")
    return True

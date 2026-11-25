"""Golden RAG evaluation set: a synthetic, connector-shaped workspace and labelled questions.

The corpus mirrors what each native connector registers (Gmail EML, Slack thread messages,
GitHub code/issues/PRs, Drive documents, a manual CSV upload) and includes hard-negative
distractors that share vocabulary with the answers. Gold evidence is labelled as exact
substrings, never passage IDs, so labels survive chunking changes. Questions were written
before any tuning; tune on `dev` and report on `test`.
"""

import json
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.connectors.core import EnvelopeFactory
from backend.db.models import ConnectorAccount, ConnectorCredential
from backend.schemas.connectors import Provider as ConnectorProvider
from backend.schemas.connectors import Resource
from backend.services.connector_sources import register

Provider = Literal["gmail", "slack", "github", "google_drive"]
Category = Literal["fact", "paraphrase", "multi_hop", "table", "code", "temporal", "unanswerable"]


@dataclass(frozen=True)
class Doc:
    key: str
    provider: Provider | None  # None = manual upload
    filename: str
    content: str
    mime: str = "text/plain"
    metadata: dict[str, object] = field(default_factory=dict)
    kind: str = "file"
    parent: str | None = None


@dataclass(frozen=True)
class Question:
    key: str
    split: Literal["dev", "test"]
    category: Category
    question: str
    keywords: str  # The kind of short query the chat model actually sends to search_snapshots.
    gold: tuple[str, ...] = ()  # Every substring must appear in returned evidence (empty = should abstain).
    answer: tuple[str, ...] = ()  # Key facts an end-to-end answer must contain (case-insensitive).


def prose(topic: str, sentences: int) -> str:
    """Varied, realistic filler so long documents span several passages without repetition."""
    bank = [
        f"The {topic} working group met on Tuesday to review open items and confirm owners for the quarter.",
        f"Questions about {topic} should go to the shared channel first so answers stay searchable.",
        "Teams are expected to document decisions in the wiki within two business days of making them.",
        "Managers review exceptions monthly and record the reason for each approved deviation.",
        "This section is reviewed every six months by the operations committee and updated as needed.",
        "Where local law sets a stricter rule, the local rule applies and HR publishes a country appendix.",
        "New joiners receive a summary during onboarding and acknowledge it in the HR portal.",
        "Feedback on this policy can be submitted anonymously through the quarterly survey.",
        f"Historical context for {topic} is kept in the archive folder for audit purposes.",
        "Any tooling referenced here is provisioned by IT; request access through the service desk.",
        "Exceptions that affect customers require a written note from the accountable director.",
        "The previous version of this text remains available in document history for comparison.",
        f"Team leads summarise {topic} changes in the monthly all-hands so nobody is surprised.",
        "Contractors follow their own agreement where it differs from this document.",
        "Disputes are escalated to the department head and then to the people team.",
        f"Metrics about {topic} are published on the internal dashboard every Monday morning.",
        "Approvals recorded in chat are valid only once they are copied into the ticket.",
        "Regional offices may publish additional guidance that does not contradict this page.",
        f"An audit last year found that most {topic} questions came from teams in their first month.",
        "Templates for the related forms live in the shared drive under Operations.",
    ]
    return " ".join(bank[i % len(bank)] for i in range(sentences))


def email(sender: str, to: str, subject: str, date: str, body: str, message_id: str, refs: str = "") -> str:
    mail = EmailMessage()
    for name, value in (("From", sender), ("To", to), ("Subject", subject), ("Date", date)):
        mail[name] = value
    mail["Message-ID"] = f"<{message_id}@northwind.example>"
    if refs:
        mail["In-Reply-To"] = mail["References"] = f"<{refs}@northwind.example>"
    mail.set_content(body)
    return mail.as_string()


def slack(name: str, metadata: dict[str, object], text: str) -> str:
    # Same raw layout as backend/connectors/slack.py normalize().
    return "# " + name + "\n\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in metadata.items()) + "\n\n" + text


def gh(name: str, facts: dict[str, object], body: str) -> str:
    # Same raw layout as backend/connectors/github.py issue/PR envelopes.
    return "# " + name + "\n\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in facts.items()) + "\n\n" + body


def slack_doc(key: str, channel: str, ts: str, thread: str, author: str, text: str) -> Doc:
    metadata: dict[str, object] = {
        "channel_id": channel,
        "channel_name": {"C1": "eng-release", "C2": "support", "C3": "random"}[channel],
        "message_ts": ts,
        "thread_ts": thread,
        "author_name": author,
        "is_private": False,
    }
    return Doc(
        key,
        "slack",
        "message.md",
        slack(f"{metadata['channel_name']} {ts}", metadata, text),
        "text/markdown",
        metadata,
        "slack_message",
        f"{channel}:{thread}",
    )


HANDBOOK = "\n\n".join(
    [
        "# Northwind Labs employee handbook",
        "## Annual leave\n\n"
        + prose("leave", 16)
        + "\n\nEmployees receive 20 days of annual leave per calendar year. Up to 5 unused days may be carried"
        " over, and carried-over days expire on 31 March of the following year.\n\n"
        + prose("leave", 14),
        "## Remote work\n\n"
        + prose("remote work", 15)
        + "\n\nStaff may work remotely up to 3 days per week. Core collaboration hours are 11:00 to 16:00 PKT,"
        " when everyone should be reachable on chat.\n\n"
        + prose("remote work", 16),
        "## Travel and expenses\n\n"
        + prose("expenses", 17)
        + "\n\nMeals while travelling are reimbursed up to 25 USD per day. Receipts must be submitted within"
        " 30 days of the trip, otherwise the claim is rejected.\n\n"
        + prose("expenses", 13),
        "## Equipment\n\n"
        + prose("equipment", 14)
        + "\n\nEvery employee receives a laptop refresh every 36 months; damaged devices go to IT, not to the"
        " manager.\n\n"
        + prose("equipment", 15),
    ]
)

OKRS = """# Q3 OKR results

| Objective | Key result | Target | Actual | Owner |
| --- | --- | --- | --- | --- |
| Faster checkout | p95 checkout latency | 300 ms | 240 ms | Payments |
| Grow enterprise | New enterprise ARR | 1.2M USD | 0.95M USD | Sales |
| Support quality | CSAT | 92% | 94% | Support |
| Reliability | Monthly uptime | 99.9% | 99.82% | Platform |
| Hiring | Engineers hired | 8 | 6 | People |
"""

SECURITY = "\n\n".join(
    [
        "# Information security policy",
        "## Authentication\n\n"
        + prose("security", 14)
        + "\n\nMulti-factor authentication is mandatory for every company account. Passwords no longer need"
        " periodic rotation; they are changed only after a suspected compromise.\n\n"
        + prose("security", 13),
        "## Access reviews\n\n"
        + prose("access", 15)
        + "\n\nAccess reviews happen quarterly. The next access review is due on 15 December and is run by the"
        " security lead, Hamza Iqbal.\n\n"
        + prose("access", 12),
        "## Devices\n\n" + prose("devices", 3) + "\n\nAll laptops must use full-disk encryption.",
    ]
)

ADR = """# ADR-014: Vector storage for semantic search

Status: Accepted (2026-08-21)

## Context

We need to store document embeddings next to the existing relational data. Options were PostgreSQL
with the pgvector extension, or a managed vector database such as Pinecone.

## Decision

We chose PostgreSQL with pgvector. Customer data must stay in-region, and a second datastore would add
roughly 9,000 USD per year plus another backup and access-control surface.

## Consequences

Exact search is acceptable below a few million vectors; we will revisit HNSW indexes if latency grows.
"""

MARKETING = "\n\n".join(
    [
        "# Blog draft: 10 tips for choosing a vector database",
        "Pinecone, Weaviate and Milvus are popular managed choices. Managed services remove operational work"
        " and scale automatically. " + prose("marketing", 4),
        "Tip 7: benchmark with your own data, because recall depends heavily on embedding quality. "
        + prose("marketing", 4),
    ]
)

PRORATION = '''"""Billing proration for plan changes."""

from decimal import ROUND_HALF_UP, Decimal


def calculate_proration(days_used: int, days_in_cycle: int, plan_price: Decimal) -> Decimal:
    """Annual plans are prorated daily; the unused share is credited to the next invoice."""
    if days_in_cycle <= 0:
        raise ValueError("days_in_cycle must be positive")
    unused = Decimal(days_in_cycle - days_used) / Decimal(days_in_cycle)
    return (plan_price * unused).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def apply_credit(invoice_total: Decimal, credit: Decimal) -> Decimal:
    """Credits never make an invoice negative."""
    return max(Decimal("0.00"), invoice_total - credit)
'''

SESSION = """export const SESSION_TTL_MINUTES = 30;
export const REFRESH_TOKEN_TTL_DAYS = 14;

export function isExpired(issuedAt: number, now: number): boolean {
  return now - issuedAt > SESSION_TTL_MINUTES * 60_000;
}

export function rotateRefreshToken(previous: string): string {
  // Each refresh issues a new token and revokes the previous one (rotation).
  return crypto.randomUUID() + ":" + previous.slice(0, 4);
}
"""

README = """# northwind-app

## Local setup

Requires Python 3.12 and PostgreSQL 16. Run `make bootstrap` once, then `make dev` to start the API on
port 8080 and the web client on port 5173.

## Tests

Run `make test`. Integration tests need a disposable database named `northwind_test`.
"""

ON_CALL = """week,primary,secondary
2026-11-02,Bilal Khan,Sara Malik
2026-11-09,Zara Ahmed,Omar Farooq
2026-11-16,Hina Malik,Bilal Khan
2026-11-23,Omar Farooq,Zara Ahmed
"""

DOCS: tuple[Doc, ...] = (
    # --- Gmail (EML as registered by backend/connectors/gmail.py) ---
    Doc("mail_renewal", "gmail", "Acme-Cloud-contract-renewal.eml", email(
        "Priya Shah <priya@northwind.example>", "team@northwind.example", "Acme Cloud contract renewal",
        "Tue, 02 Sep 2026 10:12:00 +0500",
        "Hi all,\n\nAcme Cloud proposed renewing our hosting contract at 18,400 USD per year, a 12% increase."
        " Legal (Daniel Ortiz) needs the redlined MSA by 15 September so we can respond before the notice"
        " window closes.\n\nThanks,\nPriya", "m1"), "message/rfc822", {"thread_id": "t1"}, "email_message"),
    Doc("mail_renewal_reply", "gmail", "Re-Acme-Cloud-contract-renewal.eml", email(
        "Omar Farooq <omar@northwind.example>", "team@northwind.example", "Re: Acme Cloud contract renewal",
        "Wed, 03 Sep 2026 09:40:00 +0500",
        "Finance approved the renewal up to 17,000 USD per year; anything above that needs CFO sign-off.\n\n"
        "On Tue, 2 Sep 2026 Priya Shah wrote:\n> Acme Cloud proposed renewing our hosting contract at 18,400 USD"
        " per year, a 12% increase.", "m2", "m1"), "message/rfc822", {"thread_id": "t1"}, "email_message"),
    Doc("mail_incident", "gmail", "Postmortem-INC-2041.eml", email(
        "Bilal Khan <bilal@northwind.example>", "eng@northwind.example", "Postmortem: INC-2041 checkout outage",
        "Mon, 13 Oct 2026 18:05:00 +0500",
        "Summary: an expired TLS certificate on the payments gateway caused 47 minutes of checkout failures on"
        " 12 October.\n\nAction items:\n1. Automate certificate renewal with cert-manager (owner: Bilal Khan).\n"
        "2. Add an alert 14 days before any certificate expires (owner: Hina Malik).", "m3"),
        "message/rfc822", {"thread_id": "t2"}, "email_message"),
    Doc("mail_award", "gmail", "Certificate-of-appreciation.eml", email(
        "People Team <people@northwind.example>", "all@northwind.example", "Certificate of appreciation",
        "Fri, 17 Oct 2026 16:00:00 +0500",
        "Congratulations to the support team, who receive this quarter's certificate of appreciation for"
        " keeping customer satisfaction high during the October outage.", "m4"),
        "message/rfc822", {"thread_id": "t3"}, "email_message"),
    Doc("mail_hiring", "gmail", "Hiring-update.eml", email(
        "Sara Malik <sara@northwind.example>", "leads@northwind.example", "Hiring update",
        "Thu, 06 Nov 2026 11:30:00 +0500",
        "We extended an offer to Ayesha Noor for the Senior Data Engineer role. She accepted and starts on"
        " 4 January.", "m5"), "message/rfc822", {"thread_id": "t4"}, "email_message"),
    Doc("mail_office", "gmail", "Office-move.eml", email(
        "Facilities <facilities@northwind.example>", "all@northwind.example", "Office move",
        "Mon, 20 Oct 2026 08:00:00 +0500",
        "The Lahore office moves to Gulberg Tower, floor 9, on 1 November. IT will reissue access badges on"
        " the first day.", "m6"), "message/rfc822", {"thread_id": "t5"}, "email_message"),
    # --- Slack (one source per message, thread metadata as in the Slack connector) ---
    slack_doc("slack_root", "C1", "200.000100", "200.000100", "Sara Malik", "Release 3.8 go/no-go for Thursday?"),
    slack_doc("slack_nogo", "C1", "200.000200", "200.000100", "Bilal Khan",
              "No-go: the Android build crashes on login for Samsung A-series devices (bug MOB-772)."),
    slack_doc("slack_target", "C1", "200.000300", "200.000100", "Hina Malik",
              "The fix is merged. QA re-tests Friday morning; the new release target is Monday 10 AM."),
    slack_doc("slack_vat", "C2", "300.000100", "300.000100", "Zara Ahmed",
              "Customer Globex reports invoices showing VAT twice since the 3.7 release."),
    slack_doc("slack_pizza", "C3", "400.000100", "400.000100", "Omar Farooq",
              "Thursday pizza order for the release party: pepperoni and veggie."),
    # --- GitHub (code files, issue, pull request, README) ---
    Doc("gh_proration", "github", "proration.py", PRORATION, "text/x-python",
        {"file_path": "billing/proration.py", "repository": "northwind/app"}, "source_file"),
    Doc("gh_session", "github", "session.ts", SESSION, "text/plain",
        {"file_path": "web/auth/session.ts", "repository": "northwind/app"}, "source_file"),
    Doc("gh_issue", "github", "issue.md", gh("Issue #311: VAT applied twice on invoices",
        {"number": 311, "state": "open", "labels": ["bug", "billing"], "assignee": "zara-ahmed"},
        "Regression from PR #298, which moved tax calculation into InvoiceBuilder without removing it from"
        " LineItem. Every invoice since release 3.7 adds VAT twice."), "text/markdown", {"number": 311}, "issue"),
    Doc("gh_pr", "github", "pull_request.md", gh("PR #298: Move tax calculation into InvoiceBuilder",
        {"number": 298, "state": "merged", "author": "omar-farooq"},
        "Centralises tax in InvoiceBuilder so totals are computed once per invoice."), "text/markdown",
        {"number": 298}, "pull_request"),
    Doc("gh_readme", "github", "README.md", README, "text/markdown",
        {"file_path": "README.md", "repository": "northwind/app"}, "source_file"),
    # --- Google Drive documents ---
    Doc("drive_handbook", "google_drive", "Employee handbook.md", HANDBOOK, "text/markdown"),
    Doc("drive_okrs", "google_drive", "Q3 OKR results.md", OKRS, "text/markdown"),
    Doc("drive_security", "google_drive", "Information security policy.md", SECURITY, "text/markdown"),
    Doc("drive_adr", "google_drive", "ADR-014 vector storage.md", ADR, "text/markdown"),
    Doc("drive_marketing", "google_drive", "Blog draft vector databases.md", MARKETING, "text/markdown"),
    # --- Manual upload ---
    Doc("upload_oncall", None, "on_call_rotation.csv", ON_CALL, "text/csv"),
)

def distractors() -> tuple[Doc, ...]:
    """Hard negatives: same vocabulary as the answers, different facts (other releases, renewals, offices...)."""
    mails = [
        ("Acme Analytics renewal", "Acme Analytics renewed at 9,800 USD per year with no price change; no legal review"
         " is needed this cycle."),
        ("Re: Acme Cloud invoice question", "The September Acme Cloud invoice was 1,450 USD because of extra egress;"
         " it is unrelated to the contract renewal."),
        ("Postmortem: INC-1987 search outage", "A full disk on the search cluster caused 12 minutes of degraded"
         " search on 2 August. Action item: add disk alerts (owner: Omar Farooq)."),
        ("Postmortem: INC-2003 email delays", "A misconfigured queue delayed notification emails by up to 3 hours on"
         " 18 September."),
        ("Certificate renewal reminder", "The marketing website certificate renews automatically on 1 December;"
         " no action needed."),
        ("Hiring update: design", "We are still interviewing for the Product Designer role; no offer has been made."),
        ("Office move: Islamabad", "The Islamabad team stays at Blue Area Plaza; only the Lahore office is moving."),
        ("Leave reminder", "Please submit leave requests at least two weeks in advance for December."),
        ("Expense report reminder", "Monthly expense reports close on the 5th of each month."),
        ("Security training", "Annual security awareness training must be completed by 30 November."),
        ("Re: Release 3.7 retrospective", "Release 3.7 shipped on time on 2 October after one hotfix for"
         " currency formatting."),
        ("Vendor list", "Approved vendors include Acme Cloud, Acme Analytics and Globex Logistics."),
    ]
    docs = [
        Doc(f"mail_d{i}", "gmail", f"distractor-{i}.eml", email(
            "Ops <ops@northwind.example>", "team@northwind.example", subject,
            f"Mon, {10 + i:02d} Nov 2026 09:00:00 +0500", body, f"d{i}"), "message/rfc822",
            {"thread_id": f"dt{i}"}, "email_message")
        for i, (subject, body) in enumerate(mails)
    ]
    chats = [
        ("C1", "Release 3.9 planning starts next sprint; scope includes dark mode and offline drafts."),
        ("C1", "Release 3.7 go/no-go: go. Android and iOS builds passed QA."),
        ("C1", "Reminder: release notes for 3.8 need marketing review before publishing."),
        ("C2", "Customer Initech asked how to export invoices as CSV; sent them the help article."),
        ("C2", "Customer Globex Logistics updated their billing contact to finance@globex.example."),
        ("C2", "VAT registration numbers can now be added on the billing settings page."),
        ("C3", "Lahore cafe recommendations for the office warming: Cafe Aylanto and Gloria Jeans."),
        ("C3", "Friday quiz: which planet has the most moons? Answer next week."),
    ]
    docs += [
        slack_doc(f"slack_d{i}", channel, f"5{i:02d}.000100", f"5{i:02d}.000100", "Team member", text)
        for i, (channel, text) in enumerate(chats)
    ]
    docs += [
        Doc("gh_d_tax", "github", "tax.py", (
            '"""Tax helpers."""\n\nfrom decimal import Decimal\n\n\n'
            "def vat_rate(country: str) -> Decimal:\n"
            '    """VAT rates by country code; unknown countries default to zero."""\n'
            '    return {"PK": Decimal("0.17"), "GB": Decimal("0.20")}.get(country, Decimal("0"))\n'
        ), "text/x-python", {"file_path": "billing/tax.py", "repository": "northwind/app"}, "source_file"),
        Doc("gh_d_issue", "github", "issue.md", gh("Issue #305: Invoice PDF shows wrong currency symbol",
            {"number": 305, "state": "closed", "labels": ["bug", "billing"], "assignee": "omar-farooq"},
            "Fixed in release 3.7.1 by formatting with the account locale."), "text/markdown",
            {"number": 305}, "issue"),
        Doc("gh_d_pr", "github", "pull_request.md", gh("PR #301: Add VAT number field",
            {"number": 301, "state": "merged", "author": "zara-ahmed"},
            "Adds an optional VAT registration number to billing settings."), "text/markdown",
            {"number": 301}, "pull_request"),
        Doc("gh_d_config", "github", "config.ts", (
            "export const API_TIMEOUT_MS = 10_000;\nexport const MAX_UPLOAD_MB = 25;\n"
            "export const SESSION_WARNING_MINUTES = 5; // warn before the session expires\n"
        ), "text/plain", {"file_path": "web/config.ts", "repository": "northwind/app"}, "source_file"),
        Doc("gh_d_contrib", "github", "CONTRIBUTING.md", (
            "# Contributing\n\nOpen a pull request against main. Every PR needs one approving review and green"
            " CI. Use conventional commit messages.\n"
        ), "text/markdown", {"file_path": "CONTRIBUTING.md", "repository": "northwind/app"}, "source_file"),
    ]
    docs += [
        Doc("drive_d_security_old", "google_drive", "Security policy v1 (superseded).md", (
            "# Information security policy v1 (superseded 2025)\n\nPasswords must be rotated every 90 days."
            " Access reviews were annual. " + prose("legacy security", 12)
        ), "text/markdown"),
        Doc("drive_d_contractor", "google_drive", "Contractor guide.md", (
            "# Contractor guide\n\nContractors accrue 12 days of leave per year and may not carry days over."
            " Contractors work fully remotely unless the project says otherwise. " + prose("contractors", 14)
        ), "text/markdown"),
        Doc("drive_d_okr_q2", "google_drive", "Q2 OKR results.md", (
            "# Q2 OKR results\n\n| Objective | Key result | Target | Actual |\n| --- | --- | --- | --- |\n"
            "| Faster checkout | p95 checkout latency | 350 ms | 330 ms |\n"
            "| Grow enterprise | New enterprise ARR | 1.0M USD | 1.1M USD |\n"
            "| Reliability | Monthly uptime | 99.9% | 99.95% |\n"
        ), "text/markdown"),
        Doc("drive_d_adr", "google_drive", "ADR-011 queueing.md", (
            "# ADR-011: Background job queue\n\nStatus: Accepted\n\nWe chose PostgreSQL-backed jobs over Redis"
            " queues to avoid a second datastore. " + prose("queues", 8)
        ), "text/markdown"),
        Doc("drive_d_travel", "google_drive", "Travel booking guide.md", (
            "# Travel booking guide\n\nBook flights through the travel desk at least 14 days ahead. Hotels are"
            " capped at 120 USD per night. " + prose("travel", 14)
        ), "text/markdown"),
        Doc("drive_d_meeting", "google_drive", "Leadership meeting notes.md", (
            "# Leadership meeting notes, 28 October\n\nDiscussed Q4 hiring freeze for non-engineering roles,"
            " office warming party budget and the 3.9 roadmap. " + prose("leadership", 12)
        ), "text/markdown"),
    ]
    return tuple(docs)


DOCS = DOCS + distractors()

QUESTIONS: tuple[Question, ...] = (
    # Single facts
    Question("q01", "dev", "fact", "What price did Acme Cloud propose for the renewal?", "Acme Cloud renewal price",
             ("18,400 USD per year",), ("18,400",)),
    Question("q02", "test", "fact", "Who needs the redlined MSA and by when?", "redlined MSA",
             ("Daniel Ortiz", "15 September"), ("Daniel Ortiz", "15 September")),
    Question("q03", "dev", "fact", "What caused incident INC-2041?", "INC-2041",
             ("expired TLS certificate",), ("expired TLS certificate",)),
    Question("q04", "test", "fact", "How long did the INC-2041 checkout outage last?", "INC-2041 checkout outage",
             ("47 minutes",), ("47 minutes",)),
    Question("q05", "dev", "fact", "When does Ayesha Noor start?", "Ayesha Noor start",
             ("starts on 4 January",), ("4 January",)),
    Question("q06", "test", "fact", "Where is the Lahore office moving?", "Lahore office move",
             ("Gulberg Tower, floor 9",), ("Gulberg Tower",)),
    Question("q07", "dev", "fact", "Why was release 3.8 a no-go?", "release 3.8 no-go",
             ("crashes on login for Samsung A-series",), ("Samsung",)),
    Question("q08", "test", "fact", "What is the new target for release 3.8?", "release 3.8 target",
             ("Monday 10 AM",), ("Monday",)),
    Question("q09", "dev", "fact", "Which customer reported VAT showing twice?", "VAT twice customer",
             ("Customer Globex",), ("Globex",)),
    Question("q10", "test", "fact", "How many days of annual leave do employees get?", "annual leave days",
             ("20 days of annual leave",), ("20 days",)),
    Question("q11", "dev", "fact", "When do carried-over leave days expire?", "carried-over leave expire",
             ("expire on 31 March",), ("31 March",)),
    Question("q12", "test", "fact", "What are the core collaboration hours?", "core collaboration hours",
             ("11:00 to 16:00 PKT",), ("11:00",)),
    Question("q13", "dev", "fact", "What is the daily meal limit when travelling?", "meal limit travelling",
             ("25 USD per day",), ("25 USD",)),
    Question("q14", "test", "fact", "When is the next access review due?", "next access review",
             ("15 December",), ("15 December",)),
    Question("q15", "dev", "fact", "Who runs the access reviews?", "access review security lead",
             ("Hamza Iqbal",), ("Hamza Iqbal",)),
    # Paraphrase: little keyword overlap with the source (exercises semantic retrieval).
    Question("q16", "dev", "paraphrase", "How long do staff have to hand in proof of purchase after a business trip?",
             "proof of purchase business trip deadline", ("within 30 days of the trip",), ("30 days",)),
    Question("q17", "test", "paraphrase", "Do I have to change my password every few months?",
             "password change frequency", ("no longer need periodic rotation",), ("rotation",)),
    Question("q18", "dev", "paraphrase", "How often do we get new computers?", "new computers how often",
             ("laptop refresh every 36 months",), ("36 months",)),
    Question("q19", "test", "paraphrase", "How many days a week can I work from home?", "work from home days",
             ("up to 3 days per week",), ("3 days",)),
    Question("q20", "dev", "paraphrase", "Why did we not adopt a hosted vector service?",
             "hosted vector service decision", ("Customer data must stay in-region",), ("in-region",)),
    Question("q21", "test", "paraphrase", "What made the checkout break in October?", "checkout break October",
             ("expired TLS certificate",), ("certificate",)),
    Question("q22", "test", "paraphrase", "Who is the new data engineering hire?", "new data engineer hire",
             ("Ayesha Noor",), ("Ayesha Noor",)),
    # Multi-hop across two sources
    Question("q23", "dev", "multi_hop", "Is the Acme Cloud proposal within the amount finance approved?",
             "Acme Cloud renewal finance approved", ("18,400 USD per year", "up to 17,000 USD"), ("17,000", "18,400")),
    Question("q24", "test", "multi_hop", "Which pull request caused the VAT bug that Globex reported?",
             "VAT twice regression pull request", ("Customer Globex", "Regression from PR #298"), ("298",)),
    Question("q25", "test", "multi_hop", "Who owns the VAT fix and what change introduced the bug?",
             "VAT invoices issue assignee", ("zara-ahmed", "PR #298"), ("298",)),
    Question("q26", "dev", "multi_hop", "What follow-ups came out of the certificate outage and who owns them?",
             "INC-2041 action items owner", ("cert-manager (owner: Bilal Khan)", "14 days before"), ("cert-manager",)),
    # Table lookups
    Question("q27", "dev", "table", "What was the actual p95 checkout latency in Q3?", "Q3 p95 checkout latency",
             ("240 ms",), ("240",)),
    Question("q28", "test", "table", "Did we hit the enterprise ARR target in Q3?", "Q3 enterprise ARR target",
             ("0.95M USD",), ("0.95",)),
    Question("q29", "test", "table", "What was Q3 monthly uptime?", "Q3 uptime", ("99.82%",), ("99.82",)),
    Question("q30", "dev", "table", "How many engineers were hired against the target?", "engineers hired target",
             ("Engineers hired",), ("6",)),
    Question("q31", "test", "table", "Who is primary on call the week of 9 November?", "on call 2026-11-09",
             ("2026-11-09", "Zara Ahmed"), ("Zara Ahmed",)),
    # Code
    Question("q32", "dev", "code", "How is proration rounded in billing?", "calculate_proration rounding",
             ("ROUND_HALF_UP",), ("ROUND_HALF_UP",)),
    Question("q33", "test", "code", "What is the web session timeout?", "SESSION_TTL_MINUTES",
             ("SESSION_TTL_MINUTES = 30",), ("30",)),
    Question("q34", "test", "code", "Can a credit make an invoice negative?", "apply_credit invoice negative",
             ("Credits never make an invoice negative",), ("never",)),
    Question("q35", "dev", "code", "What do I need installed to run the app locally?", "local setup requirements",
             ("Python 3.12 and PostgreSQL 16",), ("Python 3.12",)),
    Question("q36", "test", "code", "How long do refresh tokens last?", "REFRESH_TOKEN_TTL_DAYS",
             ("REFRESH_TOKEN_TTL_DAYS = 14",), ("14",)),
    # Temporal / "latest" wording (passages still useful; live facts should be flagged)
    Question("q37", "dev", "temporal", "What is the current status of release 3.8?", "release 3.8 status",
             ("Monday 10 AM",), ("Monday",)),
    Question("q38", "test", "temporal", "What is the latest decision on vector storage?", "ADR-014 vector storage",
             ("We chose PostgreSQL with pgvector",), ("pgvector",)),
    # Unanswerable: nothing in the corpus answers these (should abstain, not guess).
    Question("q39", "dev", "unanswerable", "What is the parental leave policy?", "parental leave policy"),
    Question("q40", "test", "unanswerable", "What was the Q4 CSAT score?", "Q4 CSAT"),
    Question("q41", "test", "unanswerable", "Who approved the Pinecone contract?", "Pinecone contract approved"),
    Question("q42", "dev", "unanswerable", "What is the Karachi office address?", "Karachi office address"),
)


async def placeholder_credential(db: AsyncSession, owner: str) -> UUID:
    # Chat only includes accounts with a credential reference; snapshot search never decrypts it.
    credential = ConnectorCredential(owner_id=owner, ciphertext="golden-evaluation-placeholder")
    db.add(credential)
    await db.flush()
    return credential.id


async def load_corpus(db: AsyncSession, owner: str) -> list[UUID]:
    """Register every golden document for `owner` the way connectors and uploads do; returns source IDs."""
    accounts: dict[str, ConnectorAccount] = {}
    for provider in ("gmail", "slack", "github", "google_drive"):
        account = ConnectorAccount(
            credential_reference=await placeholder_credential(db, owner),
            owner_id=owner,
            provider=provider,
            external_account_id="golden-" + provider,
            display_name="Golden " + provider,
            status="connected",
            authentication_status="valid",
        )
        db.add(account)
        accounts[provider] = account
    await db.flush()
    upload_account = ConnectorAccount(
        credential_reference=await placeholder_credential(db, owner),
        owner_id=owner,
        provider="google_drive",
        external_account_id="golden-upload",
        display_name="Golden upload",
        status="connected",
        authentication_status="valid",
    )
    db.add(upload_account)
    await db.flush()
    identifiers: list[UUID] = []
    for doc in DOCS:
        # Manual uploads are modelled as a separate Drive-like account so registration stays one code path.
        account = upload_account if doc.provider is None else accounts[doc.provider]
        provider = cast(ConnectorProvider, account.provider)
        factory = EnvelopeFactory(account.id, owner, provider, account.external_account_id)
        resource = Resource(
            id="golden:" + doc.key, name=doc.filename, kind=doc.kind, parent_id=doc.parent, metadata=doc.metadata
        )
        envelope = factory.text(resource, doc.content, doc.filename, doc.mime)
        await register(db, account, envelope)
        identifiers.append(envelope.source_id)
    return identifiers

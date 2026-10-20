#!/usr/bin/env python3
"""
Automatic course + faculty feedback submitter for the MUST CMS portal.

Flow (mirrors the browser requests you captured):
  1. GET  /login.aspx                -> read the ASP.NET hidden tokens
  2. POST /login.aspx                -> 302 to /DashBoard.aspx when credentials are OK
  3. GET  /DashBoard.aspx            -> 302 to /StudentFeedBackSummary.aspx if feedback is pending,
                                        otherwise the dashboard itself is returned -> nothing to do
  4. GET  /StudentFeedBackSummary.aspx   (course list, type 1)
  5. POST summary (click a course link)  -> 302 to /SubmitFeedBack.aspx?...
  6. GET  /SubmitFeedBack.aspx?...       -> rating form
  7. POST rating form (all "Excellent" + comment) -> 302 back to the summary
  ... repeat until no course is "Available", then do the same for type 2 (faculty), which
  needs a drop-down postback each time because the summary resets to "Course Feedback".

All hidden fields (__VIEWSTATE, __EVENTVALIDATION, __PREVIOUSPAGE ...) are read from the
previous response automatically, so no token is ever hard-coded.
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------
LOGIN_PATH = "/login.aspx"
DASHBOARD_PATH = "/DashBoard.aspx"
SUMMARY_PATH = "/StudentFeedBackSummary.aspx"

TYPE_SELECT = "ctl00$DataContent$ddlfeedbacktype"
GRID_ID = "ctl00_DataContent_gvFeedBack"
COURSE, FACULTY = "1", "2"
TYPE_LABEL = {COURSE: "Course", FACULTY: "Faculty"}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)
SKIP_INPUT_TYPES = {"submit", "button", "image", "reset", "file"}


# --------------------------------------------------------------------------------------
# Small data classes
# --------------------------------------------------------------------------------------
@dataclass
class Account:
    prefix: str
    session: str
    program: str
    roll_no: str
    password: str

    @property
    def label(self) -> str:
        return f"{self.session}-{self.program}-{self.roll_no}"


@dataclass
class Row:
    number: str
    name: str
    status: str            # "Available" / "Completed"
    target: str | None     # __doPostBack target (None when the row has no link)

    @property
    def available(self) -> bool:
        return self.status.lower() == "available" and bool(self.target)


@dataclass
class Page:
    """A fetched HTML page plus helpers to read ASP.NET forms from it."""
    url: str
    soup: BeautifulSoup
    redirect: str | None = None   # absolute URL if the server answered 3xx

    def form(self, form_id: str | None = None):
        form = self.soup.find("form", id=form_id) if form_id else self.soup.find("form")
        if form is None:
            raise RuntimeError(f"No <form> found on {self.url}")
        return form

    def fields(self, form_id: str | None = None) -> list[tuple[str, str]]:
        """Everything a browser would submit for this form (minus buttons / file inputs)."""
        out: list[tuple[str, str]] = []
        for el in self.form(form_id).find_all(["input", "textarea", "select"]):
            name = el.get("name")
            if not name:
                continue
            if el.name == "textarea":
                out.append((name, el.text or ""))
            elif el.name == "select":
                opt = el.find("option", selected=True) or el.find("option")
                out.append((name, (opt.get("value", opt.text) if opt else "")))
            else:
                itype = (el.get("type") or "text").lower()
                if itype in SKIP_INPUT_TYPES:
                    continue
                if itype in ("radio", "checkbox") and not el.has_attr("checked"):
                    continue
                out.append((name, el.get("value", "")))
        return out

    def form_action(self, form_id: str | None = None) -> str:
        return urljoin(self.url, self.form(form_id).get("action") or self.url)

    def selected_type(self) -> str | None:
        sel = self.soup.find("select", attrs={"name": TYPE_SELECT})
        if not sel:
            return None
        opt = sel.find("option", selected=True) or sel.find("option")
        return opt.get("value") if opt else None

    def rows(self) -> list[Row]:
        grid = self.soup.find("table", id=GRID_ID)
        rows: list[Row] = []
        if grid is None:
            return rows
        for tr in grid.find_all("tr"):
            status_el = tr.find("span", id=re.compile(r"lblStatus$"))
            if status_el is None:
                continue                      # header / pager row
            tds = tr.find_all("td")
            link = tr.find("a", id=re.compile(r"lnkCourse$"))
            target = None
            if link is not None and link.get("href"):
                m = re.search(r"__doPostBack\('([^']+)'", link["href"])
                target = m.group(1) if m else None
            name = (link.get_text(" ", strip=True) if link is not None
                    else (tds[1].get_text(" ", strip=True) if len(tds) > 1 else "?"))
            number = tds[0].get_text(strip=True) if tds else "?"
            rows.append(Row(number, name, status_el.get_text(strip=True), target))
        return rows

    def message(self) -> str:
        """Best-effort error text (used when login fails)."""
        for el in self.soup.find_all(["span", "div", "label"], id=re.compile(r"(msg|error|message|lbl)", re.I)):
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt[:200]
        return "no error text found on page"


def set_field(fields: list[tuple[str, str]], name: str, value: str) -> None:
    """Replace every occurrence of `name` with a single (name, value); append if absent."""
    idx = [i for i, (k, _) in enumerate(fields) if k == name]
    if idx:
        fields[idx[0]] = (name, value)
        for i in reversed(idx[1:]):
            del fields[i]
    else:
        fields.append((name, value))


# --------------------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------------------
class Portal:
    def __init__(self, base_url: str, verify_ssl: bool, delay: float, timeout: float = 30):
        self.base = base_url.rstrip("/")
        self.verify = verify_ssl
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        })
        self.last_url: str | None = None
        if not verify_ssl:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # -- low level ---------------------------------------------------------------------
    def _send(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = {}
        if self.last_url:
            headers["Referer"] = self.last_url
        if method == "POST":
            headers["Origin"] = self.base
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                resp = self.session.request(
                    method, url, headers=headers, allow_redirects=False,
                    timeout=self.timeout, verify=self.verify, **kwargs)
                if resp.status_code >= 500 and attempt < 3:
                    time.sleep(1.5 * attempt)
                    continue
                return resp
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                time.sleep(1.5 * attempt)
        raise RuntimeError(f"{method} {url} failed after 3 attempts: {last_exc}")

    def _page(self, resp: requests.Response, url: str) -> Page:
        redirect = None
        if resp.status_code in (301, 302, 303, 307, 308):
            redirect = urljoin(url, resp.headers.get("Location", ""))
        else:
            self.last_url = url
        if self.delay:
            time.sleep(self.delay)
        return Page(url, BeautifulSoup(resp.text, "html.parser"), redirect)

    # -- public verbs ------------------------------------------------------------------
    def get(self, path_or_url: str) -> Page:
        url = urljoin(self.base + "/", path_or_url)
        return self._page(self._send("GET", url), url)

    def post(self, url: str, fields: list[tuple[str, str]], multipart: bool) -> Page:
        if multipart:
            # The portal's pages are multipart/form-data and always carry an empty file input.
            parts = [(k, (None, v)) for k, v in fields]
            pos = next((i for i, (k, _) in enumerate(fields) if k.startswith("ctl00$DataContent")), len(parts))
            parts.insert(pos, ("ctl00$FileUpload1", ("", b"", "application/octet-stream")))
            resp = self._send("POST", url, files=parts)
        else:
            resp = self._send("POST", url, data=fields)
        return self._page(resp, url)

    def postback(self, page: Page, target: str, extra: dict[str, str] | None = None) -> Page:
        """Simulate __doPostBack(target, '') from `page`."""
        fields = page.fields()
        set_field(fields, "__EVENTTARGET", target)
        set_field(fields, "__EVENTARGUMENT", "")
        for k, v in (extra or {}).items():
            set_field(fields, k, v)
        return self.post(page.form_action(), fields, multipart=True)

    # -- portal steps ------------------------------------------------------------------
    def login(self, acct: Account) -> None:
        page = self.get(LOGIN_PATH)
        fields = page.fields("form2")
        set_field(fields, "ddl_Session", acct.session)
        set_field(fields, "ddl_Program", acct.program)
        set_field(fields, "txt_RollNo", acct.roll_no)
        set_field(fields, "txt_Password", acct.password)
        set_field(fields, "btn_StudentSignIn", "Sign In")
        result = self.post(page.form_action("form2"), fields, multipart=False)
        if not (result.redirect and "dashboard" in result.redirect.lower()):
            raise RuntimeError(f"Login failed ({result.message()})")

    def feedback_pending(self) -> bool:
        """Dashboard redirects to the feedback summary only while feedback is outstanding."""
        page = self.get(DASHBOARD_PATH)
        return bool(page.redirect and "studentfeedbacksummary" in page.redirect.lower())

    def summary(self, ftype: str) -> Page:
        """Open the summary page showing the requested feedback type (1=course, 2=faculty)."""
        page = self.get(SUMMARY_PATH)
        if page.selected_type() != ftype:
            page = self.postback(page, TYPE_SELECT, {TYPE_SELECT: ftype})
            if page.redirect:                    # unexpected, follow once
                page = self.get(page.redirect)
        return page

    def submit_row(self, page: Page, row: Row, rating: str, comment: str) -> bool:
        """Open one course/faculty form, fill it in and submit. Returns True on success."""
        hop = self.postback(page, row.target)
        if not (hop.redirect and "submitfeedback" in hop.redirect.lower()):
            return False
        form_page = self.get(hop.redirect)
        fields = form_page.fields()
        groups = [k for k, _ in fields if k.endswith("$rdGroup")]
        if not groups:
            return False
        for name in set(groups):
            set_field(fields, name, rating)
        set_field(fields, "ctl00$DataContent$txtComments", comment)
        set_field(fields, "ctl00$DataContent$BtnSubmitFeedBack", "Submit FeedBack")
        done = self.post(form_page.form_action(), fields, multipart=True)
        if not done.redirect:
            return False
        target = done.redirect.lower()
        # Normal case: back to the summary. The very LAST feedback has nothing left to list,
        # so the portal sends us straight to the dashboard instead - that is also success.
        return "studentfeedbacksummary" in target or "dashboard" in target


# --------------------------------------------------------------------------------------
# Progress display
# --------------------------------------------------------------------------------------
class Progress:
    def __init__(self, total: int, done: int):
        self.total, self.done, self.start = total, done, time.time()
        self.tty = sys.stdout.isatty()

    def show(self, text: str, final: bool = False) -> None:
        width = 28
        frac = self.done / self.total if self.total else 1
        bar = "#" * int(width * frac) + "-" * (width - int(width * frac))
        line = f"[{bar}] {self.done}/{self.total} ({frac:4.0%})  {text}"
        if self.tty:
            sys.stdout.write("\r\033[K" + line + ("\n" if final else ""))
            sys.stdout.flush()
        else:
            print(line)

    def advance(self, text: str) -> None:
        self.done += 1
        self.show(text)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def process_type(portal: Portal, ftype: str, progress: Progress, rating: str, comment: str,
                 failed: list[str]) -> int:
    """Submit every 'Available' row of one feedback type. Reuses the same loop for course & faculty."""
    label = TYPE_LABEL[ftype]
    submitted = 0
    skip: set[str] = set()
    while True:
        page = portal.summary(ftype)                      # fresh tokens every iteration
        todo = [r for r in page.rows() if r.available and r.target not in skip]
        if not todo:
            return submitted
        row = todo[0]
        if portal.submit_row(page, row, rating, comment):
            submitted += 1
            progress.advance(f"{label}: {row.name}")
        else:
            skip.add(row.target)                           # never retry a failing row forever
            failed.append(f"{label}: {row.name}")
            progress.show(f"{label}: FAILED {row.name}")
            print()


def run_account(acct: Account, cfg: dict) -> bool:
    print(f"\n=== {acct.label} ===")
    portal = Portal(cfg["base_url"], cfg["verify_ssl"], cfg["delay"])

    portal.login(acct)
    print("Login OK")

    if not portal.feedback_pending():
        print("No feedback pending - dashboard opened directly. Nothing to do.")
        return True

    # Count everything up-front so progress is meaningful.
    all_rows = {t: portal.summary(t).rows() for t in (COURSE, FACULTY)}
    total = sum(len(r) for r in all_rows.values())
    done = sum(1 for r in all_rows.values() for x in r if x.status.lower() == "completed")
    for t, rows in all_rows.items():
        pend = sum(1 for x in rows if x.available)
        print(f"{TYPE_LABEL[t]} feedback: {len(rows)} total, {len(rows) - pend} done, {pend} pending")

    progress = Progress(total, done)
    failed: list[str] = []
    for t in (COURSE, FACULTY):
        process_type(portal, t, progress, cfg["rating"], cfg["comment"], failed)
    progress.show("finished", final=True)

    still_pending = portal.feedback_pending()
    if failed:
        print("Could not submit:\n  - " + "\n  - ".join(failed))
    if still_pending:
        print("WARNING: dashboard still redirects to feedback - something is incomplete.")
    else:
        print(f"All feedback completed in {progress.elapsed:.1f}s. Dashboard is accessible.")
    return not failed and not still_pending


def load_accounts() -> list[Account]:
    """Supports ROLL_NO=... plus optional extra students with any prefix, e.g. S2_ROLL_NO=..."""
    accounts = []
    for key in sorted(os.environ):
        m = re.fullmatch(r"(.*)ROLL_NO", key)
        if not m:
            continue
        p = m.group(1)
        try:
            accounts.append(Account(p, os.environ[f"{p}SESSION"], os.environ[f"{p}PROGRAM"],
                                    os.environ[key], os.environ[f"{p}PASSWORD"]))
        except KeyError as exc:
            sys.exit(f"Missing {exc.args[0]} in .env (needed for {key})")
    if not accounts:
        sys.exit("No credentials found. Copy .env.example to .env and fill it in.")
    return accounts


def main() -> int:
    load_dotenv()
    cfg = {
        "base_url": os.getenv("PORTAL_BASE_URL", "https://cms.must.edu.pk:8082"),
        "verify_ssl": os.getenv("VERIFY_SSL", "false").strip().lower() in ("1", "true", "yes"),
        "delay": float(os.getenv("REQUEST_DELAY", "0")),
        "rating": os.getenv("FEEDBACK_RATING", "rdExcellent"),
        "comment": os.getenv("FEEDBACK_COMMENT", "Feedback Given By Bot."),
    }
    ok = True
    for acct in load_accounts():
        try:
            ok &= run_account(acct, cfg)
        except Exception as exc:                          # keep going with other accounts
            print(f"ERROR for {acct.label}: {exc}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

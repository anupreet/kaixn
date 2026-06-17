"""Server-side generation jobs.

A playbook is generated in a background thread, independent of any client
connection — so closing the browser (or a dropped SSE stream) never aborts a
~6-9 min generation. Clients subscribe to a job's event stream (full replay +
live tail) and can reconnect freely; the job keeps running and persisting.

One job per repo: a second request for a repo already generating returns the
in-flight job rather than starting a racing run (concurrent runs for the same
repo would otherwise corrupt persistence — run B's create_playbook deletes run
A's row out from under it, FK-violating A's doc writes).
"""

from __future__ import annotations

import json
import threading


class Job:
    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.events: list[dict] = []          # full, replayable log (no markdown)
        self.status = "running"               # running | done | error
        self.cond = threading.Condition()

    def _emit(self, ev: dict) -> None:
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    def _finish(self, status: str) -> None:
        with self.cond:
            self.status = status
            self.cond.notify_all()


class JobManager:
    """Owns one background generation thread per repo and lets clients subscribe."""

    def __init__(self, store_factory, generate) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._store_factory = store_factory   # () -> a playbook store (own conn)
        self._generate = generate             # (repo_url, *, llm) -> event iterator

    def start(self, repo_url: str, llm) -> Job:
        with self._lock:
            j = self._jobs.get(repo_url)
            if j is not None and j.status == "running":
                return j                       # dedup: reuse the in-flight job
            j = Job(repo_url)
            self._jobs[repo_url] = j
        threading.Thread(target=self._run, args=(j, repo_url, llm),
                         daemon=True).start()
        return j

    def get(self, repo: str) -> Job | None:
        with self._lock:
            return self._jobs.get(repo)

    def running_repos(self) -> list[str]:
        with self._lock:
            return [r for r, j in self._jobs.items() if j.status == "running"]

    def _run(self, job: Job, repo_url: str, llm) -> None:
        """The worker: generate → persist each piece → log the (markdown-free)
        event for subscribers. Persistence lives here so it happens exactly once,
        regardless of how many clients are (or aren't) watching."""
        store = self._store_factory()
        pid = None
        principles: list = []
        try:
            for ev in self._generate(repo_url, llm=llm):
                e = ev.get("event")
                if e == "meta":
                    job.repo = ev.get("repo", repo_url)
                    pid = store.create_playbook(job.repo, llm=bool(ev.get("llm")))
                elif e == "conventions" and pid is not None:
                    principles = list(ev.get("items", []))
                    store.update_playbook(pid, principles=principles)
                elif e == "principle" and pid is not None:
                    principles.append(ev["item"])
                    store.update_playbook(pid, principles=principles)
                elif e == "domain" and pid is not None:
                    d = ev.get("domain", {})
                    store.update_playbook(pid, mermaid=d.get("mermaid"),
                                          entities=d.get("entities", []))
                elif e == "doc" and pid is not None:
                    store.save_doc(pid, repo=job.repo, kind=ev["kind"],
                                   slug=ev["slug"], title=ev["title"],
                                   summary=ev.get("summary", ""),
                                   markdown=ev["markdown"],
                                   principles=ev.get("principles", []),
                                   grp=ev.get("grp", ""), seq=ev.get("seq", 0))
                    ev = {k: v for k, v in ev.items() if k != "markdown"}
                job._emit(ev)
            job._finish("done")
        except Exception as ex:  # noqa: BLE001 — surface to subscribers, end job
            job._emit({"event": "error", "detail": str(ex)[:300]})
            job._finish("error")

    def subscribe(self, job: Job, *, heartbeat: float = 15.0):
        """Yield SSE frames: replay everything logged so far, then tail live until
        the job is terminal. Safe to (re)subscribe at any time — a reconnecting
        client gets the full log again and rebuilds state."""
        i = 0
        while True:
            with job.cond:
                while i >= len(job.events) and job.status == "running":
                    if not job.cond.wait(timeout=heartbeat):
                        break                  # timed out → emit a keepalive
                new = job.events[i:]
                i += len(new)
                running = job.status == "running"
            for ev in new:
                yield f"data: {json.dumps(ev)}\n\n"
            if not new:
                if not running:
                    return
                yield ": keepalive\n\n"

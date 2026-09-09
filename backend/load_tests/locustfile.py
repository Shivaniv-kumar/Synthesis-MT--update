"""Locust load test for the AI Meeting Action Tracker API.

Simulates 50 concurrent users spread across 10 tenant sessions performing a
realistic mix of read-heavy and write operations.

Task weights
------------
list_items    (50) — most common operation: browsing the item backlog
update_item   (20) — status updates throughout the workday
create_meeting (10) — submit a new meeting transcript
trigger_extract (10) — kick off AI extraction on a previously created meeting
get_dashboard  (10) — summary metrics on the dashboard

Running the load test
---------------------
From the backend directory (with the app running on localhost:8000):

    locust -f load_tests/locustfile.py \
           --host=http://localhost:8000 \
           --users=50 \
           --spawn-rate=5

Or headless (CI / CD):

    locust -f load_tests/locustfile.py \
           --host=http://localhost:8000 \
           --users=50 \
           --spawn-rate=5 \
           --headless \
           --run-time=2m \
           --csv=load_test_results

Environment variables
---------------------
LOAD_TEST_EMAIL     — username for test login (default: loadtest@example.com)
LOAD_TEST_PASSWORD  — password for test login (default: loadtest123)
"""

from __future__ import annotations

import logging
import os
import random
import time
import uuid
from typing import Optional

from locust import HttpUser, between, events, task
from locust.clients import HttpSession

logger = logging.getLogger("locust.load_test")

# ---------------------------------------------------------------------------
# Test credentials — set via environment in real deployments
# ---------------------------------------------------------------------------

_TEST_EMAIL = os.getenv("LOAD_TEST_EMAIL", "loadtest@example.com")
_TEST_PASSWORD = os.getenv("LOAD_TEST_PASSWORD", "loadtest123")

# ---------------------------------------------------------------------------
# Realistic status values for item updates
# ---------------------------------------------------------------------------

_ITEM_STATUSES = ["Open", "In progress", "Done"]
_PRIORITIES = ["High", "Medium", "Low"]
_SOURCE_TYPES = ["paste"]

# ---------------------------------------------------------------------------
# Sample transcript text (keeps POST bodies small but realistic)
# ---------------------------------------------------------------------------

_SAMPLE_TRANSCRIPTS = [
    (
        "Alice: We need to finish the design review by Friday.\n"
        "Bob: I'll prepare the presentation deck by Thursday.\n"
        "Alice: Great. Who's handling the client follow-up?\n"
        "Bob: I can do that by end of week."
    ),
    (
        "Carol: The API integration tests are failing on CI.\n"
        "Dave: I'll fix the auth headers today.\n"
        "Carol: Can you also update the documentation?\n"
        "Dave: Sure, I'll do that by Wednesday."
    ),
    (
        "Eve: The budget report needs to go out by the 15th.\n"
        "Frank: I'll prepare the numbers by the 14th.\n"
        "Eve: Perfect. Frank, can you also schedule the team retrospective?\n"
        "Frank: Yes, I'll send calendar invites tomorrow."
    ),
]


# ---------------------------------------------------------------------------
# Custom Locust event hooks — track extraction latency separately
# ---------------------------------------------------------------------------


@events.init.add_listener
def on_locust_init(environment, **kwargs) -> None:
    """Attach a custom stat tracker for extraction latency."""
    environment._extraction_latencies: list[float] = []
    logger.info("load_test.init — extraction latency tracking enabled")


@events.quitting.add_listener
def on_quitting(environment, **kwargs) -> None:
    """Log extraction latency percentiles before Locust exits."""
    latencies = getattr(environment, "_extraction_latencies", [])
    if latencies:
        latencies_sorted = sorted(latencies)
        n = len(latencies_sorted)
        p50 = latencies_sorted[n // 2]
        p95 = latencies_sorted[int(n * 0.95)]
        p99 = latencies_sorted[int(n * 0.99)]
        logger.info(
            "load_test.extraction_latency_summary",
            samples=n,
            p50_ms=round(p50 * 1000),
            p95_ms=round(p95 * 1000),
            p99_ms=round(p99 * 1000),
        )


# ---------------------------------------------------------------------------
# MeetingActionTrackerUser
# ---------------------------------------------------------------------------


class MeetingActionTrackerUser(HttpUser):
    """Simulates a realistic user session against the Meeting Action Tracker API.

    The ``wait_time`` of between(1, 3) seconds models a user who reads a page,
    thinks, then takes the next action — a realistic think-time for a web app.

    The on_start hook logs the user in and caches the bearer token and a pool
    of meeting IDs so subsequent tasks can reference previously created data.
    """

    wait_time = between(1, 3)

    # Per-user state populated in on_start
    _token: Optional[str] = None
    _meeting_ids: list[str]
    _item_ids: list[str]

    def on_start(self) -> None:
        """Authenticate and initialise per-user state."""
        self._meeting_ids = []
        self._item_ids = []
        self._token = None

        self._login()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _login(self) -> None:
        """POST /api/auth/login and cache the bearer token."""
        response = self.client.post(
            "/api/auth/login",
            data={"username": _TEST_EMAIL, "password": _TEST_PASSWORD},
            name="/api/auth/login",
            catch_response=True,
        )
        if response.status_code == 200:
            body = response.json()
            self._token = body.get("access_token")
            response.success()
            logger.debug("load_test.login_ok", user=_TEST_EMAIL)
        else:
            # Mark as failure so Locust counts it in error stats
            response.failure(f"Login failed: {response.status_code} — {response.text[:200]}")
            logger.warning(
                "load_test.login_failed",
                status=response.status_code,
                body=response.text[:200],
            )

    @property
    def _auth_headers(self) -> dict[str, str]:
        """Return the Authorization header dict, re-authenticating if needed."""
        if not self._token:
            self._login()
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    @task(10)
    def create_meeting(self) -> None:
        """POST /api/meetings — submit a new meeting with a sample transcript."""
        transcript = random.choice(_SAMPLE_TRANSCRIPTS)
        payload = {
            "title": f"Load Test Meeting {uuid.uuid4().hex[:8]}",
            "source_type": "paste",
            "transcript_ref": transcript,
            "attendees": ["Alice", "Bob", "Carol"],
        }
        with self.client.post(
            "/api/meetings",
            json=payload,
            headers=self._auth_headers,
            name="/api/meetings [POST]",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                body = response.json()
                meeting_id = body.get("id")
                if meeting_id:
                    self._meeting_ids.append(meeting_id)
                    # Keep the list bounded to avoid memory growth
                    if len(self._meeting_ids) > 20:
                        self._meeting_ids = self._meeting_ids[-20:]
                response.success()
            elif response.status_code == 401:
                self._token = None  # Force re-login on next request
                response.failure("Unauthorized — will re-authenticate")
            else:
                response.failure(f"Unexpected status {response.status_code}")

    @task(10)
    def trigger_extract(self) -> None:
        """POST /api/meetings/{id}/extract — trigger AI extraction on a known meeting."""
        if not self._meeting_ids:
            # No meetings yet — create one first
            self.create_meeting()
            return

        meeting_id = random.choice(self._meeting_ids)
        start = time.perf_counter()

        with self.client.post(
            f"/api/meetings/{meeting_id}/extract",
            json={},
            headers=self._auth_headers,
            name="/api/meetings/{id}/extract [POST]",
            catch_response=True,
        ) as response:
            elapsed = time.perf_counter() - start

            if response.status_code in (200, 202):
                # Record extraction latency for separate percentile reporting
                env = self.environment
                if hasattr(env, "_extraction_latencies"):
                    env._extraction_latencies.append(elapsed)

                # Collect item IDs if extraction returned them synchronously
                body = response.json()
                if isinstance(body, dict) and "items" in body:
                    for item in body["items"]:
                        if "id" in item:
                            self._item_ids.append(item["id"])
                    if len(self._item_ids) > 50:
                        self._item_ids = self._item_ids[-50:]

                response.success()
            elif response.status_code == 401:
                self._token = None
                response.failure("Unauthorized")
            elif response.status_code == 404:
                # Meeting was cleaned up — remove from our list
                self._meeting_ids = [m for m in self._meeting_ids if m != meeting_id]
                response.success()  # Not a load-test error
            else:
                response.failure(f"Unexpected status {response.status_code}")

    @task(50)
    def list_items(self) -> None:
        """GET /api/items — browse action items with a random status filter."""
        status_filter = random.choice([None, "Open", "In progress", "Done"])
        params: dict[str, str | int] = {"page": 1, "size": 25}
        if status_filter:
            params["status"] = status_filter

        with self.client.get(
            "/api/items",
            params=params,
            headers=self._auth_headers,
            name="/api/items [GET]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                body = response.json()
                # Cache some item IDs for update_item tasks
                items = body.get("items", body if isinstance(body, list) else [])
                for item in items[:5]:
                    if isinstance(item, dict) and "id" in item:
                        self._item_ids.append(item["id"])
                if len(self._item_ids) > 100:
                    self._item_ids = self._item_ids[-100:]
                response.success()
            elif response.status_code == 401:
                self._token = None
                response.failure("Unauthorized")
            else:
                response.failure(f"Unexpected status {response.status_code}")

    @task(20)
    def update_item(self) -> None:
        """PATCH /api/items/{id} — update an item's status at random."""
        if not self._item_ids:
            # Fall back to listing items first to populate the pool
            self.list_items()
            return

        item_id = random.choice(self._item_ids)
        new_status = random.choice(_ITEM_STATUSES)
        payload = {"status": new_status}

        with self.client.patch(
            f"/api/items/{item_id}",
            json=payload,
            headers=self._auth_headers,
            name="/api/items/{id} [PATCH]",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 204):
                response.success()
            elif response.status_code == 401:
                self._token = None
                response.failure("Unauthorized")
            elif response.status_code == 404:
                # Item was deleted or belongs to another tenant — drop it
                self._item_ids = [i for i in self._item_ids if i != item_id]
                response.success()  # Not a load-test error; stale reference
            else:
                response.failure(f"Unexpected status {response.status_code}")

    @task(10)
    def get_dashboard(self) -> None:
        """GET /api/dashboard/summary — load the summary metrics."""
        with self.client.get(
            "/api/dashboard/summary",
            headers=self._auth_headers,
            name="/api/dashboard/summary [GET]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 401:
                self._token = None
                response.failure("Unauthorized")
            else:
                response.failure(f"Unexpected status {response.status_code}")

    # ------------------------------------------------------------------
    # Error recovery
    # ------------------------------------------------------------------

    def on_stop(self) -> None:
        """Clean up any state on user stop (no-op for stateless HTTP)."""
        logger.debug("load_test.user_stopped", meeting_ids=len(self._meeting_ids))

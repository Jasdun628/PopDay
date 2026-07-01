import os
import tempfile
import unittest


class PublicCandidatesRouteTests(unittest.TestCase):
    def test_matched_phrase_display_is_quoted_and_capitalised(self):
        from flask_app import _matched_phrase_display

        self.assertEqual(_matched_phrase_display("investor day"), '"Investor day"')
        self.assertEqual(_matched_phrase_display("  analyst day  "), '"Analyst day"')
        self.assertEqual(_matched_phrase_display(""), "")

    def test_known_qualifying_company_website_lookup(self):
        from flask_app import _company_website

        self.assertEqual(_company_website("HARMONIC INC."), "https://www.harmonicinc.com/")
        self.assertEqual(_company_website("Unknown Co"), "")
        self.assertEqual(
            _company_website("  example co  ", {"Example Co": "https://example.com/"}),
            "https://example.com/",
        )

    def test_front_door_opens_investor_days_view(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            os.environ["POPDAY_DB_PATH"] = db_file.name
            os.environ["POPDAY_ADMIN_PASSWORD"] = "test-password"
            from flask_app import app

            client = app.test_client()
            response = client.get("/")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn("Running list of qualifying Investor Day announcements", html)
            self.assertLess(html.find("Investor Days"), html.find("Research / Hype"))
            self.assertLess(html.find("Research / Hype"), html.find("Scan Log"))
            self.assertLess(html.find("Scan Log"), html.find("Schedule"))
            self.assertLess(html.find("Schedule"), html.find("System Health"))
            self.assertLess(html.find("System Health"), html.find("Help"))
            self.assertNotIn(">Filings<", html)

    def test_qualifying_company_name_links_to_main_website(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            os.environ["POPDAY_DB_PATH"] = db_file.name
            os.environ["POPDAY_ADMIN_PASSWORD"] = "test-password"
            from popday.db import Database
            from flask_app import app

            db = Database(db_file.name)
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (accession_number, company_name, cik, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "harmonic-route",
                        "HARMONIC INC.",
                        "0000851310",
                        "8-K",
                        "20260617",
                        "https://www.sec.gov/harmonic-route.htm",
                        "Investor Day",
                        "2026-09-15",
                        "investor day",
                        "press_release",
                        "Harmonic will host an Investor Day.",
                        "alert_candidate",
                        None,
                        "2026-06-17T01:00:00+00:00",
                    ),
                )
                db.conn.commit()
            finally:
                db.close()

            client = app.test_client()
            response = client.get("/")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn('href="https://www.harmonicinc.com/"', html)
            self.assertIn(">HARMONIC INC.</a>", html)

    def test_investor_days_table_places_evidence_between_company_and_event_date(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            os.environ["POPDAY_DB_PATH"] = db_file.name
            os.environ["POPDAY_ADMIN_PASSWORD"] = "test-password"
            from popday.db import Database
            from flask_app import app

            db = Database(db_file.name)
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (accession_number, company_name, cik, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, evidence_url, evidence_label, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "column-order-route",
                        "Column Order Co",
                        "0000000002",
                        "8-K",
                        "20260617",
                        "https://www.sec.gov/column-order-route.htm",
                        "Investor Day",
                        "2026-09-15",
                        "investor day",
                        "press_release",
                        "Column Order Co will host an Investor Day.",
                        "alert_candidate",
                        None,
                        "https://www.sec.gov/exhibit-991.htm",
                        "Exhibit 99.1",
                        "2026-06-17T01:00:00+00:00",
                    ),
                )
                db.conn.commit()
            finally:
                db.close()

            client = app.test_client()
            response = client.get("/?tab=announcements")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertNotIn("<th>Email</th>", html)
            self.assertLess(html.find("<th>Company</th>"), html.find("<th>Evidence</th>"))
            self.assertLess(html.find("<th>Evidence</th>"), html.find("Event Date"))
            self.assertLess(html.find("Exhibit 99.1"), html.find("15th September 2026"))
            self.assertLess(html.find("15th September 2026"), html.find('<td class="secondary-cell">EDGAR</td>'))

    def test_investor_days_split_upcoming_above_legacy(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            os.environ["POPDAY_DB_PATH"] = db_file.name
            os.environ["POPDAY_ADMIN_PASSWORD"] = "test-password"
            from popday.db import Database
            from flask_app import app

            db = Database(db_file.name)
            try:
                db.conn.executemany(
                    """
                    INSERT INTO detections
                    (accession_number, company_name, cik, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "legacy-route",
                            "Legacy Co",
                            "0000000003",
                            "8-K",
                            "20200102",
                            "https://www.sec.gov/legacy-route.htm",
                            "Investor Day",
                            "2020-01-15",
                            "investor day",
                            "press_release",
                            "Legacy Co hosted an Investor Day.",
                            "alert_candidate",
                            None,
                            "2020-01-02T01:00:00+00:00",
                        ),
                        (
                            "upcoming-route",
                            "Upcoming Co",
                            "0000000004",
                            "8-K",
                            "20990102",
                            "https://www.sec.gov/upcoming-route.htm",
                            "Investor Day",
                            "2099-01-15",
                            "investor day",
                            "press_release",
                            "Upcoming Co will host an Investor Day.",
                            "alert_candidate",
                            None,
                            "2099-01-02T01:00:00+00:00",
                        ),
                    ],
                )
                db.conn.commit()
            finally:
                db.close()

            client = app.test_client()
            response = client.get("/?tab=announcements")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertLess(html.find("Upcoming"), html.find("Upcoming Co"))
            self.assertLess(html.find("Upcoming Co"), html.find("Legacy"))
            self.assertLess(html.find("Legacy"), html.find("Legacy Co"))

    def test_public_research_hype_tab_renders(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            os.environ["POPDAY_DB_PATH"] = db_file.name
            os.environ["POPDAY_ADMIN_PASSWORD"] = "test-password"
            from popday.db import Database
            from flask_app import app

            db = Database(db_file.name)
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (accession_number, company_name, cik, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "research-route",
                        "Research Route Co",
                        "0000000001",
                        "8-K",
                        "20260617",
                        "https://www.sec.gov/research-route.htm",
                        "Investor Day",
                        "2026-09-15",
                        "investor day",
                        "press_release",
                        "Research Route Co will host an Investor Day.",
                        "alert_candidate",
                        None,
                        "2026-06-17T01:00:00+00:00",
                    ),
                )
                db.conn.commit()
            finally:
                db.close()

            client = app.test_client()
            response = client.get("/?tab=research")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn("Research / Hype", html)
            self.assertIn("Raw Hype Count", html)
            self.assertIn("Investor Comms Count", html)
            self.assertNotIn("/admin/login", response.request.path)

    def test_public_help_matches_current_tabs(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            os.environ["POPDAY_DB_PATH"] = db_file.name
            os.environ["POPDAY_ADMIN_PASSWORD"] = "test-password"
            from flask_app import app

            client = app.test_client()
            response = client.get("/?tab=help")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            for label in [
                "Investor Days",
                "Research / Hype",
                "Scan Log",
                "Schedule",
                "System Health",
                "Help",
            ]:
                self.assertIn(label, html)
            self.assertNotIn("Processed Filings", html)
            self.assertNotIn("Include Rules", html)
            self.assertNotIn("No Commentary", html)

    def test_admin_candidates_is_public_read_only_view(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            os.environ["POPDAY_DB_PATH"] = db_file.name
            os.environ["POPDAY_ADMIN_PASSWORD"] = "test-password"
            from flask_app import app

            client = app.test_client()
            response = client.get("/admin/candidates")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn("Scan Log", html)
            self.assertNotIn("/admin/login", response.request.path)
            self.assertNotIn("Read text", html)


if __name__ == "__main__":
    unittest.main()

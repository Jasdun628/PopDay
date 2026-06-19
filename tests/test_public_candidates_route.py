import os
import tempfile
import unittest


class PublicCandidatesRouteTests(unittest.TestCase):
    def test_admin_candidates_is_public_read_only_view(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            os.environ["POPDAY_DB_PATH"] = db_file.name
            os.environ["POPDAY_ADMIN_PASSWORD"] = "test-password"
            from flask_app import app

            client = app.test_client()
            response = client.get("/admin/candidates")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn("Candidate Matches", html)
            self.assertNotIn("/admin/login", response.request.path)
            self.assertNotIn("Read text", html)


if __name__ == "__main__":
    unittest.main()

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.mocks import OWNER, TOKEN, Servers  # noqa: E402


class ProcessTests(unittest.TestCase):
    def test_missing_token_exits_with_hint(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM", "GROQ", "OWNER"))}
        env["LOG_PATH"] = str(Path(tempfile.mkdtemp()) / "x.log")
        env["TELEGRAM_TOKEN"] = ""
        p = subprocess.run([sys.executable, "bot.py"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 1)
        self.assertIn("TELEGRAM_TOKEN", p.stdout)

    def test_real_process_end_to_end(self):
        srv = Servers()
        srv.state.long_poll = True
        srv.state.llm_script = lambda t: [{"type": "expense", "amount": 450, "category": "Одежда",
                                           "description": "футболка"}]
        tmp = Path(tempfile.mkdtemp())
        env = {**os.environ, "TELEGRAM_TOKEN": TOKEN, "OWNER_ID": str(OWNER), "GROQ_API_KEY": "k",
               "TG_BASE": srv.url, "GROQ_BASE": srv.url + "/openai/v1", "RATES_URL": srv.url + "/rates.js",
               "RATES_URL_2": srv.url + "/rates.xml", "DB_PATH": str(tmp / "life.db"),
               "LOG_PATH": str(tmp / "bot.log"), "BOT_TZ": "Asia/Omsk"}
        p = subprocess.Popen([sys.executable, "bot.py"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        try:
            def ask(text, n_before):
                srv.state.uid += 1
                srv.state.updates.append({"update_id": srv.state.uid, "message": {
                    "message_id": 1, "from": {"id": OWNER}, "chat": {"id": OWNER, "type": "private"}, "text": text}})
                deadline = time.time() + 15
                while time.time() < deadline and len(srv.state.sent) <= n_before:
                    time.sleep(0.1)
                return srv.state.sent[n_before:]
            out = ask("300 шаурма", 0)
            self.assertTrue(out, "бот не ответил")
            self.assertIn("Еда", out[0]["text"])
            out = ask("купил футболку ну такую", len(srv.state.sent))
            self.assertTrue(out and "Одежда" in out[0]["text"], out)
            out = ask("баланс", len(srv.state.sent))
            self.assertIn("Свободно", out[0]["text"])
        finally:
            p.terminate()
            try:
                output = p.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired:
                p.kill()
                output = p.communicate()[0]
            srv.close()
        self.assertNotIn("Traceback", output)
        self.assertIn("бот запущен", output)
        import sqlite3
        con = sqlite3.connect(tmp / "life.db")
        self.assertEqual(con.execute("select count(*) from tx").fetchone()[0], 2)
        self.assertIsNotNone(con.execute("select value from settings where key='rates_ok'").fetchone())
        con.close()
        self.assertTrue((tmp / "bot.log").read_text(encoding="utf-8"))


class EnvFileTests(unittest.TestCase):
    def test_env_file_parsing(self):
        import config
        old = config.BASE
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".env").write_text('# comment\nFOO_T1="bar baz"\nFOO_T2=\'q\'\n\nFOO_T3 = 5\nBROKEN\n', encoding="utf-8")
        os.environ.pop("FOO_T1", None)
        os.environ["FOO_T3"] = "keep"            # переменные окружения важнее файла
        config.BASE = tmp
        try:
            config._load_env()
        finally:
            config.BASE = old
        self.assertEqual(os.environ["FOO_T1"], "bar baz")
        self.assertEqual(os.environ["FOO_T2"], "q")
        self.assertEqual(os.environ["FOO_T3"], "keep")


if __name__ == "__main__":
    unittest.main()

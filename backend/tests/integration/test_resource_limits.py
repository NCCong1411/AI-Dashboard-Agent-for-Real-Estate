"""Bound real connection failures before SQL statement_timeout can apply."""

import socket
from time import perf_counter

from sqlalchemy.engine import make_url

from app.analytics.executor import QueryExecutor
from test_executor import executor, plan


def test_reader_connection_failure_is_bounded_and_sanitized(executor, caplog):
    # Reserve a loopback TCP port without listening so no unrelated server can
    # receive credentials during the deliberately failing connection attempt.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        url = make_url(executor.settings.reader_ca_database_url.get_secret_value()).set(port=port)
        values = executor.settings.model_dump(mode="python")
        values.update(reader_ca_database_url=url.render_as_string(hide_password=False), database_connect_timeout_seconds=2)
        settings = type(executor.settings)(_env_file=None, **values)
        broken = QueryExecutor(settings)
        try:
            started = perf_counter()
            result = broken.execute(plan(), "CA")
            elapsed = perf_counter() - started
            assert elapsed < 6, f"connection failure was not bounded: {elapsed:.2f}s"
            assert result.status == "error" and result.error_code == "database_error"
            assert result.rows == []
            assert url.password not in result.model_dump_json() + caplog.text
            assert broken._engine("CA").pool.checkedout() == 0
        finally:
            broken.close()

from unittest.mock import MagicMock, patch

from app.tests_pre_start import init, logger


def test_init_successful_connection() -> None:
    engine_mock = MagicMock()

    session_mock = MagicMock()
    # init() uses `with Session(engine) as session:`, so the object under test
    # is what __enter__ returns, not the constructor's return value.
    session_mock.__enter__.return_value = session_mock

    with (
        # Patched where it is looked up, not where it is defined:
        # app/tests_pre_start.py binds Session into its own namespace at
        # import, so patching sqlalchemy.orm.Session would leave this call site
        # pointing at the real class.
        patch("app.tests_pre_start.Session", return_value=session_mock),
        patch.object(logger, "info"),
        patch.object(logger, "error"),
        patch.object(logger, "warn"),
    ):
        try:
            init(engine_mock)
            connection_successful = True
        except Exception:
            connection_successful = False

        assert (
            connection_successful
        ), "The database connection should be successful and not raise an exception."

        session_mock.execute.assert_called_once()
        # Compared as a string because SQLAlchemy's text() clauses overload
        # __eq__ to build a SQL expression rather than to answer a question, so
        # `arg == text("SELECT 1")` is a truthy BinaryExpression either way.
        (statement,), _ = session_mock.execute.call_args
        assert str(statement) == "SELECT 1"

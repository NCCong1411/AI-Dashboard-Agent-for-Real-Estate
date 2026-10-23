from app.auth.login_limit import LoginLimiter


def test_failed_logins_are_throttled_but_success_clears_failures() -> None:
    limiter = LoginLimiter(max_failures=2)
    assert limiter.begin("Builder")
    limiter.end("Builder", successful=False)
    assert limiter.begin("builder")
    limiter.end("builder", successful=False)
    assert not limiter.begin("Builder")
    limiter.end("builder", successful=True)
    assert limiter.begin("Builder")
    limiter.end("Builder", successful=True)


def test_parallel_hashes_are_bounded_without_blocking_known_good_logins() -> None:
    limiter = LoginLimiter()
    assert limiter.begin("viewer_all")
    assert limiter.begin("viewer_all")
    assert not limiter.begin("viewer_all")
    limiter.end("viewer_all", successful=True)
    assert limiter.begin("viewer_all")
    limiter.end("viewer_all", successful=True)
    limiter.end("viewer_all", successful=True)


def test_global_failed_attempts_stop_username_rotation() -> None:
    limiter = LoginLimiter(global_failures=2)
    for name in ("one", "two"):
        assert limiter.begin(name)
        limiter.end(name, successful=False)
    assert not limiter.begin("three")

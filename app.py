"""Compatibility entrypoint for Codenoa Sales Coach.

Application code lives in ``sales_coach``. Public re-exports preserve scripts and
tests that historically imported symbols from this module.
"""

from sales_coach.server import (  # noqa: F401
    AppHandler,
    ReusableHTTPServer,
    _erp_masters_available,
    _parse_iso_date,
    _sync_sales_range_chunked,
    main,
)


if __name__ == "__main__":
    main()

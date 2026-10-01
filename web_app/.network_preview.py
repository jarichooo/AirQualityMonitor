from datetime import datetime, timedelta, timezone

import app as application


def preview_readings(filters, paginate=True):
    rows = []
    start = datetime(2026, 9, 28, 8, 30, tzinfo=timezone.utc)
    for index in range(100):
        rows.append(
            (
                start - timedelta(minutes=index * 3),
                round(29.4 + index * 0.1, 1),
                round(67.2 - index * 0.2, 1),
                round(7.8 + index * 0.05, 2),
                round(13.6 + index * 0.1, 1),
                round(10.2 + index * 0.1, 1),
                round(27.5 + index * 0.1, 1),
            )
        )

    if filters["q"]:
        query = filters["q"].lower()
        rows = [row for row in rows if query in " ".join(map(str, row)).lower()]

    total = len(rows)
    if paginate:
        offset = (filters["page"] - 1) * filters["per_page"]
        rows = rows[offset : offset + filters["per_page"]]
    return rows, total, None


application.fetch_readings = preview_readings
application.app.config.update(SECRET_KEY="local-network-preview")


@application.app.before_request
def preview_login():
    application.session.update(logged_in=True, username="Admin", role="Admin")


application.app.run(host="0.0.0.0", port=5051, debug=False, use_reloader=False)

"""Run inside the web container: python add_users.py USERNAME [--role viewer]."""
import argparse
from contextlib import closing
from getpass import getpass

from app import get_db_connection
from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description="Create a dashboard account")
    parser.add_argument("username")
    parser.add_argument("--role", choices=("admin", "viewer"), default="admin")
    args = parser.parse_args()
    if not args.username.strip() or len(args.username) > 100:
        parser.error("Username must contain 1 to 100 characters")
    password = getpass("Password: ")
    if not password or password != getpass("Confirm password: "):
        parser.error("Passwords must be nonempty and match")
    with closing(get_db_connection()) as conn:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO dashboard_users (username, password_hash, role) VALUES (%s, %s, %s) "
                    "ON CONFLICT (username) DO NOTHING RETURNING username",
                    (args.username.strip(), generate_password_hash(password), args.role),
                )
                created = cursor.fetchone()
    print("Account created." if created else "Account already exists; left unchanged.")


if __name__ == "__main__":
    main()

"""Give an existing user admin rights.

    python -m scripts.make_admin someone@example.com
"""
import sys

from app.db import get_db


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m scripts.make_admin EMAIL")
    email = sys.argv[1].strip().lower()
    result = get_db().users.update_one({"email": email}, {"$set": {"is_admin": True}})
    if result.matched_count == 0:
        sys.exit(f"No user with email {email}")
    print(f"{email} is now an admin")


if __name__ == "__main__":
    main()

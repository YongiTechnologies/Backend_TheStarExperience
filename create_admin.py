"""Create or reset the password for the username configured as ADMIN_USERNAME."""
from getpass import getpass

from app.core.config import settings
from app.core.security import get_password_hash
from app.database.connection import SessionLocal
from app.models.user import User


def main() -> None:
    password = getpass(f"Password for admin '{settings.ADMIN_USERNAME}': ")
    confirmation = getpass("Confirm password: ")
    if not password:
        raise SystemExit("Password cannot be empty.")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        if user:
            user.hashed_password = get_password_hash(password)
            message = "Admin password reset successfully."
        else:
            db.add(User(
                username=settings.ADMIN_USERNAME,
                hashed_password=get_password_hash(password),
            ))
            message = "Admin account created successfully."
        db.commit()
        print(message)
    finally:
        db.close()


if __name__ == "__main__":
    main()

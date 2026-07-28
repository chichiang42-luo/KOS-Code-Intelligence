from app.shared.db import Session


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_user(self, user: dict) -> None:
        self.session.save(user)

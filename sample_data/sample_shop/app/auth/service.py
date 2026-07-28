from app.shared.base import BaseService
from app.auth.repository import UserRepository


class AdminService(BaseService):
    def __init__(self, repository: UserRepository) -> None:
        self.repository = repository

    def can_manage(self, user: dict) -> bool:
        self.audit("checking admin rights")
        return bool(user.get("is_admin"))

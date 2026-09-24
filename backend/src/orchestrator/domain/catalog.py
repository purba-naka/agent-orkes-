import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orchestrator.db.models import Credential, Model, ModelRevision
from orchestrator.domain.schemas import (
    CredentialCreate,
    CredentialUpdate,
    ModelCreate,
    ModelRevisionCreate,
)
from orchestrator.security.encryption import vault


class CatalogService:
    @staticmethod
    async def create_credential(
        session: AsyncSession, data: CredentialCreate
    ) -> Credential:
        cred_id = uuid.uuid4()
        last_four = data.secret[-4:] if len(data.secret) >= 4 else data.secret
        aad = f"credential:{cred_id}:v1".encode()
        ciphertext, nonce, key_version = vault.encrypt(data.secret, aad=aad)

        cred = Credential(
            id=cred_id,
            name=data.name,
            kind=data.kind,
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=key_version,
            last_four=last_four,
            is_enabled=True,
        )
        session.add(cred)
        await session.commit()
        await session.refresh(cred)
        return cred

    @staticmethod
    async def update_credential(
        session: AsyncSession, cred_id: uuid.UUID, data: CredentialUpdate
    ) -> Credential | None:
        cred = await session.get(Credential, cred_id)
        if not cred:
            return None

        last_four = data.secret[-4:] if len(data.secret) >= 4 else data.secret
        aad = f"credential:{cred.id}:v{cred.key_version}".encode()
        ciphertext, nonce, _ = vault.encrypt(data.secret, aad=aad)

        cred.ciphertext = ciphertext
        cred.nonce = nonce
        cred.last_four = last_four
        await session.commit()
        await session.refresh(cred)
        return cred

    @staticmethod
    async def disable_credential(
        session: AsyncSession, cred_id: uuid.UUID
    ) -> Credential | None:
        cred = await session.get(Credential, cred_id)
        if not cred:
            return None
        cred.is_enabled = False
        await session.commit()
        await session.refresh(cred)
        return cred

    @staticmethod
    async def list_credentials(session: AsyncSession) -> list[Credential]:
        stmt = select(Credential).order_by(Credential.created_at.desc())
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_decrypted_credential(
        session: AsyncSession, cred_id: uuid.UUID
    ) -> str:
        cred = await session.get(Credential, cred_id)
        if not cred or not cred.is_enabled:
            raise ValueError(f"Credential {cred_id} is disabled or does not exist")
        aad = f"credential:{cred.id}:v{cred.key_version}".encode()
        return vault.decrypt(cred.ciphertext, cred.nonce, aad=aad)

    @staticmethod
    async def create_model(
        session: AsyncSession, data: ModelCreate
    ) -> tuple[Model, ModelRevision]:
        model_id = uuid.uuid4()
        revision_id = uuid.uuid4()

        model = Model(id=model_id, name=data.name, active_revision_id=revision_id)
        session.add(model)
        await session.flush()

        revision = ModelRevision(
            id=revision_id,
            model_id=model_id,
            revision_number=1,
            provider=data.revision.provider,
            model_name=data.revision.model_name,
            base_url=data.revision.base_url,
            api_key_id=data.revision.api_key_id,
            parameters=data.revision.parameters,
            routing=data.revision.routing,
            context_window=data.revision.context_window,
            is_enabled=True,
        )
        session.add(revision)
        await session.commit()
        refreshed_model = await CatalogService.get_model(session, model_id)
        if refreshed_model is None:
            await session.refresh(model)
            refreshed_model = model
        return refreshed_model, revision

    @staticmethod
    async def create_model_revision(
        session: AsyncSession, model_id: uuid.UUID, data: ModelRevisionCreate
    ) -> ModelRevision:
        model = await session.get(Model, model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")

        # Find max revision number
        stmt = select(func.max(ModelRevision.revision_number)).where(
            ModelRevision.model_id == model_id
        )
        max_rev = (await session.execute(stmt)).scalar() or 0
        new_rev_number = max_rev + 1

        new_rev_id = uuid.uuid4()
        rev = ModelRevision(
            id=new_rev_id,
            model_id=model_id,
            revision_number=new_rev_number,
            provider=data.provider,
            model_name=data.model_name,
            base_url=data.base_url,
            api_key_id=data.api_key_id,
            parameters=data.parameters,
            routing=data.routing,
            context_window=data.context_window,
            is_enabled=True,
        )
        model.active_revision_id = new_rev_id
        session.add(rev)
        await session.commit()
        await session.refresh(rev)
        return rev

    @staticmethod
    async def list_models(session: AsyncSession) -> list[Model]:
        stmt = (
            select(Model)
            .options(selectinload(Model.revisions))
            .order_by(Model.name.asc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_model(session: AsyncSession, model_id: uuid.UUID) -> Model | None:
        stmt = (
            select(Model)
            .where(Model.id == model_id)
            .options(selectinload(Model.revisions))
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def get_model_revision(
        session: AsyncSession, revision_id: uuid.UUID
    ) -> ModelRevision | None:
        return await session.get(ModelRevision, revision_id)

    @staticmethod
    async def resolve_chat_model(
        session: AsyncSession, revision_id: uuid.UUID
    ) -> BaseChatModel:
        rev = await session.get(ModelRevision, revision_id)
        if not rev or not rev.is_enabled:
            raise ValueError(f"Model revision {revision_id} is disabled or not found")

        api_key: str | None = None
        if rev.api_key_id:
            api_key = await CatalogService.get_decrypted_credential(
                session, rev.api_key_id
            )

        if rev.provider in ("fake", "test"):
            from tests.spikes.test_framework_compat import ScriptedTestChatModel
            from langchain_core.messages import AIMessage

            scripted_list: list[AIMessage] = []
            if "responses" in rev.parameters and isinstance(rev.parameters["responses"], list):
                scripted_list = [AIMessage(content=str(r)) for r in rev.parameters["responses"]]
            elif rev.parameters.get("behavior") != "memory_echo":
                scripted_list = [AIMessage(content="Test response from fake model")]

            behavior = str(rev.parameters.get("behavior", "memory_echo" if not scripted_list else "default"))
            return ScriptedTestChatModel(
                scripted_responses=scripted_list,
                behavior=behavior,
            )

        from langchain_litellm import ChatLiteLLM

        model_ident = (
            f"{rev.provider}/{rev.model_name}"
            if not rev.model_name.startswith(f"{rev.provider}/")
            else rev.model_name
        )

        kwargs: dict[str, Any] = dict(rev.parameters)
        if rev.base_url:
            kwargs["api_base"] = rev.base_url
        if api_key:
            kwargs["api_key"] = api_key

        return ChatLiteLLM(model=model_ident, **kwargs)
